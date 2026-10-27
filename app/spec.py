"""The closed, deterministic spec boundary. Model output is always untrusted data.

``validate`` accepts UTF-8 JSON or a JSON object and returns a typed ``Spec``.
Failures expose JSON paths and stable codes for the designer's repair loop.
No spec value is evaluated, and this module never opens a database connection.
"""
from __future__ import annotations

from datetime import date, datetime
import json
import math
from pathlib import Path
import re
from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, StringConstraints,
    ValidationError, field_validator, model_validator,
)
from pydantic_core import PydanticCustomError

MAX_BYTES = 64 * 1024
IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_]{0,40}$"
ENGINE_NAMES = frozenset({
    "users", "sessions", "allowed_emails", "comments", "request_comments",
    "ss_spec", "ss_migrations",
})
# SQL keywords are rejected even though the migration layer quotes identifiers.
# This also keeps names readable in exports and the public API.
RESERVED_WORDS = frozenset("""
all analyse analyze and any array as asc asymmetric authorization binary both
case cast check collate collation column concurrently constraint create cross
current_catalog current_date current_role current_schema current_time
current_timestamp current_user default deferrable desc distinct do else end
except false fetch for foreign freeze from full grant group having ilike in
initially inner intersect into is isnull join lateral leading left like limit
localtime localtimestamp natural not notnull null offset on only or order outer
overlaps placing primary references returning right select session_user similar
some symmetric table tablesample then to trailing true union unique user using
variadic verbose when where window with insert update delete drop alter truncate
""".split())
ENGINE_FIELDS = frozenset({
    "id", "parent_id", "owner_id", "created_at", "updated_at", "archived_at",
    "state", "sort", "active",
})


def _identifier(value: str) -> str:
    if value in ENGINE_NAMES or value in RESERVED_WORDS or value.startswith("ss_"):
        raise PydanticCustomError("reserved_identifier", "Identifier is reserved by the engine or SQL")
    return value


Identifier = Annotated[str, StringConstraints(pattern=IDENTIFIER_PATTERN), AfterValidator(_identifier)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=240)]
ScalarType = Literal["text", "int", "number", "bool", "date", "datetime", "domain", "email", "url"]
LIST_TYPES = ("list<text>", "list<int>", "list<number>", "list<bool>", "list<date>",
              "list<datetime>", "list<domain>", "list<email>", "list<url>")
DOMAIN_RE = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)


def _literal(kind: str, value: Any) -> bool:
    """Validate literal defaults; expressions and structured objects are forbidden."""
    if kind == "int":
        return type(value) is int
    if kind == "number":
        return type(value) in (int, float) and math.isfinite(value)
    if kind == "bool":
        return type(value) is bool
    if not isinstance(value, str):
        return False
    if kind == "date":
        try:
            return date.fromisoformat(value).isoformat() == value
        except ValueError:
            return False
    if kind == "datetime":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return "T" in value and parsed.tzinfo is not None
        except ValueError:
            return False
    if kind == "domain":
        return DOMAIN_RE.fullmatch(value) is not None
    if kind == "email":
        return re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) is not None
    if kind == "url":
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    if kind == "ref":
        try:
            UUID(value)
            return True
        except (ValueError, TypeError):
            return False
    return True


class FieldBase(ClosedModel):
    name: Identifier
    label: Label | None = None
    renamed_from: Identifier | None = None
    required: bool = False
    unique: bool = False
    default: Any = None

    @field_validator("name")
    @classmethod
    def available_column(cls, name):
        if name in ENGINE_FIELDS:
            raise PydanticCustomError("engine_field", "Field name is reserved for record metadata")
        return name

    @model_validator(mode="after")
    def literal_default(self):
        if "default" not in self.model_fields_set:
            return self
        value, kind = self.default, self.type
        if value is None:
            valid = not self.required
        elif kind.startswith("list<"):
            valid = isinstance(value, list) and all(_literal(kind[5:-1], item) for item in value)
        elif kind == "enum":
            valid = isinstance(value, str) and value in self.values
        else:
            valid = _literal(kind, value)
        if not valid:
            raise PydanticCustomError("invalid_default", "Default must be a literal value of the field type")
        if value is not None and hasattr(self, "min"):
            measured = len(value) if isinstance(value, str) else value
            if self.min is not None and measured < self.min or self.max is not None and measured > self.max:
                raise PydanticCustomError("invalid_default", "Default is outside the field bounds")
        return self


class TextField(FieldBase):
    type: Literal["text"]
    min: int | None = Field(default=None, ge=0)
    max: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def bounds(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise PydanticCustomError("invalid_bounds", "min must not exceed max")
        return self


class IntField(FieldBase):
    type: Literal["int"]
    min: int | None = None
    max: int | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise PydanticCustomError("invalid_bounds", "min must not exceed max")
        return self


class NumberField(IntField):
    type: Literal["number"]
    min: int | float | None = Field(default=None, allow_inf_nan=False)
    max: int | float | None = Field(default=None, allow_inf_nan=False)


class ScalarField(FieldBase):
    type: Literal["bool", "date", "datetime", "domain", "email", "url"]


class EnumField(FieldBase):
    type: Literal["enum", "list<enum>"]
    values: list[Label] = Field(min_length=1, max_length=64)

    @field_validator("values")
    @classmethod
    def distinct(cls, values):
        if len(values) != len(set(values)):
            raise PydanticCustomError("duplicate_enum", "Enum values must be distinct")
        return values

    @model_validator(mode="after")
    def enum_default(self):
        if self.type == "list<enum>" and self.default is not None and any(v not in self.values for v in self.default):
            raise PydanticCustomError("invalid_default", "List default must use the declared enum values")
        return self


class RefField(FieldBase):
    type: Literal["ref", "list<ref>"]
    to: Identifier


class ListField(FieldBase):
    type: Literal["list<text>", "list<int>", "list<number>", "list<bool>", "list<date>",
                  "list<datetime>", "list<domain>", "list<email>", "list<url>"]


SpecField = Annotated[Union[TextField, IntField, NumberField, ScalarField, EnumField, RefField, ListField], Field(discriminator="type")]


class Hierarchy(ClosedModel):
    max_depth: int = Field(default=4, ge=1, le=12)


class Lines(ClosedModel):
    entity_ref: Identifier
    min: int = Field(default=1, ge=0, le=500)
    max: int = Field(default=80, ge=1, le=500)

    @model_validator(mode="after")
    def bounds(self):
        if self.min > self.max:
            raise PydanticCustomError("invalid_bounds", "min must not exceed max")
        return self


class EntityBase(ClosedModel):
    name: Identifier
    label: Label | None = None
    renamed_from: Identifier | None = None
    fields: list[SpecField] = Field(max_length=30)


class ReferenceEntity(EntityBase):
    kind: Literal["reference"]
    hierarchy: Hierarchy | None = None
    parent: Identifier | None = None


class RecordEntity(EntityBase):
    kind: Literal["record"]
    owner: Identifier | None = None
    lines: Lines | None = None


Entity = Annotated[Union[ReferenceEntity, RecordEntity], Field(discriminator="kind")]


class Transition(ClosedModel):
    from_: list[Identifier] = Field(alias="from", min_length=1)
    to: Identifier
    roles: list[Identifier] = Field(min_length=1)
    collects: Identifier | None = None
    requires: Identifier | None = None


class Rollup(ClosedModel):
    all: dict[Identifier, Identifier]
    mixed: Identifier
    none_decided: Identifier


class Limits(ClosedModel):
    per_user_per_day: int = Field(ge=1, le=10000)


class Workflow(ClosedModel):
    entity: Identifier
    level: Literal["line", "record"]
    initial: Identifier
    states: list[Identifier] = Field(min_length=1, max_length=12)
    transitions: list[Transition] = Field(max_length=144)
    rollup: Rollup | None = None
    limits: Limits | None = None


class ViewBase(ClosedModel):
    name: Identifier | None = None
    label: Label | None = None
    roles: list[Identifier] | None = Field(default=None, min_length=1)


class BrowseView(ViewBase):
    type: Literal["browse_pick"]
    entity: Identifier
    tree: Identifier | None = None
    submit: Identifier


class RecordsView(ViewBase):
    type: Literal["my_records", "queue"]
    entity: Identifier


class ReportColumn(ClosedModel):
    """One closed selector plus its display label; no free SQL or formulas."""

    source: str = Field(min_length=1, max_length=240)
    label: Label
    empty: str = Field(default="", max_length=240)

    @model_validator(mode="before")
    @classmethod
    def coerce_name(cls, value):
        if isinstance(value, str):
            return {"source": value, "label": value}
        return value


class MatrixConfig(ClosedModel):
    sheet: Label
    title: Label
    key: str = Field(min_length=1, max_length=240)
    columns: list[ReportColumn] = Field(min_length=1)
    state_labels: dict[Identifier, Label] = Field(min_length=1)
    count_value: Label
    total_label: Label = "Total Allowed"


class SummaryMetric(ClosedModel):
    kind: Literal["owners", "groups", "items", "records", "state"]
    label: Label
    source: str | None = Field(default=None, min_length=1, max_length=240)
    state: Identifier | None = None


class PerPersonConfig(ClosedModel):
    title: Label
    name_label: Label = "Person"
    metrics: list[SummaryMetric] = Field(min_length=1)


class SummaryConfig(ClosedModel):
    sheet: Label
    title: Label
    headers: list[Label] = Field(min_length=1, max_length=8)
    metrics: list[SummaryMetric] = Field(min_length=1)
    per_person: PerPersonConfig | None = None


class WorkbookConfig(ClosedModel):
    person_title: Label
    state_source: str = Field(min_length=1, max_length=240)
    state_labels: dict[Identifier, Label] = Field(min_length=1)
    state_colors: dict[Identifier, Literal["positive", "negative", "waiting"]] = Field(default_factory=dict)
    matrix: MatrixConfig
    summary: SummaryConfig


class ReportView(ViewBase):
    type: Literal["report"]
    entity: Identifier
    columns: list[ReportColumn] | None = None
    where: dict[str, FilterValue] = Field(default_factory=dict)
    formats: list[Literal["csv", "xlsx"]] = Field(default_factory=lambda: ["csv", "xlsx"], min_length=1)
    workbook: WorkbookConfig | None = None


class ReferenceView(ViewBase):
    type: Literal["reference_editor"]
    entities: list[Identifier] = Field(min_length=1)
    import_: Literal["csv"] | None = Field(default=None, alias="import")


class SummaryView(ViewBase):
    type: Literal["exports", "dashboard"]


View = Annotated[Union[BrowseView, RecordsView, ReportView, ReferenceView, SummaryView], Field(discriminator="type")]
FilterValue = str | int | float | bool | None


class ExportColumn(ClosedModel):
    """One grouped export column over a closed selector; aggregates are closed."""

    source: str = Field(min_length=1, max_length=240)
    label: Label
    aggregate: Literal["value", "join_distinct", "none_true"] = "value"
    distinct_by: str | None = Field(default=None, min_length=1, max_length=240)
    true_label: str = Field(default="yes", max_length=80)
    false_label: str = Field(default="no", max_length=80)
    empty: str = Field(default="", max_length=240)


class Export(ClosedModel):
    name: Identifier
    label: Label | None = None
    from_: str = Field(alias="from", min_length=1, max_length=240)
    where: dict[str, FilterValue] = Field(default_factory=dict)
    distinct: bool = False
    columns: list[ExportColumn] | None = None
    formats: list[Literal["txt", "csv", "xlsx"]] = Field(min_length=1)


class Dashboard(ClosedModel):
    label: Label
    count: Identifier
    where: dict[str, FilterValue] = Field(default_factory=dict)


class App(ClosedModel):
    title: Label
    slug: Identifier
    signup_domain: str | None = None

    @field_validator("signup_domain")
    @classmethod
    def valid_domain(cls, value):
        if value is not None and (not DOMAIN_RE.fullmatch(value) or value.startswith("*.")):
            raise PydanticCustomError("invalid_domain", "Signup domain must be a literal domain")
        return value


class Spec(ClosedModel):
    spec_version: Literal[1]
    app: App
    roles: list[Identifier] = Field(min_length=1, max_length=20)
    entities: list[Entity] = Field(max_length=20)
    workflow: Workflow | None = None
    views: list[View] = Field(max_length=80)
    exports: list[Export] = Field(default_factory=list, max_length=40)
    dashboard: list[Dashboard] = Field(default_factory=list, max_length=40)

    @field_validator("spec_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise PydanticCustomError("invalid_version", "spec_version must be the integer 1")
        return value


class SpecValidationError(ValueError):
    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("; ".join(f"{item['path']}: {item['message']}" for item in errors))


def _path(location, document) -> str:
    """Pydantic union tags are diagnostic metadata, not JSON object keys."""
    result, current = "$", document
    for part in location:
        if isinstance(part, int):
            result += f"[{part}]"
            current = current[part] if isinstance(current, list) and part < len(current) else None
        elif isinstance(current, dict) and part in current:
            result += "." + str(part)
            current = current[part]
        elif part in {"reference", "record", "text", "int", "number", "bool", "date", "datetime",
                      "domain", "email", "url", "enum", "ref", *LIST_TYPES, "list<ref>", "list<enum>",
                      "browse_pick", "my_records", "queue", "report", "reference_editor", "exports", "dashboard"}:
            continue
        else:
            result += "." + str(part)
            current = None
    return result


def _graph(spec: Spec) -> list[dict[str, str]]:
    errors = []

    def error(path, code, message):
        errors.append({"path": path, "code": code, "message": message})

    def distinct(values, path):
        seen = set()
        for i, value in enumerate(values):
            if value in seen:
                error(f"{path}[{i}]", "duplicate_identifier", "Identifiers must be distinct")
            seen.add(value)

    distinct(spec.roles, "$.roles")
    if "admin" not in spec.roles:
        error("$.roles", "missing_admin", "At least one admin-capable role (admin) is required")
    distinct([entity.name for entity in spec.entities], "$.entities")
    entities = {entity.name: entity for entity in spec.entities}
    roles = set(spec.roles)
    generated = {entity.name + "_lines" for entity in spec.entities if isinstance(entity, RecordEntity) and entity.lines}
    for i, entity in enumerate(spec.entities):
        if entity.name in generated:
            error(f"$.entities[{i}].name", "generated_name_collision", "Entity name collides with a generated line table")
        if isinstance(entity, ReferenceEntity) and entity.parent and entity.hierarchy:
            error(f"$.entities[{i}].hierarchy", "ambiguous_hierarchy", "Use a ref field to combine grouping with a self hierarchy")

    def resolves(value, path, allowed, kind):
        if value is not None and value not in allowed:
            error(path, "unresolved_" + kind, f"Unknown {kind}: {value}")

    for i, entity in enumerate(spec.entities):
        base = f"$.entities[{i}]"
        distinct([field.name for field in entity.fields], base + ".fields")
        hints = set()
        for j, field in enumerate(entity.fields):
            if field.renamed_from and field.renamed_from in hints:
                error(f"{base}.fields[{j}].renamed_from", "duplicate_identifier", "Rename hints must be distinct")
            hints.add(field.renamed_from)
        for j, field in enumerate(entity.fields):
            if isinstance(field, RefField):
                resolves(field.to, f"{base}.fields[{j}].to", entities, "entity")
        if isinstance(entity, ReferenceEntity):
            resolves(entity.parent, base + ".parent", entities, "entity")
            visited, node = set(), entity
            while isinstance(node, ReferenceEntity) and node.parent in entities:
                if node.name in visited:
                    error(base + ".parent", "hierarchy_cycle", "Entity parent hierarchy must be acyclic")
                    break
                visited.add(node.name)
                node = entities[node.parent]
        else:
            resolves(entity.owner, base + ".owner", roles, "role")
            if entity.lines:
                resolves(entity.lines.entity_ref, base + ".lines.entity_ref", entities, "entity")

    workflow = spec.workflow
    if workflow:
        resolves(workflow.entity, "$.workflow.entity", entities, "entity")
        entity = entities.get(workflow.entity)
        if entity is not None and not isinstance(entity, RecordEntity):
            error("$.workflow.entity", "workflow_entity", "Workflow entity must have kind record")
        if workflow.level == "line" and entity is not None and not getattr(entity, "lines", None):
            error("$.workflow.level", "missing_lines", "Line-level workflow requires a lines entity")
        states = set(workflow.states)
        distinct(workflow.states, "$.workflow.states")
        resolves(workflow.initial, "$.workflow.initial", states, "state")
        reachable = {workflow.initial}
        for i, transition in enumerate(workflow.transitions):
            base = f"$.workflow.transitions[{i}]"
            resolves(transition.to, base + ".to", states, "state")
            for j, state in enumerate(transition.from_):
                resolves(state, f"{base}.from[{j}]", states, "state")
            for j, role in enumerate(transition.roles):
                resolves(role, f"{base}.roles[{j}]", roles, "role")
            for attribute in ("collects", "requires"):
                if getattr(transition, attribute) in ENGINE_FIELDS | {"record_id", "ref_id"}:
                    error(base + "." + attribute, "engine_field", "Transition data cannot overwrite engine metadata")
        while True:
            more = {t.to for t in workflow.transitions if reachable.intersection(t.from_)}
            if more <= reachable:
                break
            reachable |= more
        for i, state in enumerate(workflow.states):
            if state not in reachable:
                error(f"$.workflow.states[{i}]", "unreachable_state", "Every state must be reachable from initial")
        if workflow.rollup:
            for state in workflow.rollup.all:
                resolves(state, "$.workflow.rollup.all." + state, states, "state")

    wf_states = set(workflow.states) if workflow else set()
    collected_names = {name for t in (workflow.transitions if workflow else [])
                       for name in (t.collects, t.requires)} - {None}

    def selector(value, path, *, root, allow_value=False):
        """Closed graph walk mirroring the export engine's interpreter.

        Sources stay inside one root entity: root fields, ``lines`` chains,
        ``ref`` hops, ``owner`` id/name/email, ``parent`` and ``parent_path``.
        Anything else is rejected; no free SQL or formula language exists.
        """
        if value == "$value":
            if allow_value:
                return None
            error(path, "unresolved_selector", "Grouped value selectors need an export grouping")
            return None
        parts = value.split(".")
        index = 0
        entity = entities.get(root)
        if entity is None:
            error(path, "unresolved_selector", f"Unknown entity: {root}")
            return None
        if parts and parts[0] == root:
            index = 1
        elif parts and parts[0] in entities and parts[0] != root:
            # The engine walks ref fields by name, so an entity-named part that
            # is also a root field (checkout.asset) is a field path, not a hop.
            if parts[0] not in {field.name for field in entity.fields}:
                error(path, "unresolved_selector", "Selectors must stay within the declared root entity")
                return None
        line = False
        while index < len(parts):
            part = parts[index]
            if part == "owner" and isinstance(entity, RecordEntity) and entity.owner and not line:
                if index + 2 != len(parts) or parts[index + 1] not in ("id", "name", "email"):
                    error(path, "unresolved_selector", "Owner selectors expose only id, name and email")
                    return None
                return entity
            if part in ("parent", "parent_path") and not line:
                target = entity.parent or (entity.name if entity.hierarchy else None)
                if target is None or target not in entities:
                    error(path, "unresolved_selector", "Entity has no parent")
                    return None
                if part == "parent_path":
                    if index + 2 != len(parts):
                        error(path, "unresolved_selector", "parent_path must end with a label field")
                        return None
                    label = parts[index + 1]
                    if label not in {field.name for field in entities[target].fields}:
                        error(path, "unresolved_selector", f"Unknown path label: {label}")
                        return None
                    return entities[target]
                entity = entities[target]
                index += 1
                continue
            if part == "lines" and isinstance(entity, RecordEntity) and entity.lines and not line:
                entity = entities.get(entity.lines.entity_ref)
                line = True
                index += 1
                if entity and index < len(parts) and parts[index] in ("ref", entity.name):
                    # The engine resolves the referenced row here; fields and
                    # parent walks below apply to the referenced entity.
                    index += 1
                    line = False
                if entity is None:
                    return None
                continue
            fields = {field.name: field for field in entity.fields} if not line else {}
            collected = collected_names if line else set()
            if part not in fields and part not in ENGINE_FIELDS and part not in collected:
                error(path, "unresolved_selector", f"Unknown selector field: {part}")
                return None
            if index == len(parts) - 1:
                return entity
            field = fields.get(part)
            if field is None or not isinstance(field, RefField):
                error(path, "invalid_selector", "Only references and lines may be traversed")
                return None
            entity = entities.get(field.to)
            if entity is None:
                return None
            index += 1
        return entity

    def check_where(owner, group, i, where):
        if isinstance(owner, Export):
            root = owner.from_.split(".")[0]
        elif isinstance(owner, Dashboard):
            root = owner.count
        else:
            root = owner.entity
        entity = entities.get(root)
        if entity is None:
            return
        for key, value in where.items():
            path = f"$.{group}[{i}].where.{key}"
            if key == "state":
                if not workflow or workflow.entity != root or value not in workflow.states:
                    error(path, "invalid_filter_state", "State filters must reference this entity's workflow")
            elif key not in {field.name for field in entity.fields}:
                error(path, "unresolved_filter", "Filter must reference an existing field")
            elif isinstance(value, float) and not math.isfinite(value):
                error(path, "invalid_filter", "Filter values must be finite JSON literals")

    def excel_labels(values, path):
        options = list(dict.fromkeys(values))
        if any("," in item or '"' in item for item in options) or len(",".join(options)) > 253:
            error(path, "invalid_excel_label", "Dropdown labels must omit commas/quotes and fit 253 characters")

    def check_summary(metrics, path, *, root):
        for j, metric in enumerate(metrics):
            mbase = f"{path}[{j}]"
            if metric.kind == "groups":
                if not metric.source:
                    error(mbase + ".source", "unresolved_selector", "Group metrics need a source selector")
                else:
                    selector(metric.source, mbase + ".source", root=root)
            elif metric.kind == "state":
                if not workflow or not metric.state or metric.state not in wf_states:
                    error(mbase + ".state", "invalid_filter_state",
                          "State metrics must reference this entity's workflow")

    for i, view in enumerate(spec.views):
        base = f"$.views[{i}]"
        for j, role in enumerate(view.roles or []):
            resolves(role, f"{base}.roles[{j}]", roles, "role")
        for attribute in ("entity", "tree", "submit"):
            resolves(getattr(view, attribute, None), base + "." + attribute, entities, "entity")
        for j, name in enumerate(getattr(view, "entities", [])):
            resolves(name, f"{base}.entities[{j}]", entities, "entity")
        if isinstance(view, ReportView):
            for j, column in enumerate(view.columns or []):
                selector(column.source, f"{base}.columns[{j}].source", root=view.entity)
            check_where(view, "views", i, view.where)
            if view.workbook:
                wb, wbase = view.workbook, base + ".workbook"
                selector(wb.state_source, wbase + ".state_source", root=view.entity)
                for state in wb.state_labels:
                    if state not in wf_states:
                        error(wbase + ".state_labels." + state, "invalid_filter_state",
                              "State labels must reference this entity's workflow")
                for state in wb.state_colors:
                    if state not in wb.state_labels:
                        error(wbase + ".state_colors." + state, "unresolved_state",
                              "State colors must reference a declared state label")
                excel_labels(list(wb.state_labels.values()), wbase + ".state_labels")
                matrix, mbase = wb.matrix, wbase + ".matrix"
                selector(matrix.key, mbase + ".key", root=view.entity)
                for j, column in enumerate(matrix.columns):
                    selector(column.source, f"{mbase}.columns[{j}].source", root=view.entity)
                for state in matrix.state_labels:
                    if state not in wf_states:
                        error(mbase + ".state_labels." + state, "invalid_filter_state",
                              "State labels must reference this entity's workflow")
                excel_labels(list(matrix.state_labels.values()), mbase + ".state_labels")
                if matrix.count_value not in matrix.state_labels.values():
                    error(mbase + ".count_value", "invalid_matrix_count",
                          "Matrix count value must be a declared safe state label")
                check_summary(wb.summary.metrics, wbase + ".summary.metrics", root=view.entity)
                if wb.summary.per_person:
                    check_summary(wb.summary.per_person.metrics,
                                  wbase + ".summary.per_person.metrics", root=view.entity)
    view_names = set()
    for i, view in enumerate(spec.views):
        if view.name and view.name in view_names:
            error(f"$.views[{i}].name", "duplicate_identifier", "View names must be distinct")
        view_names.add(view.name)
    for i, role in enumerate(spec.roles):
        if role != "admin" and not any(view.roles is None or role in view.roles for view in spec.views):
            error(f"$.roles[{i}]", "missing_entry_view", "Every non-admin role needs an entry view")
    distinct([export.name for export in spec.exports], "$.exports")
    for i, export in enumerate(spec.exports):
        ebase = f"$.exports[{i}]"
        selector(export.from_, ebase + ".from", root=export.from_.split(".")[0])
        for j, column in enumerate(export.columns or []):
            selector(column.source, f"{ebase}.columns[{j}].source",
                     root=export.from_.split(".")[0], allow_value=True)
            if column.distinct_by:
                selector(column.distinct_by, f"{ebase}.columns[{j}].distinct_by",
                         root=export.from_.split(".")[0])
    for i, tile in enumerate(spec.dashboard):
        resolves(tile.count, f"$.dashboard[{i}].count", entities, "entity")
    for group, items in (("exports", spec.exports), ("dashboard", spec.dashboard)):
        for i, item in enumerate(items):
            check_where(item, group, i, item.where)
    return errors


def validate(document: str | bytes | dict) -> Spec:
    def fail(code, message):
        raise SpecValidationError([{"path": "$", "code": code, "message": message}])

    if isinstance(document, (str, bytes)):
        try:
            payload = document.encode("utf-8") if isinstance(document, str) else document
        except UnicodeError:
            fail("invalid_json", "Spec must be valid UTF-8 JSON")
        if len(payload) > MAX_BYTES:
            fail("spec_too_large", "Spec exceeds the 64 KB limit")
        try:
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError("Duplicate JSON key")
                    result[key] = value
                return result
            document = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
        except (ValueError, UnicodeError, RecursionError):
            fail("invalid_json", "Spec must be valid UTF-8 JSON with distinct keys and finite numbers")
    elif isinstance(document, dict):
        try:
            size = len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, UnicodeError, RecursionError):
            fail("invalid_json", "Spec must contain only JSON literal values")
        if size > MAX_BYTES:
            fail("spec_too_large", "Spec exceeds the 64 KB limit")
    else:
        fail("invalid_json", "Spec must be a JSON object")
    try:
        spec = Spec.model_validate(document)
    except ValidationError as exc:
        raise SpecValidationError([{"path": _path(item["loc"], document), "code": item["type"],
                                    "message": item["msg"]} for item in exc.errors(include_url=False)]) from None
    errors = _graph(spec)
    if errors:
        raise SpecValidationError(errors)
    return spec


def schema() -> dict:
    return Spec.model_json_schema(by_alias=True)


if __name__ == "__main__":
    # Regenerate from the models; test_spec.py checks the committed file for drift.
    path = Path(__file__).resolve().parent.parent / "spec" / "schema.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
