"""Planner tests always run; database safety tests require an explicit opt-in."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from app.migrate import (
    ConfirmationRequired, MigrationError, PlanMismatch, active, apply,
    archive_name, diff, plan, table_name, tables,
)
from app.spec import validate

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def example():
    return json.loads((ROOT / "examples/access_requests/spec.json").read_text())


def test_empty_to_example_is_additive(example):
    result = plan(None, example)
    assert result.operations
    assert {op["classification"] for op in result.operations} == {"additive"}
    assert set(tables(validate(example))) == {"topic", "tool", "request", "request_lines"}
    assert all('"x_' in step["sql"] for step in result.public()["sql"])


def test_plan_hash_is_deterministic_and_covers_spec(example):
    first = plan(None, example)
    assert first.plan_hash == plan(None, deepcopy(example)).plan_hash
    example["app"]["title"] = "Changed title"
    assert first.plan_hash != plan(None, example).plan_hash


def test_drops_archive_by_default_and_expose_explicit_alternative(example):
    new = deepcopy(example)
    new["entities"][0]["fields"].pop()
    result = plan(None, new, old_spec=example, archive_tag="202601010000001")
    assert any(op["classification"] == "destructive" for op in result.operations)
    assert any("_archived_202601010000001_icon" in step["sql"] for step in result.public()["sql"])
    assert not any("drop column" in step["sql"] for step in result.public()["sql"])
    assert any("drop column" in step["sql"] for step in result.public()["destructive_sql"])


def test_type_and_constraint_narrowing_are_risky(example):
    new = deepcopy(example)
    new["entities"][0]["fields"][0]["max"] = 4
    op = next(op for op in diff(example, new) if op.kind == "alter_column")
    assert op.classification == "risky"
    assert plan(None, new, old_spec=example).operations[0]["violating_rows"] is None


def test_nullable_column_is_additive(example):
    new = deepcopy(example)
    new["entities"][0]["fields"].append({"name": "description", "type": "text"})
    assert [(op.kind, op.classification) for op in diff(example, new)] == [("add_column", "additive")]


def test_explicit_renames_do_not_drop_data(example):
    new = deepcopy(example)
    new["entities"][1]["fields"][0].update(name="display_name", renamed_from="name")
    # WP6: report/export selectors are closed references; a valid rename renames them too.
    new["views"][3]["columns"][1]["source"] = "lines.ref.display_name"
    new["views"][3]["workbook"]["matrix"]["columns"][1]["source"] = "lines.ref.display_name"
    new["exports"][0]["columns"][1]["source"] = "lines.ref.display_name"
    ops = diff(example, new)
    assert [op.kind for op in ops] == ["rename_column"]
    assert "drop column" not in "\n".join(step["sql"] for step in plan(None, new, old_spec=example).public()["sql"])


def test_archive_names_fit_postgres_identifier_limit():
    name = archive_name("x_" + "a" * 41 + "_lines", "202601010000001234")
    assert name.startswith("_archived_202601010000001234_")
    assert len(name.encode()) <= 63


def test_values_remain_separate_from_ddl(example):
    label = "literal'); DROP TABLE users; --"
    example["entities"][0]["fields"].append({"name": "example_text", "type": "text", "default": label})
    result = plan(None, example).public()
    assert all(label not in step["sql"] for step in result["sql"])
    assert any(label in step["parameters"] for step in result["sql"])


def test_array_defaults_have_explicit_type_casts(example):
    example["entities"][0]["fields"].append({"name": "dates", "type": "list<date>", "default": ["2026-01-01"]})
    statement = next(step for step in plan(None, example).public()["sql"] if "create table \"x_topic\"" in step["sql"])
    assert '"dates" date[] default %s::date[]' in statement["sql"]
    assert ["2026-01-01"] in statement["parameters"]


def test_reducing_tree_depth_requires_review(example):
    new = deepcopy(example)
    new["entities"][0]["hierarchy"]["max_depth"] = 2
    result = plan(None, new, old_spec=example)
    assert [(op["kind"], op["classification"]) for op in result.operations] == [("narrow_hierarchy", "risky")]
    assert result.operations[0]["violating_rows"] is None


def test_second_example_uses_the_same_planner():
    spec = json.loads((ROOT / "examples/equipment_checkout/spec.json").read_text())
    assert set(tables(validate(spec))) == {"asset", "checkout"}
    assert {op["classification"] for op in plan(None, spec).operations} == {"additive"}


@pytest.fixture
def pg():
    if os.environ.get("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Real-Postgres migration checks deferred; set RUN_POSTGRES_TESTS=1 on a disposable test database")
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        # A private transaction/schema isolates each test without dropping shared data.
        with conn.transaction(force_rollback=True):
            conn.execute("create schema migration_test")
            conn.execute("set local search_path to migration_test, public")
            conn.execute((ROOT / "app/core.sql").read_text())
            yield conn


def install(pg, spec):
    result = plan(pg, spec)
    apply(pg, spec, result.plan_hash)


def test_pg_additive_auto_apply(pg, example):
    install(pg, example)
    assert active(pg)["spec"]["app"]["title"] == "Access Requests"
    new = deepcopy(example)
    new["entities"][0]["fields"].append({"name": "description", "type": "text"})
    install(pg, new)
    assert pg.execute("select description from x_topic").fetchall() == []


def test_pg_risky_confirmation_and_violating_count(pg, example):
    install(pg, example)
    pg.execute("insert into x_topic (name) values (%s)", ("A long topic",))
    new = deepcopy(example)
    new["entities"][0]["fields"][0]["max"] = 4
    reviewed = plan(pg, new)
    assert reviewed.operations[0]["violating_rows"] == 1
    with pytest.raises(ConfirmationRequired):
        apply(pg, new, reviewed.plan_hash)
    with pytest.raises(MigrationError):
        apply(pg, new, reviewed.plan_hash, confirm_risky=True)
    pg.execute("update x_topic set name = %s", ("Fits",))
    reviewed = plan(pg, new)
    apply(pg, new, reviewed.plan_hash, confirm_risky=True)


def test_pg_archive_keeps_data_and_accepts_future_inserts(pg, example):
    install(pg, example)
    pg.execute("insert into x_topic (name,icon) values (%s,%s)", ("Demo", "code"))
    new = deepcopy(example)
    new["entities"][0]["fields"].pop()
    reviewed = plan(pg, new)
    apply(pg, new, reviewed.plan_hash)
    from psycopg import sql
    archived = archive_name("icon", reviewed.archive_tag)
    row = pg.execute(sql.SQL("select ") + sql.Identifier(archived) + sql.SQL(" as value from x_topic")).fetchone()
    assert row["value"] == "code"
    pg.execute("insert into x_topic (name) values (%s)", ("Another demo",))


def test_pg_stale_plan_hash_is_rejected(pg, example):
    reviewed = plan(pg, example)
    new = deepcopy(example)
    new["app"]["title"] = "Another app"
    with pytest.raises(PlanMismatch):
        apply(pg, new, reviewed.plan_hash)
    assert active(pg) is None


def test_pg_explicit_rename_preserves_data(pg, example):
    # Rename an unreferenced field: views in the spec still point at "name".
    install(pg, example)
    pg.execute("insert into x_topic (name, icon) values (%s, %s)", ("Keep me", "star"))
    new = deepcopy(example)
    new["entities"][0]["fields"][1].update(name="glyph", renamed_from="icon")
    install(pg, new)
    assert pg.execute("select glyph from x_topic").fetchone()["glyph"] == "star"


def test_pg_round_trip(pg, example):
    install(pg, example)
    new = deepcopy(example)
    new["entities"][0]["fields"].append({"name": "description", "type": "text"})
    install(pg, new)
    install(pg, example)
    assert active(pg)["spec"]["app"]["title"] == "Access Requests"
