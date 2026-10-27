"""Pure authorization tests plus explicitly opted-in Postgres workflow checks."""
from copy import deepcopy
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
import pytest

from app import spec as spec_module
from app import records, workflow
from app.migrate import apply, plan
from app.spec import validate
from tests.test_migrate import pg  # Reuse the isolated, opt-in real-Postgres fixture.

ROOT = Path(spec_module.__file__).resolve().parent.parent
A_EDGES = ([(source, target, "admin") for target in ("approved", "denied")
            for source in ("pending", "approved", "denied", "needs_info")]
           + [(source, "needs_info", "admin") for source in ("pending", "approved", "denied", "needs_info")]
           + [("needs_info", "pending", "requester")]
           + [(source, "pending", "admin") for source in ("needs_info", "approved", "denied", "pending")])


@pytest.fixture
def example():
    return validate((Path(__file__).resolve().parent.parent / "examples/access_requests/spec.json").read_text())


@pytest.fixture
def equipment():
    return validate((ROOT / "examples/equipment_checkout/spec.json").read_text())


def actor(role="requester", *, identifier=None, **kwargs):
    return SimpleNamespace(id=identifier or uuid.uuid4(), roles=[role], status="approved", **kwargs)


def fails(code, fn, *args, **kwargs):
    with pytest.raises(HTTPException) as error:
        fn(*args, **kwargs)
    assert error.value.status_code == code


@pytest.mark.parametrize("state,target,role", A_EDGES)
def test_every_example_a_edge(example, state, target, role):
    user = actor(role)
    edge = workflow.select_transition(example, "request", state, target, user, user.id)
    assert edge.to == target


def test_every_declared_example_a_edge_including_legacy_resets(example):
    for edge in example.workflow.transitions:
        for source in edge.from_:
            for role in edge.roles:
                user = actor(role)
                selected = workflow.select_transition(example, "request", source, edge.to, user, user.id)
                assert selected == edge


def test_roles_are_exact_and_admin_does_not_inherit_requester(example):
    admin = actor("admin", is_admin=True)
    assert workflow.roles_for(admin, example) == {"admin"}
    fails(403, workflow.require_role, admin, "requester", spec=example)
    fails(403, workflow.select_transition, example, "request", "pending", "approved", actor(), uuid.uuid4())
    fails(403, workflow.select_transition, example, "request", "pending", "approved", actor("reviewer"), uuid.uuid4())


def test_owner_transition_requires_actual_owner(example):
    user = actor()
    fails(403, workflow.select_transition, example, "request", "needs_info", "pending", user, uuid.uuid4())
    # A mixed-role account still cannot use an owner-only edge by admin bypass.
    owner_only = deepcopy(example)
    owner_only.workflow.transitions = [edge for edge in example.workflow.transitions if edge.requires]
    user.roles.append("admin")
    fails(403, workflow.select_transition, owner_only, "request", "needs_info", "pending", user, uuid.uuid4())


def test_unreachable_transition_and_unapproved_actor(example):
    user = actor("admin")
    fails(409, workflow.select_transition, example, "request", "approved", "missing_state", user, user.id)
    user.status = "pending"
    fails(403, workflow.require_role, user, "admin", spec=example)
    fails(401, workflow.require_role, None, "admin", spec=example)


def test_legacy_role_compatibility(example, equipment):
    ordinary = SimpleNamespace(id=uuid.uuid4(), is_admin=False, is_reviewer=False)
    assert workflow.roles_for(ordinary, example) == {"requester"}
    assert workflow.roles_for(ordinary, equipment) == {"borrower"}
    assert workflow.roles_for(SimpleNamespace(is_admin=True), example) == {"admin"}
    assert workflow.roles_for(SimpleNamespace(is_reviewer=True), example) == {"reviewer"}
    assert workflow.roles_for(None, example) == set()


def test_collects_marks_future_answer_requires_fresh_nonblank_answer(example):
    entity = records.entity_of(example, "request")
    ask, answer = example.workflow.transitions[2:4]
    assert workflow.transition_values(entity, ask, None) == {}
    for data in (None, {}, {"justification": "  "}):
        fails(422, workflow.transition_values, entity, answer, data)
    assert workflow.transition_values(entity, answer, {"justification": " project work "}) == {"justification": "project work"}
    for data in ({"state": "approved"}, {"owner_id": str(uuid.uuid4())}, {"justification": 3}, {"justification": "a" * 2001}):
        fails(422, workflow.transition_values, entity, answer, data)


def test_declared_transition_fields_use_runtime_validation(example):
    entity = records.entity_of(example, "request")
    edge = next(edge for edge in example.workflow.transitions if edge.requires).model_copy(update={"requires": "reason"})
    fails(422, workflow.transition_values, entity, edge, {"reason": "a"})
    fails(422, workflow.transition_values, entity, edge, {"reason": " a "})
    assert workflow.transition_values(entity, edge, {"reason": "valid"}) == {"reason": "valid"}


@pytest.mark.parametrize("states,expected", [
    ([], "pending"), (["pending"], "pending"), (["needs_info", "pending"], "pending"),
    (["approved", "approved"], "approved"), (["denied"], "denied"),
    (["approved", "denied"], "partial"), (["approved", "pending"], "partial"),
    (["denied", "needs_info"], "partial"),
])
def test_rollup_preserves_source_semantics(example, states, expected):
    assert workflow.rollup(example.workflow, states) == expected


def test_second_domain_graph(equipment):
    user = actor("admin")
    state = equipment.workflow.initial
    for target in ("approved", "checked_out", "returned"):
        assert workflow.select_transition(equipment, "checkout", state, target, user, uuid.uuid4()).to == target
        state = target
    assert workflow.select_transition(equipment, "checkout", "requested", "rejected", user, uuid.uuid4()).to == "rejected"
    fails(403, workflow.select_transition, equipment, "checkout", "requested", "approved", actor("borrower"), uuid.uuid4())


class CountConnection:
    def __init__(self, count):
        self.count, self.calls = count, []

    def execute(self, statement, params):
        self.calls.append((statement if isinstance(statement, str) else statement.as_string(), params))
        return self

    def fetchone(self):
        return {"n": self.count}


def test_daily_limit_locks_before_counting_and_counts_archives(example):
    user = actor()
    conn = CountConnection(9)
    workflow.enforce_daily_limit(conn, example, "request", user)
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert "count(*)" in conn.calls[1][0]
    assert "active" not in conn.calls[1][0]
    assert "UTC" in conn.calls[1][0]
    assert str(user.id) not in conn.calls[1][0]
    assert conn.calls[1][1] == (user.id,)
    fails(429, workflow.enforce_daily_limit, CountConnection(10), example, "request", user)
    other = CountConnection(0)
    workflow.enforce_daily_limit(other, example, "request", actor())
    assert other.calls[0][1] != conn.calls[0][1]


def test_no_daily_limit_no_database_access(equipment):
    conn = CountConnection(100)
    workflow.enforce_daily_limit(conn, equipment, "checkout", actor("borrower"))
    assert conn.calls == []


class LockConnection:
    """A SQL call spy, not a replacement for the opt-in database checks."""
    def __init__(self, *rows):
        self.rows, self.calls, self.transactions = list(rows), [], 0

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield

    def execute(self, statement, params):
        self.calls.append((statement.as_string(), params))
        return self

    def fetchone(self):
        return self.rows.pop(0)


def test_transition_locks_header_before_line_and_routes_field_scopes(example, monkeypatch):
    user = actor()
    header = {"id": uuid.uuid4(), "owner_id": user.id, "reason": "Original"}
    line = {"id": uuid.uuid4(), "record_id": header["id"], "state": "needs_info"}
    conn = LockConnection(header, line)
    writes = []

    def update(conn, table, identifier, values):
        writes.append((table, identifier, values))
        (line if table.endswith("_lines") else header).update(values)
        return header

    monkeypatch.setattr(workflow, "_update", update)
    monkeypatch.setattr(records, "_references", lambda *a, **kw: None)
    monkeypatch.setattr(records, "hydrate", lambda *a: dict(header, lines=[line]))
    result = workflow.transition(conn, example, "request", header["id"], user, "pending",
                                 line_id=line["id"], data={"justification": "Synthetic answer"})
    assert conn.transactions == 1
    assert '"x_request"' in conn.calls[0][0] and "for update" in conn.calls[0][0]
    assert '"x_request_lines"' in conn.calls[1][0] and '"record_id" = %s' in conn.calls[1][0]
    assert writes[0][2] == {"justification": "Synthetic answer", "state": "pending"}
    assert writes[1][2] == {}
    assert result["state"] == "pending"

    # A declared field requested by a line transition remains header-scoped.
    next(edge for edge in example.workflow.transitions if edge.requires).requires = "reason"
    line["state"] = "needs_info"
    conn = LockConnection(header, line)
    writes.clear()
    workflow.transition(conn, example, "request", header["id"], user, "pending",
                        line_id=line["id"], data={"reason": "New reason"})
    assert writes[0][2] == {"state": "pending"}
    assert writes[1][2] == {"reason": "New reason"}


def test_transition_scope_and_missing_records(example, equipment):
    user = actor("admin")
    fails(422, workflow.transition, LockConnection(), example, "request", uuid.uuid4(), user, "approved")
    fails(422, workflow.transition, LockConnection(), equipment, "checkout", uuid.uuid4(), user, "approved", line_id=uuid.uuid4())
    fails(404, workflow.transition, LockConnection(None), example, "request", uuid.uuid4(), user, "approved", line_id=uuid.uuid4())
    header = {"id": uuid.uuid4(), "owner_id": user.id}
    fails(404, workflow.transition, LockConnection(header, None), example, "request", header["id"], user, "approved", line_id=uuid.uuid4())


def test_update_quotes_identifiers_and_binds_values():
    value = "'); drop table users; --"
    conn = LockConnection({"id": uuid.uuid4()})
    identifier = uuid.uuid4()
    workflow._update(conn, "x_request_lines", identifier, {"justification": value, "state": "pending"})
    statement, params = conn.calls[0]
    assert '"x_request_lines"' in statement and '"justification" = %s' in statement
    assert value not in statement
    assert params == (value, "pending", identifier)


def install(pg, spec):
    migration = plan(pg, spec)
    apply(pg, spec, migration.plan_hash)


def insert_user(pg, role):
    user = actor(role)
    pg.execute("insert into users(id,name,email,password_hash,status) values (%s,%s,%s,%s,%s)",
               (user.id, "Test User", str(user.id) + "@example.test", "unused", "approved"))
    return user


def access_record(pg, example):
    install(pg, example)
    admin, requester = insert_user(pg, "admin"), insert_user(pg, "requester")
    topic = records.create(pg, example, records.entity_of(example, "topic"), {"name": "Test Topic"}, admin)
    tool = records.create(pg, example, records.entity_of(example, "tool"),
                          {"name": "Test Tool", "parent_id": str(topic["id"])}, admin)
    record = records.create(pg, example, records.entity_of(example, "request"),
                            {"reason": "Synthetic test", "lines": [str(tool["id"])]}, requester)
    return admin, requester, record


@pytest.mark.parametrize("state,target,role", A_EDGES)
def test_pg_every_example_a_transition(pg, example, state, target, role):
    admin, requester, record = access_record(pg, example)
    line = record["lines"][0]
    if state != "pending":
        workflow.transition(pg, example, "request", record["id"], admin, state, line_id=line["id"])
    result = workflow.transition(pg, example, "request", record["id"], admin if role == "admin" else requester,
                                 target, line_id=line["id"], data={"justification": "For testing"} if role == "requester" else None)
    assert result["lines"][0]["state"] == target
    assert result["state"] == workflow.rollup(example.workflow, [target])


def test_pg_justification_and_owner_guards_preserve_state(pg, example):
    admin, requester, record = access_record(pg, example)
    line_id = record["lines"][0]["id"]
    workflow.transition(pg, example, "request", record["id"], admin, "needs_info", line_id=line_id)
    fails(422, workflow.transition, pg, example, "request", record["id"], requester, "pending", line_id=line_id)
    other = insert_user(pg, "requester")
    fails(403, workflow.transition, pg, example, "request", record["id"], other, "pending", line_id=line_id, data={"justification": "Someone else's record"})
    result = workflow.transition(pg, example, "request", record["id"], requester, "pending", line_id=line_id, data={"justification": "Needed for work"})
    assert result["lines"][0]["justification"] == "Needed for work"
    assert result["lines"][0]["state"] == "pending"


def test_pg_daily_limit_counts_only_current_user_and_day(pg, example):
    admin, requester, record = access_record(pg, example)
    # Count the original plus nine more, then refuse the eleventh. Integration
    # in records.create must call enforce_daily_limit before every insert.
    for _ in range(9):
        records.create(pg, example, records.entity_of(example, "request"),
                       {"reason": "Synthetic test", "lines": [str(record["lines"][0]["ref_id"])]}, requester)
    fails(429, workflow.enforce_daily_limit, pg, example, "request", requester)
    workflow.enforce_daily_limit(pg, example, "request", admin)
    pg.execute("update x_request set created_at = created_at - interval '2 days' where id = %s", (record["id"],))
    workflow.enforce_daily_limit(pg, example, "request", requester)


def test_pg_equipment_flow_end_to_end(pg, equipment):
    install(pg, equipment)
    admin, borrower = insert_user(pg, "admin"), insert_user(pg, "borrower")
    asset = records.create(pg, equipment, records.entity_of(equipment, "asset"),
                           {"tag": "TEST-001", "name": "Synthetic camera", "condition": "good"}, admin)
    entity = records.entity_of(equipment, "checkout")
    record = records.create(pg, equipment, entity, {"asset": str(asset["id"]), "due_date": "2030-01-01"}, borrower)
    fails(403, workflow.transition, pg, equipment, entity, record["id"], borrower, "approved")
    for state in ("approved", "checked_out", "returned"):
        result = workflow.transition(pg, equipment, entity, record["id"], admin, state)
        assert result["state"] == state
    fails(409, workflow.transition, pg, equipment, entity, record["id"], admin, "rejected")
    rejected = records.create(pg, equipment, entity, {"asset": str(asset["id"]), "due_date": "2030-01-01"}, borrower)
    assert workflow.transition(pg, equipment, entity, rejected["id"], admin, "rejected")["state"] == "rejected"
