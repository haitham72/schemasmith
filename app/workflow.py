"""Role guards and transactional spec-defined workflow execution.

``collects`` identifies an answer the target state requests; entering that state
does not require the answer. ``requires`` requires a fresh payload value. Declared
record fields live on the header; implicit collected fields live at workflow level.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json

from fastapi import HTTPException
from psycopg import sql

from . import records
from .migrate import table_name
from .spec import RecordEntity

I, S, P = sql.Identifier, sql.SQL, sql.Placeholder


def _get(actor, name, default=None):
    return actor.get(name, default) if isinstance(actor, dict) else getattr(actor, name, default)


def roles_for(actor, spec=None) -> set[str]:
    if not actor:
        return set()
    roles = set(_get(actor, "roles", None) or [])
    if _get(actor, "is_admin", False):
        roles.add("admin")
    if _get(actor, "is_reviewer", False):
        roles.add("reviewer")
    # Compatibility for ordinary accounts predating the roles column. Privileged
    # accounts do not gain owner-only transitions through this fallback.
    if not roles and spec:
        roles.update(e.owner for e in spec.entities if isinstance(e, RecordEntity) and e.owner)
    return roles.intersection(spec.roles) if spec else roles


def require_role(actor, *roles, spec=None):
    """Guard an already resolved actor; returns the actor for route composition."""
    if not actor:
        raise HTTPException(401, "Sign in first.")
    if _get(actor, "status", "approved") != "approved":
        raise HTTPException(403, "Your account is waiting for admin approval.")
    if roles and not roles_for(actor, spec).intersection(roles):
        raise HTTPException(403, "Your role cannot perform this action.")
    return actor


def rollup(workflow, states) -> str:
    """Generalize the source status_of: undecided-only stays pending."""
    states = list(states)
    rule = workflow.rollup
    if rule is None:
        return states[0] if states and len(set(states)) == 1 else workflow.initial
    if not any(state in rule.all for state in states):
        return rule.none_decided
    if len(set(states)) == 1 and states[0] in rule.all:
        return rule.all[states[0]]
    return rule.mixed


def workflow_for(spec, entity):
    entity = records.entity_of(spec, entity) if isinstance(entity, str) else entity
    if not spec.workflow or spec.workflow.entity != entity.name:
        raise HTTPException(422, "This entity has no workflow.")
    return entity, spec.workflow


def select_transition(spec, entity, state, to_state, actor, owner_id):
    """Resolve an edge by graph, exact roles and (for owner roles) ownership."""
    require_role(actor, spec=spec)
    entity, workflow = workflow_for(spec, entity)
    edges = [edge for edge in workflow.transitions if state in edge.from_ and edge.to == to_state]
    if not edges:
        raise HTTPException(409, "This transition is not available from the current state.")
    roles = roles_for(actor, spec)
    for edge in edges:
        allowed = roles.intersection(edge.roles)
        if entity.owner in allowed and str(owner_id) != str(_get(actor, "id")):
            allowed.discard(entity.owner)
        if allowed:
            return edge
    raise HTTPException(403, "Your role or ownership does not permit this transition.")


def transition_values(entity, edge, data):
    if data is None:
        data = {}
    allowed = {name for name in (edge.collects, edge.requires) if name}
    if not isinstance(data, dict) or set(data) - allowed:
        raise HTTPException(422, "Supply only fields declared by this transition.")
    fields = {field.name: field for field in entity.fields}
    values = {}
    for name, value in data.items():
        value = value.strip() if isinstance(value, str) else value
        if name in fields:
            # Reuse the full record validator, including finite numbers, bounds,
            # nullable/required semantics and timezone-aware datetime parsing.
            value = records.values_for(entity, {name: value}, patch=True)[name]
        elif not isinstance(value, str) or len(value) > 2000:
            raise HTTPException(422, "Collected text must be at most 2000 characters.")
        values[name] = value.strip() if isinstance(value, str) else value
    if edge.requires:
        value = values.get(edge.requires)
        if value is None or value == "" or value == []:
            raise HTTPException(422, "A value is required for " + edge.requires + ".")
    return values


def enforce_daily_limit(conn, spec, entity, actor):
    """Call INSIDE the creation transaction, before insert (including for admins).

    A transaction advisory lock serializes submissions for this entity/user.
    Counts include archived records, using UTC calendar days. READ COMMITTED is
    required so a waiter sees the previous holder's commit before counting.
    """
    entity = records.entity_of(spec, entity) if isinstance(entity, str) else entity
    workflow = spec.workflow
    if not workflow or workflow.entity != entity.name or not workflow.limits:
        return
    require_role(actor, spec=spec)
    owner_id = records._uuid(_get(actor, "id"))
    key = "schemasmith:daily:" + entity.name + ":" + str(owner_id)
    lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
    conn.execute("select pg_advisory_xact_lock(%s)", (lock,))
    day = "date_trunc('day', now() at time zone 'UTC')"
    count = conn.execute(records.query(
        "select count(*) as n from ", I(table_name(entity.name)), " where ",
        I("owner_id"), " = %s and ", I("created_at"), " >= (", day, ") at time zone 'UTC'",
        " and ", I("created_at"), " < (", day, " + interval '1 day') at time zone 'UTC'"), (owner_id,)).fetchone()["n"]
    if count >= workflow.limits.per_user_per_day:
        raise HTTPException(429, "Daily submission limit reached.")


def _update(conn, table, identifier, values):
    sets = [records.query(I(name), " = ", P()) for name in values]
    sets.append(records.query(I("updated_at"), " = now()"))
    return conn.execute(records.query("update ", I(table), " set ", S(", ").join(sets),
                                      " where ", I("id"), " = %s returning *"),
                        (*values.values(), identifier)).fetchone()


def _moment(value):
    """Best-effort created_at parse; None when the event carries no usable time."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _latest(events):
    """Newest event by timestamp then id; event rows are plain dicts."""
    def key(event):
        moment = _moment(event.get("created_at"))
        return (moment.isoformat() if moment else "", event.get("id") or 0)
    return max(events, key=key)


def fetch_events(conn, spec, entity, record_id):
    """All workflow events for one record, oldest first. Adapters resolve them
    with the pure helpers below; callers outside a transaction see commits only."""
    entity = records.entity_of(spec, entity) if isinstance(entity, str) else entity
    return [dict(row) for row in conn.execute(
        records.query("select * from ", I("ss_workflow_events"),
                      " where ", I("entity"), " = %s and ", I("record_id"), " = %s",
                      " order by ", I("created_at"), ", ", I("id")),
        (entity.name, records._uuid(record_id))).fetchall()]


def stable_justified_at(events):
    """Earliest justification supply wins; later transitions never move it.

    Fixes the WP4 adapter compromise that derived justified_at from
    line.updated_at, which drifted on every subsequent decision.
    """
    moments = [_moment(event.get("created_at")) for event in events
               if isinstance((event.get("data") or {}).get("justification"), str)
               and (event.get("data") or {})["justification"].strip()]
    moments = [moment for moment in moments if moment]
    return min(moments) if moments else None


def decision_attribution(events):
    """Latest transition supplies the decision actor/time.

    Fixes the WP4 adapter compromise that returned decided_at/decided_by as
    null. None while no transition has moved the record.
    """
    moves = [event for event in events
             if event.get("kind") == "transition" and event.get("from_state") != event.get("to_state")]
    if not moves:
        return None
    latest = _latest(moves)
    return {"decided_at": latest.get("created_at"), "decided_by": latest.get("actor_email") or ""}


def visible_note(note_events, fallback=""):
    """Latest explicit note wins, including an explicit empty clear.

    Fixes the WP4 adapter compromise where an empty decision note could not
    clear an earlier note and thread chatter leaked into the admin note.
    """
    notes = [event for event in note_events if event.get("kind") == "note"]
    if not notes:
        return fallback
    return (_latest(notes).get("data") or {}).get("note", fallback)


def thread_ask_open(events):
    """A thread ask stays open until a later resolve, even on fully decided records.

    Fixes the WP4 compromise where asking on an all-decided record left no
    flag because only unresolved lines were transitioned. Per-line asks keep
    working through line states; this covers the thread-wide remainder.
    """
    marks = [event for event in events
             if event.get("kind") in ("ask", "resolve") and not event.get("line_id")]
    if not marks:
        return False
    return _latest(marks).get("kind") == "ask"


def _record_event(conn, *, entity, record_id, line_id, from_state, to_state, actor, data=None, kind="transition"):
    """Append one workflow event inside the caller's transaction."""
    try:
        payload = json.dumps(data or {}, default=str)
    except (TypeError, ValueError):
        payload = "{}"
    name = entity.name if not isinstance(entity, str) else entity
    conn.execute(records.query("insert into ", I("ss_workflow_events"),
                               " (entity, record_id, line_id, from_state, to_state,",
                               " actor_id, actor_email, data, kind)",
                               " values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)"),
                 (name, str(record_id), str(line_id) if line_id is not None else None,
                  from_state, to_state, str(_get(actor, "id", "") or ""),
                  str(_get(actor, "email", "") or ""), payload, kind))


def annotate(conn, spec, entity, record_id, actor, *, line_id=None, note=None, ask=None, resolve=False):
    """Note/ask annotations without a state change; empty notes clear on purpose.

    Runs inside the caller's transaction next to the comment or decision it
    records, so the event and the write it describes commit together.
    """
    require_role(actor, spec=spec)
    entity, wf = workflow_for(spec, entity)
    header = records.fetch(conn, entity, record_id)
    state, line_key = header.get("state", "") or "", None
    if wf.level == "line":
        if line_id is None:
            states = [line["state"] for line in records.hydrate(conn, spec, entity, header).get("lines", [])]
            state = rollup(wf, states)
        else:
            line = conn.execute(records.query("select * from ", I(table_name(entity.name + "_lines")),
                                              " where ", I("id"), " = %s and ",
                                              I("record_id"), " = %s"),
                                (records._uuid(line_id), header["id"])).fetchone()
            if line is None:
                raise HTTPException(404, "Line not found in this record.")
            state, line_key = line["state"], line["id"]
    data, kind = {"note": note or ""}, "note"
    if ask is not None:
        data, kind = {"ask": ask}, "ask"
    elif resolve:
        data, kind = {"resolve": True}, "resolve"
    with records.integrity(conn):
        _record_event(conn, entity=entity, record_id=header["id"], line_id=line_key,
                      from_state=state, to_state=state, actor=actor, data=data, kind=kind)
    return {"ok": True}


def transition(conn, spec, entity, record_id, actor, to_state, *, line_id=None, data=None):
    """Lock header then line, validate the graph, persist, return hydrated record.

    The caller's outer transaction controls final commit. The nested transaction
    rolls back all state/data changes together on any exception. The state move
    is also appended to ss_workflow_events so adapters can attribute stable
    justification times and decision actors without reading comments.
    """
    require_role(actor, spec=spec)
    entity, workflow = workflow_for(spec, entity)
    if (workflow.level == "line") != (line_id is not None):
        raise HTTPException(422, "Supply line_id exactly for line-level workflows.")
    with records.integrity(conn):
        header = conn.execute(records.query("select * from ", I(table_name(entity.name)),
                              " where ", I("id"), " = %s and ", I("active"), " for update"),
                              (records._uuid(record_id),)).fetchone()
        if header is None:
            raise HTTPException(404, "Record not found.")
        row = header
        target = table_name(entity.name)
        if workflow.level == "line":
            target = table_name(entity.name + "_lines")
            row = conn.execute(records.query("select * from ", I(target), " where ", I("id"),
                               " = %s and ", I("record_id"), " = %s for update"),
                               (records._uuid(line_id), header["id"])).fetchone()
            if row is None:
                raise HTTPException(404, "Line not found in this record.")
        from_state = row["state"]
        edge = select_transition(spec, entity, from_state, to_state, actor, header["owner_id"])
        values = transition_values(entity, edge, data)
        declared = {field.name for field in entity.fields}
        # Reference values use the same existence/archive checks as record CRUD.
        records._references(conn, spec, entity, {k: v for k, v in values.items() if k in declared})
        if workflow.level == "line":
            header_values = {k: v for k, v in values.items() if k in declared}
            line_values = {k: v for k, v in values.items() if k not in declared}
            _update(conn, target, row["id"], dict(line_values, state=to_state))
            header = _update(conn, table_name(entity.name), header["id"], header_values)
        else:
            header = _update(conn, target, header["id"], dict(values, state=to_state))
        _record_event(conn, entity=entity, record_id=header["id"],
                      line_id=(row["id"] if workflow.level == "line" else None),
                      from_state=from_state, to_state=to_state, actor=actor, data=values)
        result = records.hydrate(conn, spec, entity, header)
        if workflow.level == "line":
            result["state"] = rollup(workflow, [line["state"] for line in result["lines"]])
        return result
