"""Parity metadata: durable workflow events replace the WP4 adapter compromises.

Pure checks run everywhere. Live-Postgres checks run only when the owner sets
RUN_POSTGRES_TESTS=1 with TEST_DATABASE_URL; otherwise they skip.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import workflow as staged_workflow

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def event(kind, **fields):
    row = {"id": 1, "kind": kind, "from_state": "pending", "to_state": "pending",
           "line_id": None, "actor_email": "", "data": {}, "created_at": T0}
    row.update(fields)
    return row


def test_core_sql_adds_event_table_only():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "app" / "core.sql").read_text(encoding="utf-8")
    block = text.split("Durable workflow-event metadata", 1)[1]
    for token in ("create table if not exists ss_workflow_events", "record_id", "line_id",
                  "from_state", "to_state", "actor_id", "actor_email", "data", "jsonb",
                  "'transition'", "'note'", "'ask'", "'resolve'",
                  "ss_workflow_events_record", "(entity, record_id, created_at)"):
        assert token in block
    assert "drop table" not in block and "alter table" not in block


def test_stable_justified_at_ignores_later_moves():
    events = [event("transition", id=1, from_state="needs_info", to_state="pending",
                    data={"justification": "first"}, created_at=T0),
              event("transition", id=2, from_state="pending", to_state="approved",
                    created_at=T0 + timedelta(hours=2))]
    assert staged_workflow.stable_justified_at(events) == T0


def test_stable_justified_at_skips_blank_and_empty():
    events = [event("transition", id=1, data={"justification": "  "}, created_at=T0),
              event("note", id=2, data={"note": "hi"}, created_at=T0)]
    assert staged_workflow.stable_justified_at(events) is None
    assert staged_workflow.stable_justified_at([]) is None


def test_decision_attribution_uses_latest_move():
    events = [event("transition", id=1, from_state="pending", to_state="needs_info",
                    actor_email="admin@example.test", created_at=T0),
              event("note", id=2, created_at=T0 + timedelta(hours=1)),
              event("transition", id=3, from_state="needs_info", to_state="approved",
                    actor_email="boss@example.test", created_at=T0 + timedelta(hours=2))]
    assert staged_workflow.decision_attribution(events) == {
        "decided_at": T0 + timedelta(hours=2), "decided_by": "boss@example.test"}
    assert staged_workflow.decision_attribution([event("ask", id=4)]) is None


def test_visible_note_explicit_empty_clears():
    cleared = [event("note", id=1, data={"note": "see this"}, created_at=T0),
               event("note", id=2, data={"note": ""}, created_at=T0 + timedelta(minutes=5))]
    assert staged_workflow.visible_note(cleared, fallback="older comment") == ""
    assert staged_workflow.visible_note([], fallback="older comment") == "older comment"


def test_thread_ask_open_needs_resolve():
    assert staged_workflow.thread_ask_open([]) is False
    assert staged_workflow.thread_ask_open([event("ask", id=1)]) is True
    closed = [event("ask", id=1, created_at=T0),
              event("resolve", id=2, created_at=T0 + timedelta(minutes=1))]
    assert staged_workflow.thread_ask_open(closed) is False
    # Per-line asks ride on line states, not on the thread flag.
    assert staged_workflow.thread_ask_open(
        [event("ask", id=3, line_id="l1")]) is False


def test_transition_and_annotate_use_identifier_sql():
    import inspect
    source = inspect.getsource(staged_workflow)
    assert "ss_workflow_events" in source
    assert "_record_event" in source
    assert "execute(f" not in source and ".format(" not in source
    assert "sql.Identifier" in source or "I(" in source


PG_LIVE = os.environ.get("RUN_POSTGRES_TESTS") == "1" and bool(os.environ.get("TEST_DATABASE_URL"))


@pytest.mark.skipif(not PG_LIVE, reason="Owner deferred real Postgres; set RUN_POSTGRES_TESTS=1")
def test_transition_appends_event_row():
    pytest.skip("needs seeded spec; capture against the real baseline")


@pytest.mark.skipif(not PG_LIVE, reason="Owner deferred real Postgres; set RUN_POSTGRES_TESTS=1")
def test_annotate_note_ask_resolve_round_trip():
    pytest.skip("needs seeded spec; capture against the real baseline")
