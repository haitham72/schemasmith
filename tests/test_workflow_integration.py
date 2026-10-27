"""Integration-boundary unit checks. SQL is inspected, never executed here."""
import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app import main, records, workflow
from app.spec import validate

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def example():
    return validate((ROOT / 'examples/access_requests/spec.json').read_bytes())


def user(*roles):
    return main.Who(id=str(uuid.uuid4()), name='Demo User', email='demo@example.test',
                    is_admin='admin' in roles, is_reviewer='reviewer' in roles, roles=list(roles))


class ConnectionSpy:
    def __init__(self, rows=()):
        self.rows, self.calls, self.transactions = list(rows), [], 0

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield

    def execute(self, statement, params=()):
        self.calls.append((statement if isinstance(statement, str) else statement.as_string(), params))
        return self

    def fetchone(self):
        return self.rows.pop(0)


class PoolSpy:
    def __init__(self, connection):
        self.conn = connection

    @contextmanager
    def connection(self):
        yield self.conn


def test_auth_projection_retains_explicit_roles_and_legacy_flags():
    row = dict(id=uuid.uuid4(), name='Demo User', email='demo@example.test', status='approved',
               is_admin=False, is_reviewer=True, roles=['requester'])
    actor = main.row_to_who(row)
    assert actor.roles == ['requester', 'reviewer']
    assert actor.is_reviewer and not actor.is_admin
    assert main.user_public(row)['roles'] == ['requester', 'reviewer']
    assert main.me(actor).roles == actor.roles


def test_signup_defaults_follow_current_spec_without_granting_owner_to_admin(example, monkeypatch):
    monkeypatch.setattr(main, 'active_spec', lambda conn: {'spec': example.model_dump(mode='json', by_alias=True)})
    assert main._signup_roles(None, True) == ['admin']
    assert main._signup_roles(None, False) == ['requester']
    monkeypatch.setattr(main, 'active_spec', lambda conn: None)
    assert main._signup_roles(None, False) == []


def test_pre_spec_signup_roles_are_materialized_once_and_explicit_assignments_survive(example, monkeypatch):
    monkeypatch.setattr(main, 'active_spec', lambda conn: {'spec': example.model_dump(mode='json', by_alias=True)})
    row = {'id': uuid.uuid4(), 'roles': [], 'is_admin': False}
    conn = ConnectionSpy([{'roles': ['requester']}])
    main._assign_unset_roles(conn, row)
    assert row['roles'] == ['requester']
    assert 'cardinality(roles) = 0' in conn.calls[0][0]
    main._assign_unset_roles(conn, row)
    assert len(conn.calls) == 1
    row['roles'] = ['previous_owner_role']
    main._assign_unset_roles(conn, row)
    assert row['roles'] == ['previous_owner_role'] and len(conn.calls) == 1


def test_role_assignment_validates_active_spec_and_protects_last_approved_admin(example, monkeypatch):
    actor = user('admin')
    row = {'id': uuid.uuid4(), 'roles': ['admin'], 'is_admin': True, 'status': 'approved'}
    monkeypatch.setattr(main, 'active_spec', lambda conn: {'spec': example.model_dump(mode='json', by_alias=True)})
    conn = ConnectionSpy([row])
    monkeypatch.setattr(main, 'pool', PoolSpy(conn))
    with pytest.raises(HTTPException) as error:
        main.set_user_roles(row['id'], main.UserRoles(roles=['borrower']), actor)
    assert error.value.status_code == 422
    conn = ConnectionSpy([row, {'n': 1}])
    monkeypatch.setattr(main, 'pool', PoolSpy(conn))
    with pytest.raises(HTTPException) as error:
        main.set_user_roles(row['id'], main.UserRoles(roles=['requester']), actor)
    assert error.value.status_code == 400
    conn = ConnectionSpy([row, {'n': 2}, {'id': row['id'], 'roles': ['requester']}])
    monkeypatch.setattr(main, 'pool', PoolSpy(conn))
    assert main.set_user_roles(row['id'], main.UserRoles(roles=['requester']), actor)['roles'] == ['requester']
    assert conn.calls[-1][1] == (['requester'], False, False, row['id'])


@pytest.mark.parametrize('flag,enabled', [('admin', True), ('admin', False), ('reviewer', True), ('reviewer', False)])
def test_flag_toggles_update_only_the_corresponding_role(flag, enabled, monkeypatch):
    actor, identifier = user('admin'), uuid.uuid4()
    conn = ConnectionSpy([{'id': identifier}, {'id': identifier}])
    monkeypatch.setattr(main, 'pool', PoolSpy(conn))
    if flag == 'admin':
        main.set_admin(identifier, main.AdminFlag(is_admin=enabled), actor)
    else:
        main.set_reviewer(identifier, main.ReviewerFlag(is_reviewer=enabled), actor)
    statement, params = next(call for call in conn.calls if call[0].startswith('update users'))
    assert "array_remove(roles, '" + flag + "')" in statement
    assert "roles || array['" + flag + "']" in statement
    assert params == (enabled, enabled, identifier)


def test_guards_accept_roles_and_reject_pending_status():
    admin = user('admin')
    assert main.admin(admin) is admin
    assert workflow.require_role(admin, 'admin', 'reviewer') is admin
    assert workflow.require_role(user('reviewer'), 'admin', 'reviewer').roles == ['reviewer']
    with pytest.raises(HTTPException) as error:
        main.admin(user('requester'))
    assert error.value.status_code == 403
    admin.status = 'pending'
    with pytest.raises(HTTPException) as error:
        main.admin(admin)
    assert error.value.status_code == 403


def test_effective_signup_domain_uses_environment_then_spec_then_open(monkeypatch):
    stored = {'spec': {'app': {'title': 'Demo', 'signup_domain': 'example.test'}}}
    monkeypatch.setattr(main, 'SIGNUP_DOMAIN', '')
    assert main._effective_signup_domain(stored) == 'example.test'
    assert main._effective_signup_domain({'spec': {'app': {'signup_domain': None}}}) == ''
    assert main._effective_signup_domain(None) == ''
    monkeypatch.setattr(main, 'SIGNUP_DOMAIN', 'override.example.test')
    assert main._effective_signup_domain(stored) == 'override.example.test'
    conn = ConnectionSpy()
    monkeypatch.setattr(main, 'pool', PoolSpy(conn))
    monkeypatch.setattr(main, 'active_spec', lambda conn: stored)
    assert main.meta()['signup_domain'] == 'override.example.test'


def test_generic_transition_route_registration_and_closed_body(example, monkeypatch):
    conn, actor = ConnectionSpy(), user('admin')
    monkeypatch.setattr(main, 'pool', PoolSpy(conn))
    monkeypatch.setattr(records, 'current_spec', lambda conn: example)
    calls = []

    def transition(*args, **kwargs):
        calls.append((args, kwargs))
        return {'state': 'approved'}

    monkeypatch.setattr(workflow, 'transition', transition)
    main.app.dependency_overrides[main.who] = lambda: actor
    try:
        client = TestClient(main.app)  # No lifespan context: no database bootstrap.
        record_id, line_id = uuid.uuid4(), uuid.uuid4()
        route = '/api/e/request/' + str(record_id) + '/transition'
        response = client.post(route, json={'to_state': 'approved', 'line_id': str(line_id)})
        assert response.status_code == 200 and response.json() == {'state': 'approved'}
        assert calls[0][0][2].name == 'request'
        assert calls[0][0][3] == record_id and calls[0][1]['line_id'] == line_id
        assert client.post(route, json={'to_state': 'approved', 'owner_id': actor.id}).status_code == 422
        assert client.post(route, json={'to_state': 'bad;SQL'}).status_code == 422
        assert client.post(route, json={'to_state': 'approved', 'data': 'not an object'}).status_code == 422
        schema = main.app.openapi()
        assert schema['components']['schemas']['TransitionInput']['additionalProperties'] is False
    finally:
        main.app.dependency_overrides.clear()


def test_daily_limit_runs_inside_creation_transaction_before_insert(example, monkeypatch):
    entity = records.entity_of(example, 'request')
    conn, actor = ConnectionSpy(), user('requester')

    def refuse(conn, spec, model, current):
        assert conn.transactions == 1
        assert model is entity and current is actor
        assert not conn.calls
        raise HTTPException(429, 'Daily limit')

    monkeypatch.setattr(workflow, 'enforce_daily_limit', refuse)
    with pytest.raises(HTTPException) as error:
        records.create(conn, example, entity, {'reason': 'Synthetic request', 'lines': [str(uuid.uuid4())]}, actor)
    assert error.value.status_code == 429


def test_generic_transition_dispatch_uses_line_ids(example, monkeypatch):
    actor = user('admin')
    requester = user('requester')
    record_id, line_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(main, 'pool', PoolSpy(ConnectionSpy()))
    monkeypatch.setattr(records, 'current_spec', lambda conn: example)
    calls = []

    def transition(conn, spec, entity, record_id, current, target, **kwargs):
        calls.append((entity.name, record_id, current, target, kwargs))
        return {'state': target}

    monkeypatch.setattr(workflow, 'transition', transition)
    out = main.transition_record('request', record_id, main.TransitionInput(to_state='needs_info', line_id=line_id), actor)
    assert out == {'state': 'needs_info'}
    assert calls[-1] == ('request', record_id, actor, 'needs_info', {'line_id': line_id, 'data': {}})
    main.transition_record('request', record_id, main.TransitionInput(
        to_state='pending', line_id=line_id, data={'justification': 'Synthetic answer'}), requester)
    assert calls[-1][3] == 'pending'
    assert calls[-1][4]['data'] == {'justification': 'Synthetic answer'}
    main.transition_record('request', record_id, main.TransitionInput(to_state='approved', line_id=line_id), actor)
    assert calls[-1][3] == 'approved'
    with pytest.raises(HTTPException) as error:
        main.transition_record('no_such_entity', record_id, main.TransitionInput(to_state='approved'), actor)
    assert error.value.status_code == 404


def test_user_roles_body_is_closed_and_last_admin_is_protected():
    with pytest.raises(ValidationError):
        main.UserRoles(roles=['admin'], is_admin=False)
    with pytest.raises(ValidationError):
        main.UserRoles(roles=[])
    with pytest.raises(HTTPException) as error:
        main._protect_last_admin(ConnectionSpy([{'n': 1}]))
    assert error.value.status_code == 400
    main._protect_last_admin(ConnectionSpy([{'n': 2}]))
