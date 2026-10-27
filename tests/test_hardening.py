"""Deterministic hostile-spec checks; destructive SQL execution is explicit opt-in."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from psycopg import sql

from app import spec as spec_module
from app.migrate import ConfirmationRequired, active, apply, archive_name, plan
from app.spec import SpecValidationError, validate
from tests.test_migrate import pg

ROOT = Path(spec_module.__file__).resolve().parent.parent


def document():
    return {
        'spec_version': 1, 'app': {'title': 'Synthetic hardening', 'slug': 'synthetic'},
        'roles': ['admin'],
        'entities': [{'name': 'entry', 'kind': 'record', 'owner': 'admin',
                      'fields': [{'name': 'description', 'type': 'text'}]}],
        'workflow': {'entity': 'entry', 'level': 'record', 'initial': 'draft',
                     'states': ['draft', 'ready'],
                     'transitions': [{'from': ['draft'], 'to': 'ready', 'roles': ['admin']}]},
        'views': [],
    }


HOSTILE_NAMES = [
    "entry; DROP TABLE users", "entry' OR '1'='1", 'entry"; SELECT 1; --',
    '../entry', 'entry.name', 'entry name', 'entry\n', 'entry\x00name',
    'a' * 42, '_entry', '9entry', 'ent\u00e9ry', 'ent\u202ery', 'select',
    'users', 'ss_hidden', 'entry--comment', 'entry/*comment*/', 'entry\\name',
    'entry\U0001f600',
]
IDENTIFIER_PATHS = [
    ('app', 'slug'), ('roles', 0), ('entities', 0, 'name'),
    ('entities', 0, 'renamed_from'), ('entities', 0, 'fields', 0, 'name'),
    ('entities', 0, 'fields', 0, 'renamed_from'), ('entities', 0, 'owner'),
    ('workflow', 'initial'), ('workflow', 'states', 0),
    ('workflow', 'transitions', 0, 'to'),
]


@pytest.mark.parametrize('location', IDENTIFIER_PATHS, ids=lambda p: '.'.join(map(str, p)))
@pytest.mark.parametrize('name', HOSTILE_NAMES, ids=['hostile-' + str(i) for i in range(20)])
def test_200_hostile_identifier_specs_are_rejected_before_ddl(location, name):
    candidate = document()
    target = candidate
    for component in location[:-1]:
        target = target[component]
    target[location[-1]] = name
    with pytest.raises(SpecValidationError) as error:
        plan(None, candidate)
    assert error.value.errors
    assert all(item['path'].startswith('$') and item['code'] for item in error.value.errors)


def leaves(statement):
    if isinstance(statement, sql.Composed):
        for part in statement:
            yield from leaves(part)
    else:
        yield statement


def assert_parameter_correspondence(migration):
    for step in migration.steps + migration.destructive_steps:
        parts = list(leaves(step.query))
        assert sum(isinstance(part, sql.Placeholder) for part in parts) == len(step.params)
        for part in parts:
            if isinstance(part, sql.Identifier):
                quoted = part.as_string()
                assert quoted.startswith('"') and quoted.endswith('"')


@pytest.mark.parametrize('identifier', ['a', 'z' * 41, 'entry_09', 'quoted_identifier'])
def test_valid_boundary_identifiers_reach_ddl_only_as_identifiers(identifier):
    candidate = document()
    candidate['entities'][0]['name'] = identifier
    candidate['workflow']['entity'] = identifier
    candidate['entities'][0]['fields'][0]['name'] = identifier
    migration = plan(None, candidate)
    expected = {'x_' + identifier, identifier}
    quoted = {part.as_string() for step in migration.steps for part in leaves(step.query)
              if isinstance(part, sql.Identifier)}
    assert {sql.Identifier(name).as_string() for name in expected} <= quoted
    assert_parameter_correspondence(migration)


@pytest.mark.parametrize('value', [
    "'); DROP TABLE users; --", '"; SELECT pg_sleep(1); --', '/* comment */',
    '$$ SELECT 1 $$', '<script>alert(1)</script>', '%s %(name)s %%',
    "line one\nline two\\end", '\u202equoted \U0001f600',
])
def test_malicious_text_defaults_are_bound_values(value):
    candidate = document()
    candidate['entities'][0]['fields'][0].update(default=value, min=0, max=240)
    migration = plan(None, candidate)
    assert any(value in step.params for step in migration.steps)
    assert all(value not in step.query.as_string() for step in migration.steps)
    assert_parameter_correspondence(migration)


@pytest.mark.parametrize('example', ['access_requests', 'equipment_checkout'])
def test_example_ddl_has_matching_placeholder_parameters(example):
    candidate = json.loads((ROOT / 'examples' / example / 'spec.json').read_bytes())
    assert_parameter_correspondence(plan(None, candidate))
    revised = deepcopy(candidate)
    revised['entities'][0]['fields'].append({'name': 'safe_note', 'type': 'text',
                                           'min': 2, 'max': 80, 'default': 'Synthetic value'})
    assert_parameter_correspondence(plan(None, revised, old_spec=candidate))


def destructive_document():
    return {
        'spec_version': 1, 'app': {'title': 'Synthetic archive', 'slug': 'synthetic'},
        'roles': ['admin'], 'views': [],
        'entities': [{'name': 'sample', 'kind': 'reference',
                      'fields': [{'name': 'label', 'type': 'text'},
                                 {'name': 'obsolete_note', 'type': 'text'}]}],
    }


@pytest.mark.parametrize('kind', ['drop_column', 'drop_table'])
def test_destructive_plans_archive_and_separate_real_drop(kind):
    original = destructive_document()
    revised = deepcopy(original)
    if kind == 'drop_column':
        revised['entities'][0]['fields'].pop()
    else:
        revised['entities'] = []
    reviewed = plan(None, revised, old_spec=original)
    default_sql = '\n'.join(step.query.as_string().lower() for step in reviewed.steps)
    override_sql = '\n'.join(step.query.as_string().lower() for step in reviewed.destructive_steps)
    assert '_archived_' in default_sql
    assert 'drop column' not in default_sql and 'drop table' not in default_sql
    assert kind.replace('_', ' ') in override_sql
    assert_parameter_correspondence(reviewed)


@pytest.mark.parametrize('kind', ['drop_column', 'drop_table'])
@pytest.mark.parametrize('confirmation', ['none', 'wrong_hash', 'right_hash'])
def test_pg_destructive_confirmation_matrix_preserves_or_drops_data(pg, kind, confirmation):
    original = destructive_document()
    initial = plan(pg, original)
    apply(pg, original, initial.plan_hash)
    pg.execute('insert into x_sample(label,obsolete_note) values (%s,%s)',
               ('Synthetic row', 'Preserved note'))
    revised = deepcopy(original)
    if kind == 'drop_column':
        revised['entities'][0]['fields'].pop()
    else:
        revised['entities'] = []
    reviewed = plan(pg, revised)
    assert any(operation['kind'] == kind for operation in reviewed.operations)
    assert all('drop column' not in step.query.as_string().lower() and 'drop table' not in step.query.as_string().lower()
               for step in reviewed.steps)
    assert any(kind.replace('_', ' ') in step.query.as_string().lower() for step in reviewed.destructive_steps)

    if confirmation == 'wrong_hash':
        with pytest.raises(ConfirmationRequired):
            apply(pg, revised, reviewed.plan_hash, confirm_destructive='0' * 64)
        assert active(pg)['spec']['entities'][0]['name'] == 'sample'
        assert pg.execute('select obsolete_note from x_sample').fetchone()['obsolete_note'] == 'Preserved note'
        assert pg.execute('select count(*) as n from ss_migrations').fetchone()['n'] == 1
        return

    explicit = reviewed.plan_hash if confirmation == 'right_hash' else None
    apply(pg, revised, reviewed.plan_hash, confirm_destructive=explicit)
    if kind == 'drop_column':
        columns = [row['column_name'] for row in pg.execute(
            'select column_name from information_schema.columns where table_schema=current_schema() and table_name=%s',
            ('x_sample',)).fetchall()]
        assert 'obsolete_note' not in columns
        archived = archive_name('obsolete_note', reviewed.archive_tag)
        if confirmation == 'none':
            assert archived in columns
            statement = sql.SQL('select ') + sql.Identifier(archived) + sql.SQL(' as note from ') + sql.Identifier('x_sample')
            assert pg.execute(statement).fetchone()['note'] == 'Preserved note'
        else:
            assert archived not in columns
        assert pg.execute('select label from x_sample').fetchone()['label'] == 'Synthetic row'
    else:
        assert pg.execute('select to_regclass(%s) as relation', ('x_sample',)).fetchone()['relation'] is None
        archived = archive_name('x_sample', reviewed.archive_tag)
        if confirmation == 'none':
            statement = sql.SQL('select obsolete_note from ') + sql.Identifier(archived)
            assert pg.execute(statement).fetchone()['obsolete_note'] == 'Preserved note'
        else:
            assert pg.execute('select to_regclass(%s) as relation', (archived,)).fetchone()['relation'] is None
    assert validate(active(pg)['spec']) == validate(revised)
