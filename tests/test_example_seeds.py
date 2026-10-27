"""Seed data validates against bundled fields; SQL call spies need no database."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import uuid

import pytest

from app import records, spec as spec_module
from app.spec import validate

HERE = Path(__file__).resolve().parent.parent
ROOT = Path(spec_module.__file__).resolve().parent.parent


def load(example):
    definition = importlib.util.spec_from_file_location("seed_" + example, HERE / "examples" / example / "seed.py")
    module = importlib.util.module_from_spec(definition)
    definition.loader.exec_module(module)
    return module


class Spy:
    def __init__(self, populated=False):
        self.populated, self.calls, self.current = populated, [], ""

    def execute(self, statement, params=()):
        self.current = statement.as_string()
        self.calls.append((self.current, params))
        return self

    def fetchone(self):
        if self.current.startswith("select"):
            return {"present": 1} if self.populated else None
        return {"id": len(self.calls)}

    @contextmanager
    def cursor(self):
        yield self

    def executemany(self, statement, rows):
        for row in rows:
            self.execute(statement, row)


def test_equipment_has_fifteen_unique_typed_assets():
    seed = load("equipment_checkout")
    spec = validate((ROOT / "examples/equipment_checkout/spec.json").read_text())
    entity = records.entity_of(spec, "asset")
    assert len(seed.ASSETS) == 15
    assert len({tag for tag, _, _ in seed.ASSETS}) == 15
    for tag, name, condition in seed.ASSETS:
        assert records.values_for(entity, {"tag": tag, "name": name, "condition": condition})["tag"] == tag
    spy = Spy()
    seed.seed_assets(spy)
    assert len(spy.calls) == 16
    assert all('"x_asset"' in statement for statement, _ in spy.calls)
    assert all(tag not in statement for statement, _ in spy.calls for tag, _, _ in seed.ASSETS)


def test_access_catalog_fields_are_valid_and_loader_only_targets_generated_tables():
    seed = load("access_requests")
    spec = validate((ROOT / "examples/access_requests/spec.json").read_text())
    topic, tool = records.entity_of(spec, "topic"), records.entity_of(spec, "tool")
    count = 0

    def walk(nodes):
        nonlocal count
        for node in nodes:
            records.values_for(topic, {"name": node["name"], "icon": node.get("icon", "folder")})
            for name, domains, review, notes in node.get("items", []):
                records.values_for(tool, {"name": name, "domains": domains.split(), "needs_review": review,
                                          "notes": notes, "parent_id": str(uuid.uuid4())})
                count += 1
            walk(node.get("children", []))

    walk(seed.SEED)
    assert count >= 90
    spy = Spy()
    seed.seed_catalog(spy)
    assert all('"x_topic"' in statement or '"x_tool"' in statement for statement, _ in spy.calls)
    assert not any('"categories"' in statement or '"items"' in statement for statement, _ in spy.calls)


@pytest.mark.parametrize("example,function", [("access_requests", "seed_catalog"), ("equipment_checkout", "seed_assets")])
def test_seed_does_not_overwrite_and_refuses_obsolete_mode(example, function):
    seed = getattr(load(example), function)
    spy = Spy(populated=True)
    seed(spy)
    assert len(spy.calls) == 1
    with pytest.raises(ValueError, match="spec-generated"):
        seed(Spy(), dynamic=False)
