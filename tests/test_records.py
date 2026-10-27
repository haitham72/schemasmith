"""Runtime validation/trees run offline; the same API matrix covers both specs."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app import records
from app.spec import validate
from tests.test_migrate import pg, install

ROOT = Path(__file__).resolve().parent.parent


def example(name="access_requests"):
    return validate((ROOT / "examples" / name / "spec.json").read_bytes())


def model(fields):
    raw = {"spec_version": 1, "app": {"title": "Demo", "slug": "demo"}, "roles": ["admin"],
           "entities": [{"name": "thing", "kind": "reference", "fields": fields}], "views": []}
    return validate(raw).entities[0]


@pytest.mark.parametrize("kind,value,expected", [
    ("text", " demo ", "demo"), ("int", 3, 3), ("number", 2.5, 2.5), ("bool", False, False),
    ("date", "2026-01-01", date(2026, 1, 1)),
    ("datetime", "2026-01-01T12:00:00Z", datetime.fromisoformat("2026-01-01T12:00:00+00:00")),
    ("domain", "https://api.example.test/v1", "api.example.test"),
    ("email", "demo@example.test", "demo@example.test"), ("url", "https://example.test", "https://example.test"),
    ("list<domain>", "https://api.example.test/v1, api.example.test *.example.test", ["api.example.test", "*.example.test"]),
    ("list<int>", [1, 2], [1, 2]), ("list<bool>", [True, False], [True, False]),
])
def test_registry_closed_types(kind, value, expected):
    entity = model([{"name": "value_field", "type": kind}])
    assert records.values_for(entity, {"value_field": value})["value_field"] == expected


@pytest.mark.parametrize("kind,value", [
    ("int", True), ("int", "3"), ("bool", "false"), ("number", float("inf")), ("number", float("nan")),
    ("date", "yesterday"), ("date", 1234), ("datetime", "2026-01-01T12:00:00"),
    ("domain", 1), ("list<domain>", True), ("email", "invalid"), ("url", "javascript:alert(1)"),
])
def test_registry_rejects_wrong_types(kind, value):
    entity = model([{"name": "value_field", "type": kind}])
    with pytest.raises(HTTPException) as error:
        records.values_for(entity, {"value_field": value})
    assert error.value.status_code == 422


def test_required_unknown_and_patch_semantics():
    entity = model([{"name": "title", "type": "text", "required": True, "min": 2, "max": 8}])
    for data in ({}, {"title": " "}, {"title": "too long text"}, {"title": "valid", "owner_id": str(uuid.uuid4())}):
        with pytest.raises(HTTPException):
            records.values_for(entity, data)
    assert records.values_for(entity, {}, patch=True) == {}
    with pytest.raises(HTTPException):
        records.values_for(entity, {"title": None}, patch=True)


def test_default_lists_are_typed_and_not_shared():
    entity = model([{"name": "dates", "type": "list<date>", "default": ["2026-01-01"]}])
    first = records.values_for(entity, {})
    assert first["dates"] == [date(2026, 1, 1)]
    first["dates"].append(date(2026, 1, 2))
    assert records.values_for(entity, {})["dates"] == [date(2026, 1, 1)]


def test_enum_and_reference_types():
    spec = example("equipment_checkout")
    asset, checkout = spec.entities
    with pytest.raises(HTTPException):
        records.values_for(asset, {"tag": "DEMO-1", "name": "Demo", "condition": "unknown"})
    identifier = uuid.uuid4()
    parsed = records.values_for(checkout, {"asset": str(identifier), "due_date": "2026-12-01"})
    assert parsed["asset"] == identifier
    with pytest.raises(HTTPException):
        records.values_for(checkout, {"asset": "bad", "due_date": "2026-12-01"})


def test_line_selection_limits_apply_after_deduplication():
    spec = example()
    raw = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
    raw["entities"][2]["lines"]["min"] = 2
    request = validate(raw).entities[2]
    identifier = str(uuid.uuid4())
    with pytest.raises(HTTPException):
        records.values_for(request, {"reason": "demo", "lines": [identifier, identifier]})


def test_grouped_reference_requires_parent_but_tree_root_does_not():
    topic, tool, _ = example().entities
    assert records.values_for(topic, {"name": "Demo"})["parent_id"] is None
    with pytest.raises(HTTPException):
        records.values_for(tool, {"name": "Demo"})


def test_tree_cycles_depth_and_descendants():
    root, child, leaf = [uuid.UUID(int=index) for index in (1, 2, 3)]
    rows = {root: {"id": root, "parent_id": None}, child: {"id": child, "parent_id": root},
            leaf: {"id": leaf, "parent_id": child}}
    assert [row["id"] for row in records.tree_path(rows, leaf)] == [root, child, leaf]
    assert records.descendants(rows, child) == {child, leaf}
    with pytest.raises(HTTPException):
        records.tree_path(rows, leaf, max_depth=2)
    rows[root]["parent_id"] = leaf
    with pytest.raises(HTTPException):
        records.tree_path(rows, root)


def test_record_guards_and_report_role():
    spec = example()
    entity = spec.entities[2]
    owner = SimpleNamespace(id=str(uuid.uuid4()), is_admin=False, is_reviewer=False, status="approved")
    other = {"owner_id": uuid.uuid4()}
    with pytest.raises(HTTPException) as error:
        records.guard(entity, owner, row=other, spec=spec)
    assert error.value.status_code == 404
    owner.is_reviewer = True
    records.guard(entity, owner, row=other, spec=spec)
    with pytest.raises(HTTPException):
        records.guard(entity, owner, row=other, write=True, spec=spec)


@pytest.mark.parametrize("name", ["access_requests", "equipment_checkout"])
def test_pg_same_crud_csv_api_matrix(pg, name):
    spec = example(name)
    install(pg, spec)
    actors = []
    for label, admin in (("Admin", True), ("Requester", False)):
        row = pg.execute("insert into users (name,email,password_hash,is_admin,status) values (%s,%s,%s,%s,'approved') returning id",
                         ("Demo " + label, label.lower() + "@example.test", "synthetic-not-a-real-password", admin)).fetchone()
        actors.append(SimpleNamespace(id=str(row["id"]), name="Demo " + label, email=label.lower() + "@example.test",
                                      is_admin=admin, is_reviewer=False, status="approved"))
    selected = {"actor": actors[0]}

    class Pool:
        @contextmanager
        def connection(self):
            yield pg

    app = FastAPI()
    records.mount(app, Pool(), lambda: selected["actor"], lambda: selected["actor"])
    with TestClient(app) as client:
        if name == "access_requests":
            root = client.post("/api/e/topic", json={"name": "Demo topic"})
            assert root.status_code == 201, root.text
            reference, record = "tool", "request"
            body = {"name": "Demo tool", "parent_id": root.json()["id"], "domains": ["demo.example.test"]}
        else:
            reference, record = "asset", "checkout"
            body = {"tag": "DEMO-001", "name": "Demo asset", "condition": "good"}
        created = client.post("/api/e/" + reference, json=body)
        assert created.status_code == 201, created.text
        rid = created.json()["id"]
        assert client.patch(f"/api/e/{reference}/{rid}", json={"name": "Updated demo"}).status_code == 200
        assert client.get(f"/api/e/{reference}/{rid}").json()["name"] == "Updated demo"
        assert client.get(f"/api/e/{reference}/export.csv").status_code == 200
        import_body = dict(body)
        import_body['name'] = 'Imported demo'
        if name == 'equipment_checkout':
            import_body['tag'] = 'DEMO-002'
        import csv
        import io
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(import_body))
        writer.writeheader()
        writer.writerow({key: json.dumps(value) if isinstance(value, list) else value for key,value in import_body.items()})
        dry = client.post(f"/api/e/{reference}/import.csv", json={'csv': buffer.getvalue(), 'dry_run': True})
        assert dry.status_code == 200 and dry.json()['created'] == 1 and not dry.json()['errors'], dry.text
        assert client.get('/api/e/' + reference).json()['total'] == 1
        imported = client.post(f"/api/e/{reference}/import.csv", json={'csv': buffer.getvalue()})
        assert imported.status_code == 200 and imported.json()['created'] == 1, imported.text
        assert client.get('/api/e/' + reference).json()['total'] == 2
        selected["actor"] = actors[1]
        assert client.post("/api/e/" + reference, json=body).status_code == 403
        payload = {"reason": "Synthetic request", "lines": [rid]} if name == "access_requests" else {
            "asset": rid, "due_date": "2026-12-01", "purpose": "Synthetic request"}
        result = client.post("/api/e/" + record, json=payload)
        assert result.status_code == 201, result.text
        record_id = result.json()["id"]
        assert client.get("/api/e/" + record).json()["total"] == 1
        field = "reason" if name == "access_requests" else "purpose"
        assert client.patch(f"/api/e/{record}/{record_id}", json={field: "Updated purpose"}).status_code == 200
        assert client.post(f"/api/e/{record}/{record_id}/comments", json={"body": "Synthetic comment"}).status_code == 201
        assert len(client.get(f"/api/e/{record}/{record_id}/comments").json()) == 1
        assert client.delete(f"/api/e/{record}/{record_id}").status_code == 200
        assert client.get(f"/api/e/{record}/{record_id}").status_code == 404
        selected['actor'] = actors[0]
        assert client.delete(f'/api/e/{reference}/{rid}').status_code == 200
        assert client.get(f'/api/e/{reference}/{rid}').status_code == 404
