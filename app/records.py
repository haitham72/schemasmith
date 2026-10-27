"""Spec-defined records, runtime field models, trees and reference-data CSV.

The API applies ownership/role guards; query construction only accepts fields
from the active spec. Spec strings never become SQL fragments.
"""
from __future__ import annotations

from contextlib import contextmanager
import csv
from datetime import date, datetime
import io
import json
import re
from typing import Annotated, Literal
import uuid

from fastapi import Body, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import (
    AfterValidator, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat,
    StrictInt, StrictStr, ValidationError, create_model,
)
from psycopg import errors as pgerr, sql

from .migrate import active, table_name
from .spec import DOMAIN_RE, RecordEntity, ReferenceEntity, _literal, validate

I, S, P = sql.Identifier, sql.SQL, sql.Placeholder


def query(*parts):
    return sql.Composed([S(part) if isinstance(part, str) else part for part in parts])


def current_spec(conn):
    stored = active(conn)
    if not stored:
        raise HTTPException(409, "Design or load an application spec first.")
    return validate(stored["spec"])


def entity_of(spec, name):
    for entity in spec.entities:
        if entity.name == name:
            return entity
    raise HTTPException(404, "Unknown entity.")


def norm_domains(value):
    """Retained source normalizer, now registered for list<domain> fields."""
    if isinstance(value, str):
        value = re.split(r"[\s,;]+", value)
    if value is not None and not isinstance(value, list):
        raise ValueError("Domains must be a list or separated text")
    out = []
    for domain in value or []:
        if not isinstance(domain, str):
            raise ValueError("A domain must be text")
        domain = domain.strip().lower()
        if not domain:
            continue
        domain = re.sub(r"^[a-z]+://", "", domain).split("/")[0].split(":")[0]
        if not DOMAIN_RE.fullmatch(domain):
            raise ValueError("Invalid domain: " + domain)
        if domain not in out:
            out.append(domain)
    return out


def _domain(value):
    if not isinstance(value, str):
        raise ValueError("A domain must be text")
    values = norm_domains(value)
    if len(values) != 1:
        raise ValueError("Enter exactly one domain")
    return values[0]


def _date(value):
    if type(value) is date:
        return value
    if not isinstance(value, str) or not _literal("date", value):
        raise ValueError("Use an ISO date (YYYY-MM-DD)")
    return date.fromisoformat(value)


def _datetime(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and _literal("datetime", value):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Use an ISO timestamp with timezone")
    if result.tzinfo is None:
        raise ValueError("Timestamp requires a timezone")
    return result


def _uuid(value):
    if not isinstance(value, (str, uuid.UUID)):
        raise ValueError("Reference must be a UUID")
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValueError("Reference must be a UUID") from None


def _typed_text(kind):
    def check(value):
        if not _literal(kind, value):
            raise ValueError("Invalid " + kind)
        return value
    return Annotated[StrictStr, AfterValidator(check)]


FIELDS = {
    "text": Annotated[StrictStr, BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)],
    "int": StrictInt, "number": StrictFloat, "bool": StrictBool,
    "date": Annotated[date, BeforeValidator(_date)],
    "datetime": Annotated[datetime, BeforeValidator(_datetime)],
    "domain": Annotated[StrictStr, BeforeValidator(_domain)],
    "email": _typed_text("email"), "url": _typed_text("url"),
    "ref": Annotated[uuid.UUID, BeforeValidator(_uuid)],
}


def field_type(field):
    kind = field.type
    base = kind[5:-1] if kind.startswith("list<") else kind
    item = Literal.__getitem__(tuple(field.values)) if base == "enum" else FIELDS[base]
    if kind.startswith("list<"):
        annotation = list[item]
        if base == "domain":
            annotation = Annotated[annotation, BeforeValidator(norm_domains)]
        return annotation
    return item


def record_model(entity, *, patch=False):
    fields = {}
    for field in entity.fields:
        annotation = field_type(field)
        if not field.required:
            annotation = annotation | None
        default = None if patch else (field.default if "default" in field.model_fields_set else (... if field.required else None))
        limits = {}
        if field.type == "text":
            limits = {"min_length": field.min if field.min is not None else (1 if field.required else 0)}
            if field.max is not None:
                limits["max_length"] = field.max
        elif field.type in ("int", "number"):
            limits = {key: value for key, value in (("ge", field.min), ("le", field.max)) if value is not None}
        fields[field.name] = (annotation, Field(default=default, **limits))
    fields["sort"] = (StrictInt, 0 if not patch else None)
    if isinstance(entity, ReferenceEntity) and (entity.hierarchy or entity.parent):
        fields["parent_id"] = (FIELDS["ref"], None if patch else ...) if entity.parent else (
            Annotated[uuid.UUID | None, BeforeValidator(lambda v: None if v is None else _uuid(v))], None)
    if isinstance(entity, RecordEntity) and entity.lines:
        annotation = list[FIELDS["ref"]]
        fields["lines"] = (annotation, Field(default=None if patch else ...,
                                            min_length=entity.lines.min, max_length=entity.lines.max))
    return create_model("Record_" + entity.name + ("_patch" if patch else ""),
                        __config__=ConfigDict(extra="forbid", strict=True, allow_inf_nan=False,
                                              validate_default=not patch), **fields)


def values_for(entity, data, *, patch=False):
    try:
        values = record_model(entity, patch=patch).model_validate(data).model_dump(exclude_unset=patch)
    except ValidationError as exc:
        raise HTTPException(422, [{"path": "$." + ".".join(str(p) for p in error["loc"]),
                                   "code": error["type"], "message": error["msg"]}
                                  for error in exc.errors(include_url=False)]) from None
    if isinstance(entity, RecordEntity) and entity.lines and "lines" in values:
        values["lines"] = list(dict.fromkeys(values["lines"]))
        if not entity.lines.min <= len(values["lines"]) <= entity.lines.max:
            raise HTTPException(422, "The number of distinct selections is outside the spec limits.")
    return values


def require_actor(actor):
    if not actor:
        raise HTTPException(401, "Sign in first.")
    if getattr(actor, "status", "approved") != "approved":
        raise HTTPException(403, "Your account is waiting for admin approval.")
    return actor


def is_admin(actor):
    return bool(actor and (getattr(actor, "is_admin", False) or "admin" in getattr(actor, "roles", [])))


def can_read_all(spec, entity, actor):
    if is_admin(actor):
        return True
    roles = set(getattr(actor, "roles", []) or [])
    if getattr(actor, "is_reviewer", False):
        roles.add("reviewer")
    return bool(actor and any(view.type in {"queue", "report"} and getattr(view, "entity", None) == entity.name
                             and (view.roles is None or roles.intersection(view.roles)) for view in spec.views))


def guard(entity, actor, *, row=None, write=False, spec=None):
    if isinstance(entity, ReferenceEntity) and not write:
        return
    require_actor(actor)
    if is_admin(actor):
        return
    if not write and spec and can_read_all(spec, entity, actor):
        return
    if isinstance(entity, ReferenceEntity):
        raise HTTPException(403, "Admins only.")
    if row is not None and str(row["owner_id"]) != str(actor.id):
        raise HTTPException(404, "Record not found.")


def list_records(conn, entity, *, owner_id=None, parent_id=None, q="", include_archived=False, limit=500):
    conditions, params = [], []
    if not include_archived:
        conditions.append(I("active"))
    if owner_id:
        conditions.append(query(I("owner_id"), " = ", P()))
        params.append(_uuid(owner_id))
    if parent_id:
        conditions.append(query(I("parent_id"), " = ", P()))
        params.append(_uuid(parent_id))
    if q:
        searchable = [query(I(field.name), " ilike ", P()) for field in entity.fields if field.type == "text"]
        if searchable:
            conditions.append(query("(", S(" or ").join(searchable), ")"))
            params.extend(["%" + q + "%"] * len(searchable))
    where = query(" where ", S(" and ").join(conditions)) if conditions else S("")
    statement = query("select * from ", I(table_name(entity.name)), where,
                      " order by ", I("sort"), ", ", I("created_at"), ", ", I("id"), " limit ", P())
    return conn.execute(statement, (*params, min(max(limit, 1), 5000))).fetchall()


def fetch(conn, entity, record_id, *, archived=False):
    statement = query("select * from ", I(table_name(entity.name)), " where ", I("id"), " = ", P())
    if not archived:
        statement += query(" and ", I("active"))
    row = conn.execute(statement, (_uuid(record_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "Record not found.")
    return row


def hydrate(conn, spec, entity, row):
    return hydrate_many(conn, spec, entity, [row])[0]


def hydrate_many(conn, spec, entity, rows):
    """Retain the source loader's batched joins rather than querying each row."""
    if not rows or not isinstance(entity, RecordEntity):
        return [dict(row) for row in rows]
    owners = {row["id"]: row for row in conn.execute("select id,name,email from users where id = any(%s)",
                                                       (list({row["owner_id"] for row in rows}),)).fetchall()}
    grouped = {}
    if entity.lines:
        lines = conn.execute(query("select * from ", I(table_name(entity.name + "_lines")),
                                   " where ", I("record_id"), " = any(", P(), ") order by ", I("sort"), ", ", I("id")),
                             ([row["id"] for row in rows],)).fetchall()
        references = {}
        if lines:
            references = {row["id"]: row for row in conn.execute(query("select * from ", I(table_name(entity.lines.entity_ref)),
                                                                      " where ", I("id"), " = any(", P(), ")"),
                                                                (list({line["ref_id"] for line in lines}),)).fetchall()}
        for line in lines:
            grouped.setdefault(line["record_id"], []).append(dict(line, ref=references[line["ref_id"]]))
    return [dict(row, owner=owners[row["owner_id"]], **({"lines": grouped.get(row["id"], [])} if entity.lines else {})) for row in rows]


def tree_map(conn, entity, *, archived=False):
    statement = query("select * from ", I(table_name(entity.name)))
    if not archived:
        statement += query(" where ", I("active"))
    return {row["id"]: row for row in conn.execute(statement).fetchall()}


def tree_path(rows, record_id, *, max_depth=None):
    """Generalized source path walker, with cycle/depth guards for untrusted moves."""
    path, seen, row = [], set(), rows.get(record_id)
    while row:
        if row["id"] in seen:
            raise HTTPException(400, "A record cannot be moved inside itself.")
        seen.add(row["id"])
        path.append(row)
        row = rows.get(row.get("parent_id"))
    if max_depth and len(path) > max_depth:
        raise HTTPException(400, "The move would exceed the hierarchy depth limit.")
    return path[::-1]


def descendants(rows, record_id):
    found, todo = set(), [record_id]
    while todo:
        current = todo.pop()
        if current in found:
            continue
        found.add(current)
        todo.extend(row["id"] for row in rows.values() if row.get("parent_id") == current)
    return found


def _references(conn, spec, entity, values, *, record_id=None):
    checks = []
    for field in entity.fields:
        if field.type in ("ref", "list<ref>") and values.get(field.name) is not None:
            ids = values[field.name] if field.type.startswith("list<") else [values[field.name]]
            checks.append((field.to, ids))
    if isinstance(entity, ReferenceEntity) and (entity.hierarchy or entity.parent) and values.get("parent_id"):
        checks.append((entity.parent or entity.name, [values["parent_id"]]))
    if isinstance(entity, RecordEntity) and entity.lines and "lines" in values:
        checks.append((entity.lines.entity_ref, values["lines"]))
    for name, ids in checks:
        ids = set(ids)
        if not ids:
            continue
        rows = conn.execute(query("select ", I("id"), " from ", I(table_name(name)),
                                  " where ", I("active"), " and ", I("id"), " = any(", P(), ") for share"), (list(ids),)).fetchall()
        if len(rows) != len(ids):
            raise HTTPException(400, "A referenced record is missing or archived.")
    if isinstance(entity, ReferenceEntity) and entity.hierarchy and "parent_id" in values:
        rows = tree_map(conn, entity)
        identifier = record_id or uuid.uuid4()
        rows[identifier] = dict(rows.get(identifier, {}), id=identifier, parent_id=values["parent_id"])
        for member in descendants(rows, identifier):
            tree_path(rows, member, max_depth=entity.hierarchy.max_depth)


@contextmanager
def integrity(conn):
    try:
        with conn.transaction():
            yield
    except pgerr.UniqueViolation:
        raise HTTPException(409, "A unique field already has that value.") from None
    except (pgerr.CheckViolation, pgerr.NotNullViolation, pgerr.ForeignKeyViolation, pgerr.DataError):
        raise HTTPException(422, "The record violates the active spec.") from None


def create(conn, spec, entity, data, actor):
    guard(entity, actor, write=True)
    values = values_for(entity, data)
    with integrity(conn):
        if isinstance(entity, RecordEntity):
            from .workflow import enforce_daily_limit, require_role
            if entity.owner:
                require_role(actor, entity.owner, "admin", spec=spec)
            enforce_daily_limit(conn, spec, entity, actor)
        _references(conn, spec, entity, values)
        lines = list(dict.fromkeys(values.pop("lines", [])))
        if isinstance(entity, RecordEntity):
            values["owner_id"] = _uuid(actor.id)
        columns = list(values)
        statement = query("insert into ", I(table_name(entity.name)), " (", S(", ").join(map(I, columns)),
                          ") values (", S(", ").join(P() for _ in columns), ") returning *")
        row = conn.execute(statement, tuple(values[name] for name in columns)).fetchone()
        if lines:
            with conn.cursor() as cursor:
                cursor.executemany(query("insert into ", I(table_name(entity.name + "_lines")),
                                         " (", I("record_id"), ", ", I("ref_id"), ", ", I("sort"), ") values (%s,%s,%s)"),
                                   [(row["id"], reference, index) for index, reference in enumerate(lines)])
    return hydrate(conn, spec, entity, row)


def update(conn, spec, entity, record_id, data, actor):
    row = fetch(conn, entity, record_id)
    guard(entity, actor, row=row, write=True)
    values = values_for(entity, data, patch=True)
    if "lines" in values:
        raise HTTPException(422, "Submit a new record to change its selected references.")
    _references(conn, spec, entity, values, record_id=row["id"])
    if not values:
        return hydrate(conn, spec, entity, row)
    columns = list(values)
    sets = [query(I(name), " = ", P()) for name in columns]
    with integrity(conn):
        row = conn.execute(query("update ", I(table_name(entity.name)), " set ", S(", ").join(sets),
                                 ", ", I("updated_at"), " = now() where ", I("id"), " = ", P(), " returning *"),
                           (*[values[name] for name in columns], row["id"])).fetchone()
    return hydrate(conn, spec, entity, row)


def archive(conn, spec, entity, record_id, actor):
    row = fetch(conn, entity, record_id)
    guard(entity, actor, row=row, write=True)
    ids = descendants(tree_map(conn, entity), row["id"]) if getattr(entity, "hierarchy", None) else {row["id"]}
    conn.execute(query("update ", I(table_name(entity.name)), " set ", I("active"), " = false where ",
                       I("id"), " = any(", P(), ")"), (list(ids),))
    for child in spec.entities:
        if isinstance(child, ReferenceEntity) and child.parent == entity.name:
            children = conn.execute(query("select ", I("id"), " from ", I(table_name(child.name)),
                                          " where ", I("active"), " and ", I("parent_id"), " = any(", P(), ")"), (list(ids),)).fetchall()
            for item in children:
                archive(conn, spec, child, item["id"], actor)
    return {"result": "archived", "count": len(ids)}


def reorder(conn, entity, identifiers, actor):
    if not is_admin(require_actor(actor)):
        raise HTTPException(403, "Admins only.")
    try:
        if not isinstance(identifiers, list):
            raise ValueError("IDs must be a list")
        ids = [_uuid(item) for item in identifiers]
    except ValueError:
        raise HTTPException(422, "Supply a list of UUID record IDs.") from None
    if not ids or len(ids) != len(set(ids)) or len(ids) > 500:
        raise HTTPException(422, "Supply 1–500 distinct record IDs.")
    parent = I("parent_id") if isinstance(entity, ReferenceEntity) and (entity.parent or entity.hierarchy) else S("null")
    rows = conn.execute(query("select ", I("id"), ", ", parent, " as parent from ", I(table_name(entity.name)),
                              " where ", I("active"), " and ", I("id"), " = any(", P(), ")"), (ids,)).fetchall()
    if len(rows) != len(ids):
        raise HTTPException(404, "Some records are missing or archived.")
    if len({row["parent"] for row in rows}) != 1:
        raise HTTPException(400, "Only siblings can be reordered together.")
    with conn.cursor() as cursor:
        cursor.executemany(query("update ", I(table_name(entity.name)), " set ", I("sort"), " = %s where ", I("id"), " = %s"),
                           [(index, identifier) for index, identifier in enumerate(ids)])
    return {"ok": True}


def export_csv(conn, entity):
    columns = ["id", *[field.name for field in entity.fields]]
    if isinstance(entity, ReferenceEntity) and (entity.hierarchy or entity.parent):
        columns.append("parent_id")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in list_records(conn, entity, limit=5000):
        writer.writerow([json.dumps(row[name], default=str, ensure_ascii=False) if isinstance(row[name], list)
                         else "" if row[name] is None else str(row[name]).lower() if isinstance(row[name], bool)
                         else row[name] for name in columns])
    return buffer.getvalue()


def import_csv(conn, spec, entity, text, actor, *, dry_run=False):
    guard(entity, actor, write=True)
    if not isinstance(entity, ReferenceEntity):
        raise HTTPException(422, "CSV import is available for reference entities.")
    if len(text.encode("utf-8")) > 2_000_000:
        raise HTTPException(413, "CSV exceeds the 2 MB limit.")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    allowed = {"id", "sort", *[field.name for field in entity.fields]}
    if entity.parent or entity.hierarchy:
        allowed.add("parent_id")
    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or not set(reader.fieldnames) <= allowed:
        raise HTTPException(422, "CSV contains missing, duplicate, or unknown columns.")
    stats = {"created": 0, "updated": 0, "errors": [], "dry_run": dry_run}
    with conn.transaction(force_rollback=dry_run):
        for number, raw in enumerate(reader, 2):
            try:
                with conn.transaction():
                    data = {name: value for name, value in raw.items() if name != "id" and value not in ("", None)}
                    for field in entity.fields:
                        if field.name not in data:
                            continue
                        value = data[field.name]
                        if field.type.startswith("list<"):
                            data[field.name] = norm_domains(value) if field.type == "list<domain>" and not value.startswith("[") else json.loads(value)
                        elif field.type == "bool":
                            if value.lower() not in {"true", "false", "yes", "no", "1", "0"}:
                                raise ValueError("Boolean must be true or false")
                            data[field.name] = value.lower() in {"true", "yes", "1"}
                        elif field.type == "int":
                            data[field.name] = int(value)
                        elif field.type == "number":
                            data[field.name] = float(value)
                    if "sort" in data:
                        data["sort"] = int(data["sort"])
                    if raw.get("id"):
                        update(conn, spec, entity, raw["id"], data, actor)
                        stats["updated"] += 1
                    else:
                        create(conn, spec, entity, data, actor)
                        stats["created"] += 1
            except (HTTPException, ValueError) as exc:
                stats["errors"].append({"row": number, "message": str(exc.detail if isinstance(exc, HTTPException) else exc)})
    return stats


def comments(conn, spec, entity, record_id, actor):
    row = fetch(conn, entity, record_id)
    guard(entity, actor, row=row, spec=spec)
    return conn.execute("select id, author_name, author_email, is_admin, body, created_at from comments "
                        "where entity = %s and record_id = %s order by created_at, id limit 200",
                        (entity.name, row["id"])).fetchall()


def add_comment(conn, spec, entity, record_id, actor, body):
    require_actor(actor)
    row = fetch(conn, entity, record_id)
    guard(entity, actor, row=row, spec=spec)
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= 2000:
        raise HTTPException(422, "Comments need 1–2000 characters.")
    return conn.execute("insert into comments (entity,record_id,author_name,author_email,is_admin,body) "
                        "values (%s,%s,%s,%s,%s,%s) returning id,author_name,author_email,is_admin,body,created_at",
                        (entity.name, row["id"], actor.name, actor.email, is_admin(actor), body.strip())).fetchone()


def mount(app, pool, who_dependency, who_optional):
    """Inject auth/pool to reuse the existing application and test real SQL in isolation."""
    @app.get("/api/e/{entity}/export.csv")
    def csv_export(entity: str, actor=Depends(who_dependency)):
        with pool.connection() as conn:
            spec = current_spec(conn)
            model = entity_of(spec, entity)
            if not is_admin(actor) or not isinstance(model, ReferenceEntity):
                raise HTTPException(403, "Admins may export reference data.")
            return Response(export_csv(conn, model), media_type="text/csv",
                            headers={"Content-Disposition": 'attachment; filename="' + entity + '.csv"'})

    @app.post("/api/e/{entity}/import.csv")
    def csv_import(entity: str, body: dict = Body(...), actor=Depends(who_dependency)):
        if set(body) - {"csv", "dry_run"} or not isinstance(body.get("csv"), str) or type(body.get("dry_run", False)) is not bool:
            raise HTTPException(422, "Supply csv text and an optional dry_run boolean.")
        with pool.connection() as conn:
            spec = current_spec(conn)
            return import_csv(conn, spec, entity_of(spec, entity), body["csv"], actor, dry_run=body.get("dry_run", False))

    @app.post("/api/e/{entity}/reorder")
    def api_reorder(entity: str, body: dict = Body(...), actor=Depends(who_dependency)):
        with pool.connection() as conn:
            return reorder(conn, entity_of(current_spec(conn), entity), body.get("ids", []), actor)

    @app.get("/api/e/{entity}")
    def api_list(entity: str, q: str = Query(default="", max_length=240), parent_id: uuid.UUID | None = None,
                 limit: int = Query(default=500, ge=1, le=500), actor=Depends(who_optional)):
        with pool.connection() as conn:
            spec = current_spec(conn)
            model = entity_of(spec, entity)
            guard(model, actor, spec=spec)
            owner = actor.id if isinstance(model, RecordEntity) and not can_read_all(spec, model, actor) else None
            if parent_id and not (isinstance(model, ReferenceEntity) and (model.parent or model.hierarchy)):
                raise HTTPException(422, "Parent filtering requires a reference entity.")
            rows = list_records(conn, model, owner_id=owner, parent_id=parent_id, q=q, limit=limit)
            return {"items": hydrate_many(conn, spec, model, rows), "total": len(rows)}

    @app.post("/api/e/{entity}", status_code=201)
    def api_create(entity: str, body: dict = Body(...), actor=Depends(who_dependency)):
        with pool.connection() as conn:
            spec = current_spec(conn)
            return create(conn, spec, entity_of(spec, entity), body, actor)

    @app.get("/api/e/{entity}/{record_id}")
    def api_get(entity: str, record_id: uuid.UUID, actor=Depends(who_optional)):
        with pool.connection() as conn:
            spec = current_spec(conn)
            model = entity_of(spec, entity)
            row = fetch(conn, model, record_id)
            guard(model, actor, row=row, spec=spec)
            return hydrate(conn, spec, model, row)

    @app.patch("/api/e/{entity}/{record_id}")
    def api_update(entity: str, record_id: uuid.UUID, body: dict = Body(...), actor=Depends(who_dependency)):
        with pool.connection() as conn:
            spec = current_spec(conn)
            return update(conn, spec, entity_of(spec, entity), record_id, body, actor)

    @app.delete("/api/e/{entity}/{record_id}")
    def api_archive(entity: str, record_id: uuid.UUID, actor=Depends(who_dependency)):
        with pool.connection() as conn:
            spec = current_spec(conn)
            return archive(conn, spec, entity_of(spec, entity), record_id, actor)

    @app.get("/api/e/{entity}/{record_id}/comments")
    def api_comments(entity: str, record_id: uuid.UUID, actor=Depends(who_dependency)):
        with pool.connection() as conn:
            spec = current_spec(conn)
            return comments(conn, spec, entity_of(spec, entity), record_id, actor)

    @app.post("/api/e/{entity}/{record_id}/comments", status_code=201)
    def api_comment(entity: str, record_id: uuid.UUID, body: dict = Body(...), actor=Depends(who_dependency)):
        if set(body) != {"body"}:
            raise HTTPException(422, "Supply the comment body.")
        with pool.connection() as conn:
            spec = current_spec(conn)
            return add_comment(conn, spec, entity_of(spec, entity), record_id, actor, body["body"])
