"""The spec boundary is independent of Postgres and of any model endpoint."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.spec import MAX_BYTES, SpecValidationError, schema, validate

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def example():
    return json.loads((ROOT / "examples/access_requests/spec.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["access_requests", "equipment_checkout"])
def test_bundled_examples(name):
    raw = (ROOT / "examples" / name / "spec.json").read_bytes()
    spec = validate(raw)
    assert spec.spec_version == 1
    assert validate(spec.model_dump(mode="json", by_alias=True, exclude_unset=True)) == spec


def test_json_schema_cannot_drift():
    assert json.loads((ROOT / "spec/schema.json").read_text(encoding="utf-8")) == schema()


def rejected(document, code):
    with pytest.raises(SpecValidationError) as raised:
        validate(document)
    errors = raised.value.errors
    assert errors and code in {error["code"] for error in errors}, errors
    for error in errors:
        assert set(error) == {"path", "code", "message"}
        assert error["path"].startswith("$")
        assert isinstance(error["message"], str) and error["message"]
    return errors


@pytest.mark.parametrize("name,code", [
    ("Upper", "string_pattern_mismatch"), ("two.words", "string_pattern_mismatch"),
    ("a" * 42, "string_pattern_mismatch"), ("quote'", "string_pattern_mismatch"),
    ("select", "reserved_identifier"), ("users", "reserved_identifier"),
    ("ss_private", "reserved_identifier"), ("équipement", "string_pattern_mismatch"),
])
def test_identifier_contract(example, name, code):
    example["entities"][0]["name"] = name
    errors = rejected(example, code)
    assert errors[0]["path"] == "$.entities[0].name"


def test_engine_column_name(example):
    example["entities"][0]["fields"][0]["name"] = "id"
    assert rejected(example, "engine_field")[0]["path"] == "$.entities[0].fields[0].name"


@pytest.mark.parametrize("where", ["root", "app", "entity", "field", "workflow", "transition", "view", "export", "dashboard"])
def test_extra_keys_are_forbidden_at_every_boundary(example, where):
    target = {"root": example, "app": example["app"], "entity": example["entities"][0],
              "field": example["entities"][0]["fields"][0], "workflow": example["workflow"],
              "transition": example["workflow"]["transitions"][0], "view": example["views"][0],
              "export": example["exports"][0], "dashboard": example["dashboard"][0]}[where]
    target["raw_sql"] = "DROP TABLE users"
    rejected(example, "extra_forbidden")


@pytest.mark.parametrize("which", ["entities", "fields", "states"])
def test_size_limits(example, which):
    if which == "entities":
        example["entities"] = [{"name": f"entity_{i}", "kind": "reference", "fields": []} for i in range(21)]
    elif which == "fields":
        example["entities"][0]["fields"] = [{"name": f"field_{i}", "type": "text"} for i in range(31)]
    else:
        example["workflow"]["states"] = [f"state_{i}" for i in range(13)]
    rejected(example, "too_long")


@pytest.mark.parametrize("type_name", ["sql", "jsonb", "list<sql>", "text); DROP TABLE users;--"])
def test_type_set_is_closed(example, type_name):
    example["entities"][0]["fields"][0]["type"] = type_name
    rejected(example, "union_tag_invalid")


@pytest.mark.parametrize("kind,extra,default", [
    ("text", {}, {"sql": "now()"}), ("int", {}, "1"), ("bool", {}, 1),
    ("date", {}, "now()"), ("number", {}, True), ("domain", {}, "https://example.test"),
    ("enum", {"values": ["ready"]}, "other"),
    ("list<int>", {}, ["1"]), ("list<enum>", {"values": ["ready"]}, ["other"]),
    ("ref", {"to": "topic"}, "not-a-uuid"),
])
def test_defaults_are_typed_literals(example, kind, extra, default):
    example["entities"][0]["fields"].append({"name": "example_field", "type": kind, "default": default, **extra})
    rejected(example, "invalid_default")


def test_all_closed_scalar_and_list_types(example):
    scalar = {"text": "literal", "int": 3, "number": 1.5, "bool": True, "date": "2026-01-01",
              "datetime": "2026-01-01T12:00:00Z", "domain": "example.test", "email": "demo@example.test",
              "url": "https://example.test"}
    for index, (kind, value) in enumerate(scalar.items()):
        example["entities"][0]["fields"].append({"name": f"scalar_{index}", "type": kind, "default": value})
        example["entities"][0]["fields"].append({"name": f"list_{index}", "type": f"list<{kind}>", "default": [value]})
    example["entities"][0]["fields"].append({"name": "enum_list", "type": "list<enum>", "values": ["ready"], "default": ["ready"]})
    example["entities"][0]["fields"].append({"name": "ref_list", "type": "list<ref>", "to": "tool", "default": []})
    assert validate(example)


@pytest.mark.parametrize("target", ["ref", "parent", "line", "view", "role"])
def test_graph_references_resolve(example, target):
    if target == "ref":
        example["entities"][0]["fields"].append({"name": "link", "type": "ref", "to": "missing"})
    elif target == "parent":
        example["entities"][1]["parent"] = "missing"
    elif target == "line":
        example["entities"][2]["lines"]["entity_ref"] = "missing"
    elif target == "view":
        example["views"][0]["entity"] = "missing"
    else:
        example["workflow"]["transitions"][0]["roles"] = ["missing"]
    rejected(example, "unresolved_role" if target == "role" else "unresolved_entity")


def test_parent_hierarchy_is_acyclic(example):
    example["entities"][0]["parent"] = "tool"
    rejected(example, "hierarchy_cycle")


def test_every_state_reachable(example):
    example["workflow"]["states"].append("orphan")
    rejected(example, "unreachable_state")


def test_transition_state_must_exist(example):
    example["workflow"]["transitions"][0]["to"] = "missing"
    rejected(example, "unresolved_state")


def test_initial_state_must_exist(example):
    example["workflow"]["initial"] = "missing"
    rejected(example, "unresolved_state")


def test_admin_role_is_required(example):
    example["roles"].remove("admin")
    rejected(example, "missing_admin")


def test_each_non_admin_role_has_an_entry_view(example):
    for view in example["views"]:
        view["roles"] = ["admin"]
    rejected(example, "missing_entry_view")


@pytest.mark.parametrize("target", ["entities", "fields", "states", "roles"])
def test_identifiers_are_unique(example, target):
    if target == "entities":
        example["entities"].append(deepcopy(example["entities"][0]))
    elif target == "fields":
        example["entities"][0]["fields"].append(deepcopy(example["entities"][0]["fields"][0]))
    elif target == "states":
        example["workflow"]["states"].append("pending")
    else:
        example["roles"].append("admin")
    rejected(example, "duplicate_identifier")


@pytest.mark.parametrize("raw", [b"{", b"\xff", '{"spec_version":1,"spec_version":1}', '{"value":NaN}'])
def test_json_is_strict(raw):
    rejected(raw, "invalid_json")


@pytest.mark.parametrize("as_bytes", [True, False])
def test_64kb_cap_is_utf8_bytes(example, as_bytes):
    example["padding"] = "é" * (MAX_BYTES // 2)
    rejected(json.dumps(example, ensure_ascii=False).encode("utf-8") if as_bytes else example, "spec_too_large")


def test_boolean_is_not_version_one(example):
    example["spec_version"] = True
    rejected(example, "invalid_version")


def test_export_selectors_resolve(example):
    example["exports"][0]["from"] = "request.lines.tool.unknown"
    rejected(example, "unresolved_selector")


def test_filter_fields_are_closed(example):
    example["exports"][0]["where"]["raw_sql"] = "1=1"
    rejected(example, "unresolved_filter")


def test_labels_are_data_not_code(example):
    example["app"]["title"] = '<img src=x onerror="alert(1)">'
    assert validate(example).app.title == example["app"]["title"]


def test_generated_line_namespace_cannot_collide(example):
    example["entities"].append({"name": "request_lines", "kind": "reference", "fields": []})
    rejected(example, "generated_name_collision")


def test_hierarchy_parent_column_is_unambiguous(example):
    example["entities"][1]["hierarchy"] = {"max_depth": 4}
    rejected(example, "ambiguous_hierarchy")


def test_transition_cannot_collect_engine_metadata(example):
    example["workflow"]["transitions"][0]["collects"] = "owner_id"
    rejected(example, "engine_field")
