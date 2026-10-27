"""Deterministic, reviewed Postgres migrations generated only from validated specs.

Identifiers always use ``sql.Identifier``. DDL values remain parameters, bound
by Psycopg's ClientCursor (Postgres cannot bind DDL parameters server-side):
https://www.psycopg.org/psycopg3/docs/advanced/cursors.html#client-side-binding-cursors
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

from psycopg import ClientCursor, sql
from psycopg.types.json import Jsonb

from .spec import DOMAIN_RE, RecordEntity, ReferenceEntity, Spec, validate

LOCK_ID = 739311602
TYPES = {"text": "text", "enum": "text", "domain": "text", "email": "text", "url": "text",
         "int": "bigint", "number": "numeric", "bool": "boolean", "date": "date",
         "datetime": "timestamptz", "ref": "uuid", "uuid": "uuid"}
I, S, P = sql.Identifier, sql.SQL, sql.Placeholder


class MigrationError(ValueError):
    pass


class PlanMismatch(MigrationError):
    pass


class ConfirmationRequired(MigrationError):
    pass


@dataclass(frozen=True)
class Op:
    kind: str
    table: str
    classification: str
    field: str | None = None
    source: str | None = None
    before: dict | None = None
    after: dict | None = None


@dataclass
class Step:
    query: sql.Composable
    params: tuple = ()

    def public(self):
        return {"sql": self.query.as_string(), "parameters": list(self.params)}


@dataclass
class Plan:
    plan_hash: str
    spec: dict
    operations: list[dict]
    steps: list[Step]
    destructive_steps: list[Step]
    archive_tag: str

    def public(self):
        return {"plan_hash": self.plan_hash, "operations": self.operations,
                "sql": [step.public() for step in self.steps],
                "destructive_sql": [step.public() for step in self.destructive_steps],
                "backup_reminder": any(op["classification"] != "additive" for op in self.operations)}


def _join(*parts):
    # Callers supply only fixed SQL fragments and separately quoted identifiers.
    return sql.Composed([S(part) if isinstance(part, str) else part for part in parts])


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _spec(value):
    return validate(value.model_dump(mode="json", by_alias=True, exclude_unset=True) if isinstance(value, Spec) else value)


def _document(spec):
    return spec.model_dump(mode="json", by_alias=True, exclude_none=True)


def table_name(name):
    return "x_" + name


def _constraint(kind, table, field):
    digest = hashlib.sha256((kind + ":" + table + ":" + field).encode()).hexdigest()[:24]
    return "ss_" + kind + "_" + digest


def archive_name(name, tag):
    prefix = "_archived_" + tag + "_"
    if len((prefix + name).encode()) <= 63:
        return prefix + name
    digest = hashlib.sha256(name.encode()).hexdigest()[:8]
    return prefix + name[:max(1, 63 - len(prefix) - 9)] + "_" + digest


def tables(spec):
    """Physical table shapes, including engine metadata and workflow collections."""
    if spec is None:
        return {}
    result = {}
    for entity in spec.entities:
        common = {
            "id": {"name": "id", "type": "uuid", "required": True, "engine_default": "uuid"},
            "created_at": {"name": "created_at", "type": "datetime", "required": True, "engine_default": "now"},
            "updated_at": {"name": "updated_at", "type": "datetime", "required": True, "engine_default": "now"},
            "sort": {"name": "sort", "type": "int", "required": True, "default": 0},
            "active": {"name": "active", "type": "bool", "required": True, "default": True},
        }
        fields = {field.name: field.model_dump(mode="json", by_alias=True, exclude_none=True) for field in entity.fields}
        if isinstance(entity, ReferenceEntity) and (entity.parent or entity.hierarchy):
            if entity.parent and entity.hierarchy:
                raise MigrationError("An entity cannot combine parent and self-hierarchy; use a separate ref field")
            fields["parent_id"] = {"name": "parent_id", "type": "ref", "to": entity.parent or entity.name,
                                   "required": bool(entity.parent)}
        if isinstance(entity, RecordEntity):
            fields["owner_id"] = {"name": "owner_id", "type": "ref", "to": "users", "required": True}
        result[entity.name] = {"columns": common | fields, "renamed_from": entity.renamed_from,
                               "max_depth": entity.hierarchy.max_depth if isinstance(entity, ReferenceEntity) and entity.hierarchy else None}
        if isinstance(entity, RecordEntity) and entity.lines:
            line_name = entity.name + "_lines"
            result[line_name] = {"columns": {
                "id": {"name": "id", "type": "uuid", "required": True, "engine_default": "uuid"},
                "record_id": {"name": "record_id", "type": "ref", "to": entity.name, "required": True},
                "ref_id": {"name": "ref_id", "type": "ref", "to": entity.lines.entity_ref, "required": True},
                "sort": {"name": "sort", "type": "int", "required": True, "default": 0},
                "updated_at": {"name": "updated_at", "type": "datetime", "required": True, "engine_default": "now"},
            }, "renamed_from": entity.renamed_from + "_lines" if entity.renamed_from else None}
    if len(result) != len(spec.entities) + sum(bool(getattr(e, "lines", None)) for e in spec.entities):
        raise MigrationError("An entity name collides with a generated line table")
    if spec.workflow:
        wf = spec.workflow
        target = wf.entity + ("_lines" if wf.level == "line" else "")
        columns = result[target]["columns"]
        declared = {field.name for entity in spec.entities if entity.name == wf.entity for field in entity.fields}
        columns["state"] = {"name": "state", "type": "enum", "values": wf.states,
                            "required": True, "default": wf.initial}
        for transition in wf.transitions:
            for name in (transition.collects, transition.requires):
                if name and name not in columns and name not in declared:
                    columns[name] = {"name": name, "type": "text", "default": ""}
    return result


def _type(field):
    kind = field["type"]
    if kind.startswith("list<"):
        return S(TYPES[kind[5:-1]] + "[]")
    return S(TYPES[kind])


def _predicate(field, value):
    clauses, params = [], []
    measure = _join("char_length(", value, ")") if field["type"] == "text" else value
    for bound, operator in (("min", ">="), ("max", "<=")):
        if field.get(bound) is not None:
            clauses.append(_join(measure, " " + operator + " ", P()))
            params.append(field[bound])
    if field["type"] == "enum":
        clauses.append(_join(value, " = any(", P(), ")"))
        params.append(field["values"])
    if field["type"] == "list<enum>":
        clauses.append(_join(value, " <@ ", P(), "::text[]"))
        params.append(field["values"])
    regex = {"domain": DOMAIN_RE.pattern, "email": r"^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$",
             "url": r"^https?://[^/[:space:]]+"}.get(field["type"])
    if regex:
        clauses.append(_join(value, " ~ ", P()))
        params.append(regex)
    return (S(" and ").join(clauses), tuple(params)) if clauses else (None, ())


def _kinds(field):
    result = ["pk"] if field["name"] == "id" else []
    predicate, _ = _predicate(field, I(field["name"]))
    if predicate is not None:
        result.append("ck")
    if field.get("unique"):
        result.append("uq")
    if field["type"] == "ref" and field.get("to"):
        result.append("fk")
    return result


def _column(field, *, nullable=False):
    parts, params = [I(field["name"]), S(" "), _type(field)], []
    if field.get("engine_default"):
        parts += [S(" default "), S({"uuid": "gen_random_uuid()", "now": "now()"}[field["engine_default"]])]
    elif "default" in field:
        parts += [S(" default "), P(), S("::"), _type(field)]
        params.append(field["default"])
    if field.get("required") and not nullable:
        parts.append(S(" not null"))
    return sql.Composed(parts), tuple(params)


def _add_constraints(table, field):
    target, column = I(table_name(table)), I(field["name"])
    steps = []
    for kind in _kinds(field):
        params = ()
        if kind == "pk":
            body = _join("primary key (", column, ")")
        elif kind == "uq":
            body = _join("unique (", column, ")")
        elif kind == "fk":
            referenced = "users" if field["to"] == "users" else table_name(field["to"])
            body = _join("foreign key (", column, ") references ", I(referenced), " (", I("id"), ")")
        else:
            predicate, params = _predicate(field, column)
            body = _join("check (", predicate, ")")
        steps.append(Step(_join("alter table ", target, " add constraint ",
                                I(_constraint(kind, table, field["name"])), " ", body), params))
    return steps


def _rename_constraints(old_table, new_table, before, after):
    return [Step(_join("alter table ", I(table_name(new_table)), " rename constraint ",
                       I(_constraint(kind, old_table, before["name"])), " to ",
                       I(_constraint(kind, new_table, after["name"])))) for kind in _kinds(before)]


def _classification(before, after):
    if before["type"] != after["type"] or before.get("to") != after.get("to"):
        return "risky"
    if after.get("required") and not before.get("required") and after.get("default") is None:
        return "risky"
    if after.get("unique") and not before.get("unique"):
        return "risky"
    if before["type"] in ("enum", "list<enum>") and not set(before["values"]) <= set(after["values"]):
        return "risky"
    for bound in ("min", "max"):
        old, new = before.get(bound), after.get(bound)
        if new is not None and (old is None or (new > old if bound == "min" else new < old)):
            return "risky"
    return "additive"


def _match(before, after):
    result, used = {}, set()
    for name, item in after.items():
        hint = item.get("renamed_from")
        if name in before:
            if hint and hint != name and hint in before:
                raise MigrationError("Rename target already exists: " + name)
            old = name
        elif hint:
            if hint not in before:
                raise MigrationError("Rename source does not exist: " + hint)
            old = hint
        else:
            old = None
        if old is not None:
            if old in used:
                raise MigrationError("Rename source is used more than once: " + old)
            used.add(old)
        result[name] = old
    return result, used


def _semantic(field):
    return {key: value for key, value in field.items() if key not in {"name", "renamed_from", "label"}}


def diff(old_spec, new_spec):
    old = _spec(old_spec) if old_spec is not None else None
    new = _spec(new_spec)
    before, after = tables(old), tables(new)
    matched, used = _match(before, after)
    ops = []
    for name, shape in after.items():
        previous = matched[name]
        if previous is None:
            ops.append(Op("create_table", name, "additive", after=shape))
            continue
        if previous != name:
            ops.append(Op("rename_table", name, "additive", source=previous, before=before[previous], after=shape))
        old_fields, new_fields = before[previous]["columns"], shape["columns"]
        fields, claimed = _match(old_fields, new_fields)
        for field_name, field in new_fields.items():
            old_name = fields[field_name]
            if old_name is None:
                risky = field.get("unique") or field.get("required") and field.get("default") is None
                ops.append(Op("add_column", name, "risky" if risky else "additive", field_name, after=field))
                continue
            prior = old_fields[old_name]
            if old_name != field_name:
                ops.append(Op("rename_column", name, "additive", field_name, source=old_name, before=prior, after=field))
            if _semantic(prior) != _semantic(field):
                ops.append(Op("alter_column", name, _classification(prior, field), field_name,
                              source=old_name, before=prior, after=field))
        for field_name, field in old_fields.items():
            if field_name not in claimed:
                ops.append(Op("drop_column", name, "destructive", field_name, before=field))
        old_depth, new_depth = before[previous].get("max_depth"), shape.get("max_depth")
        if new_depth is not None and old_depth is not None and new_depth < old_depth:
            ops.append(Op("narrow_hierarchy", name, "risky", before={"max_depth": old_depth}, after={"max_depth": new_depth}))
    for name in reversed(before):
        if name not in used:
            ops.append(Op("drop_table", name, "destructive", before=before[name]))
    # Renames must precede new tables whose foreign keys can point at renamed ones.
    order = {"rename_table": 0, "create_table": 1, "rename_column": 2,
             "add_column": 3, "alter_column": 4, "narrow_hierarchy": 4, "drop_column": 5, "drop_table": 6}
    return sorted(ops, key=lambda op: order[op.kind])


def _render(ops, tag, *, destructive=False):
    steps, constraints, drops = [], [], []
    for op in ops:
        target = I(table_name(op.table))
        if op.kind == "create_table":
            columns, params = [], []
            for field in op.after["columns"].values():
                query, bound = _column(field)
                columns.append(query)
                params.extend(bound)
                constraints += _add_constraints(op.table, field)
            steps.append(Step(_join("create table ", target, " (", S(", ").join(columns), ")"), tuple(params)))
        elif op.kind == "rename_table":
            steps.append(Step(_join("alter table ", I(table_name(op.source)), " rename to ", target)))
            for field in op.before["columns"].values():
                steps += _rename_constraints(op.source, op.table, field, field)
        elif op.kind == "rename_column":
            steps.append(Step(_join("alter table ", target, " rename column ", I(op.source), " to ", I(op.field))))
            steps += _rename_constraints(op.table, op.table, op.before, op.after)
        elif op.kind == "add_column":
            query, params = _column(op.after)
            steps.append(Step(_join("alter table ", target, " add column ", query), params))
            constraints += _add_constraints(op.table, op.after)
        elif op.kind == "alter_column":
            before = dict(op.before, name=op.field)
            for kind in _kinds(before):
                steps.append(Step(_join("alter table ", target, " drop constraint ", I(_constraint(kind, op.table, op.field)))))
            column = I(op.field)
            steps.append(Step(_join("alter table ", target, " alter column ", column, " drop default")))
            if before["type"] != op.after["type"]:
                steps.append(Step(_join("alter table ", target, " alter column ", column, " type ", _type(op.after),
                                        " using ", column, "::text::", _type(op.after))))
            if "default" in op.after:
                steps.append(Step(_join("alter table ", target, " alter column ", column, " set default ", P(), "::", _type(op.after)), (op.after["default"],)))
                if op.after.get("required"):
                    steps.append(Step(_join("update ", target, " set ", column, " = ", P(), "::", _type(op.after), " where ", column, " is null"), (op.after["default"],)))
            steps.append(Step(_join("alter table ", target, " alter column ", column,
                                    " set not null" if op.after.get("required") else " drop not null")))
            constraints += _add_constraints(op.table, op.after)
        elif op.kind == "drop_column":
            if destructive:
                steps.append(Step(_join("alter table ", target, " drop column ", I(op.field))))
            else:
                archived = archive_name(op.field, tag)
                steps.append(Step(_join("alter table ", target, " rename column ", I(op.field), " to ", I(archived))))
                # Archived data stays intact while future inserts may omit the column.
                steps.append(Step(_join("alter table ", target, " alter column ", I(archived), " drop not null")))
                steps.append(Step(_join("alter table ", target, " alter column ", I(archived), " drop default")))
                steps += _rename_constraints(op.table, op.table, op.before, dict(op.before, name=archived))
        elif op.kind == "drop_table":
            if destructive:
                drops.append(target)
            else:
                archived = archive_name(table_name(op.table), tag)
                steps.append(Step(_join("alter table ", target, " rename to ", I(archived))))
                # Free constraint/index names so the same logical entity can be added later.
                for field in op.before["columns"].values():
                    for kind in _kinds(field):
                        steps.append(Step(_join("alter table ", I(archived), " rename constraint ",
                                                I(_constraint(kind, op.table, field["name"])), " to ",
                                                I(_constraint(kind, archived, field["name"])))))
    # Every referenced table/primary key exists before any foreign key is added.
    primary = [step for step in constraints if " primary key " in step.query.as_string()]
    secondary = [step for step in constraints if " primary key " not in step.query.as_string()]
    if drops:
        steps.append(Step(_join("drop table ", S(", ").join(drops))))
    return steps + primary + secondary


def active(conn):
    return conn.execute("select id, created_at, spec from ss_spec where active").fetchone()


def _impact(conn, op, before_tables, matched):
    old_name = matched.get(op.table)
    if op.kind == "create_table":
        return {"rows": 0, "violating_rows": 0}
    if conn is None:
        return {"rows": None, "violating_rows": None}
    source = op.table if op.kind == "drop_table" else old_name or op.table
    target = I(table_name(source))
    rows = conn.execute(_join("select count(*) as n from ", target)).fetchone()["n"]
    if op.kind == "narrow_hierarchy":
        query = _join(
            "with recursive ancestry as (select id as root, id, parent_id, array[id] as path, 1 as depth, false as cycle from ",
            target, " union all select a.root, p.id, p.parent_id, a.path || p.id, a.depth + 1, p.id = any(a.path) ",
            "from ancestry a join ", target, " p on p.id = a.parent_id where not a.cycle) ",
            "select count(distinct root) filter (where depth > ", P(), " or cycle) as n from ancestry")
        violations = conn.execute(query, (op.after["max_depth"],)).fetchone()["n"]
        return {"rows": rows, "violating_rows": violations}
    if op.kind not in {"add_column", "alter_column"}:
        return {"rows": rows, "violating_rows": 0}
    field = op.after
    if op.kind == "add_column":
        violations = rows if field.get("required") and field.get("default") is None else 0
        if field.get("unique") and field.get("default") is not None and rows > 1:
            violations = rows
        return {"rows": rows, "violating_rows": violations}
    original = I(op.source or op.field)
    cast_params = ()
    if op.before["type"] != field["type"]:
        type_name = _type(field).as_string()
        valid = _join("pg_input_is_valid(", original, "::text, ", P(), ")")
        cast_params = (type_name,)
        value = _join("case when ", valid, " then ", original, "::text::", _type(field), " else null end")
    else:
        valid, value = S("true"), original
    # Default backfill changes the values checked by uniqueness and constraints.
    params_value = cast_params
    if field.get("required") and field.get("default") is not None:
        value = _join("coalesce(", value, ", ", P(), "::", _type(field), ")")
        params_value += (field["default"],)
    conditions, params = [], []
    if field.get("required") and field.get("default") is None:
        conditions.append(_join(original, " is null"))
    if op.before["type"] != field["type"]:
        conditions.append(_join("(", original, " is not null and not ", valid, ")"))
        params.extend(cast_params)
    predicate, predicate_params = _predicate(field, value)
    if predicate is not None:
        # _predicate may repeat its value expression for min/max/enum checks.
        expanded = []
        for parameter in predicate_params:
            expanded.extend(params_value)
            expanded.append(parameter)
        conditions.append(_join("not (", predicate, ")"))
        params.extend(expanded)
    if field.get("unique"):
        conditions.append(_join(value, " in (select ", value, " from ", target, " group by ", value, " having count(*) > 1)"))
        params.extend(params_value * 3)
    if field["type"] == "ref" and field.get("to"):
        reference = matched.get(field["to"]) or field["to"]
        ref = "users" if reference == "users" else table_name(reference)
        exists = conn.execute("select to_regclass(%s) as name", (ref,)).fetchone()["name"]
        if exists:
            conditions.append(_join(value, " not in (select ", I("id"), " from ", I(ref), ")"))
        else:
            conditions.append(_join(value, " is not null"))
        params.extend(params_value)
    violations = conn.execute(_join("select count(*) as n from ", target, " where ",
                                    S(" or ").join(conditions)), tuple(params)).fetchone()["n"] if conditions else 0
    return {"rows": rows, "violating_rows": violations}


def plan(conn, new_spec, *, old_spec=None, archive_tag=None):
    new = _spec(new_spec)
    stored = active(conn) if conn is not None else None
    old = _spec(stored["spec"]) if stored else (_spec(old_spec) if old_spec is not None else None)
    # Use the active version's stable timestamp/id, never the current clock: a
    # re-plan at apply time must produce exactly the reviewed archive names.
    tag = archive_tag or (stored["created_at"].strftime("%Y%m%d%H%M%S") + str(stored["id"]) if stored else "initial")
    ops = diff(old, new)
    old_tables, new_tables = tables(old), tables(new)
    matched, _ = _match(old_tables, new_tables)
    operations = [asdict(op) | _impact(conn, op, old_tables, matched) for op in ops]
    steps, destructive = _render(ops, tag), _render(ops, tag, destructive=True)
    payload = {"before": _document(old) if old else None, "after": _document(new), "archive_tag": tag,
               "operations": operations, "sql": [step.public() for step in steps],
               "destructive_sql": [step.public() for step in destructive]}
    digest = hashlib.sha256(_canonical(payload).encode()).hexdigest()
    return Plan(digest, _document(new), operations, steps, destructive, tag)


def apply(conn, new_spec, plan_hash, *, confirm_risky=False, confirm_destructive=None):
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(%s)", (LOCK_ID,))
        reviewed = plan(conn, new_spec)
        if reviewed.plan_hash != plan_hash:
            raise PlanMismatch("Plan changed; review a fresh migration plan before applying")
        if confirm_destructive is not None and confirm_destructive != plan_hash:
            raise ConfirmationRequired("Destructive confirmation must equal the reviewed plan hash")
        if any(op["classification"] == "risky" for op in reviewed.operations) and not confirm_risky:
            raise ConfirmationRequired("Risky changes require explicit confirmation and a backup")
        if any(op["violating_rows"] for op in reviewed.operations):
            raise MigrationError("Existing rows violate the proposed schema; correct them before applying")
        chosen = reviewed.destructive_steps if confirm_destructive else reviewed.steps
        with ClientCursor(conn) as cursor:
            for step in chosen:
                cursor.execute(step.query, step.params)
        conn.execute("update ss_spec set active = false where active")
        conn.execute("insert into ss_spec (spec, spec_hash) values (%s,%s)", (Jsonb(reviewed.spec), plan_hash))
        conn.execute("insert into ss_migrations (spec_hash, ops, sql) values (%s,%s,%s)",
                     (plan_hash, Jsonb(reviewed.operations), Jsonb([step.public() for step in chosen])))
    return reviewed
