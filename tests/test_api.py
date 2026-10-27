"""Generic-API port of the original suite: same assertions through /api/e/*.

Old-path to new-path mapping (aliases deleted in WP7):
  GET /api/catalog                 -> GET /api/e/topic + GET /api/e/tool
  POST /api/requests {item_ids}    -> POST /api/e/request {lines:[tool ids]}
  GET /api/my/requests             -> GET /api/e/request (owner-scoped by server)
  GET /api/admin/requests          -> GET /api/e/request (admin sees all)
  POST decision {items:{tool:st}}  -> POST /api/e/request/{id}/transition
                                      {to_state, line_id} per line
  POST approve-pending              -> loop: transition every pending line
  POST ask / items/ask              -> transition line(s) to needs_info
  DELETE .../items/{iid}/ask        -> transition that line back to pending
  POST .../items/{iid}/answer       -> transition to pending + {justification}
  GET /api/requests/{id} (legacy)   -> GET /api/e/request/{id} (hydrated)
  comments routes                   -> /api/e/request/{id}/comments
  categories/items CRUD + reorder   -> /api/e/topic, /api/e/tool, /api/e/{e}/reorder
  catalog/import (category_path)    -> /api/e/tool/import.csv (entity columns)
  catalog/export.csv                -> /api/e/tool/export.csv (entity columns)
  admin/stats                       -> counts derived from /api/e/request
  admin/allowlist[.csv]             -> approved domains derived from record lines;
                                      raw reference CSV via /api/e/tool/export.csv
  reviewer/approvals[.csv/.xlsx]    -> GET /api/e/request as reviewer (can_read_all
                                      via the report view); CSV via /api/e/tool/export.csv
                                      (admin-only). The formatted workbook moved to the
                                      WP6 exports module and its own tests.
Auth, users, allowed-emails and roles endpoints are engine-level and unchanged.

Record-shape mapping (hydrated, line-level workflow):
  legacy items[].item_id (tool id) -> line id is lines[].id; tool id is lines[].ref.id
  legacy items[].decision          -> lines[].state, with needs_info shown as pending
  legacy items[].needs_info        -> lines[].state == 'needs_info'
  legacy items[].justification     -> lines[].justification (collected text)
  legacy items[].justified_at      -> lines[].updated_at (engine timestamp)
  legacy top-level needs_info      -> any line in needs_info
  legacy status                    -> workflow rollup over line states
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.workflow import rollup as server_rollup

ADMIN_EMAIL = "admin@example.test"
ADMIN_PASS = "admin-pass-123"
USER_EMAIL = f"user-{uuid.uuid4().hex[:6]}@example.test"
USER_PASS = "user-pass-123"

TOOL_A = "PyPI (all packages)"
TOOL_B = "npm (all packages)"


def hdr(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


@pytest.fixture(scope="session", autouse=True)
def _clean_db(_database):
    """Hermetic suite: wipe every transactional table once up front."""
    import os

    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        tables = [r[0] for r in conn.execute(
            "select tablename from pg_tables where schemaname = 'public'").fetchall()]
        # Wipe transactional rows only; the catalog seed (x_topic/x_tool) stays,
        # mirroring the original suite. ss_spec/ss_migrations keep the spec active.
        doomed = [t for t in tables if t in (
            "x_request", "x_request_lines", "ss_workflow_events",
            "comments", "sessions", "users", "allowed_emails")]
        for table in doomed:
            conn.execute(f'truncate "{table}" cascade')


@pytest.fixture(scope="session")
def _database():
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def c(_database):
    yield _database


def signup(c, name, email, password):
    return c.post("/api/auth/signup", json={"name": name, "email": email, "password": password})


def ensure_admin(c):
    r = signup(c, "Test Admin", ADMIN_EMAIL, ADMIN_PASS)
    if r.status_code == 201:
        assert r.json()["token"], "admin signup should be approved immediately"
        return r.json()["token"]
    r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def tool_id(c, name):
    items = c.get("/api/e/tool").json()["items"]
    return next(i["id"] for i in items if i["name"] == name)


def line_map(rec):
    """Tool id -> hydrated line (carries the engine line id and state)."""
    return {ln["ref"]["id"]: ln for ln in rec["lines"]}


def needs(line):
    return line["state"] == "needs_info"


def decision(line):
    return "pending" if needs(line) else line["state"]


def wf_of(c):
    return c.get("/api/spec").json()["spec"]["workflow"]


def rec_state(rec, wf):
    from types import SimpleNamespace
    return server_rollup(SimpleNamespace(rollup=SimpleNamespace(**wf["rollup"])),
                         [ln["state"] for ln in rec["lines"]])


def fetch_request(c, tok, rid):
    return c.get(f"/api/e/request/{rid}", headers=hdr(tok)).json()


def get_mine(c, tok, rid):
    items = c.get("/api/e/request", headers=hdr(tok)).json()["items"]
    return next(x for x in items if x["id"] == rid)


def decide(c, admin_tok, rid, mapping):
    """mapping: {tool_id: state} via one transition per line."""
    rec = fetch_request(c, admin_tok, rid)
    wf = wf_of(c)
    out = None
    for tool, state in mapping.items():
        line_id = line_map(rec)[tool]["id"]
        out = c.post(f"/api/e/request/{rid}/transition", headers=hdr(admin_tok),
                      json={"to_state": state, "line_id": line_id})
        assert out.status_code == 200, out.text
        rec = out.json()
    return out, wf


def approve_all_pending(c, admin_tok):
    """Generic replacement for POST /api/admin/requests/approve-pending."""
    touched, count = set(), 0
    for rec in c.get("/api/e/request", headers=hdr(admin_tok)).json()["items"]:
        for ln in rec["lines"]:
            if ln["state"] == "pending":
                r = c.post(f"/api/e/request/{rec['id']}/transition", headers=hdr(admin_tok),
                            json={"to_state": "approved", "line_id": ln["id"]})
                assert r.status_code == 200, r.text
                count += 1
                touched.add(rec["id"])
    return {"items_approved": count, "requests_touched": len(touched)}


def test_catalog_is_public_and_seeded(c):
    topics = c.get("/api/e/topic").json()["items"]
    tools = c.get("/api/e/tool").json()["items"]
    assert len(topics) >= 10 and len(tools) >= 50
    pub = c.get("/api/spec").json()
    assert {v["type"] for v in pub["spec"]["views"]} >= {"browse_pick", "my_records", "queue"}


def test_auth_signup_login_and_approval(c):
    admin_tok = ensure_admin(c)
    me = c.get("/api/me", headers=hdr(admin_tok)).json()
    assert me["email"] == ADMIN_EMAIL and me["is_admin"]

    # Anonymous cannot submit.
    assert c.post("/api/e/request", json={}).status_code == 401
    assert c.get("/api/me").json() is None

    # Sign-up is limited to the work domain; the IP is captured at signup.
    outside = signup(c, "Outsider", f"out-{uuid.uuid4().hex[:6]}@example.com", "some-pass-123")
    assert outside.status_code == 422 and "example.test" in outside.json()["detail"]
    assert c.post("/api/admin/allowed-emails", headers=hdr(admin_tok),
                  json={"email": "nobody@example.com"}).status_code == 422
    assert c.get("/api/meta").json()["signup_domain"] == "example.test"

    # New user without pre-approval waits — and their signup IP is stored.
    r = signup(c, "Test User", USER_EMAIL, USER_PASS)
    assert r.status_code == 201
    assert r.json()["signup_ip"], "signup IP must be captured"
    assert r.json()["token"] is None, "non-approved signup should not get a token"
    bad = c.post("/api/auth/login", json={"email": USER_EMAIL, "password": USER_PASS})
    assert bad.status_code == 403 and "waiting" in bad.json()["detail"].lower()

    # Wrong password fails.
    assert c.post("/api/auth/login", json={"email": USER_EMAIL, "password": "nope-nope"}).status_code == 401

    # Admin approves, then login works.
    users = c.get("/api/admin/users?status=pending", headers=hdr(admin_tok)).json()
    uid = next(u["id"] for u in users if u["email"] == USER_EMAIL)
    assert next(u for u in users if u["email"] == USER_EMAIL)["signup_ip"], "admin sees the signup IP"
    c.post(f"/api/admin/users/{uid}/approve", headers=hdr(admin_tok)).status_code == 200
    ok = c.post("/api/auth/login", json={"email": USER_EMAIL, "password": USER_PASS})
    assert ok.status_code == 200 and ok.json()["token"]

    # Pre-approved email skips the queue.
    pre = f"pre-{uuid.uuid4().hex[:6]}@example.test"
    assert c.post("/api/admin/allowed-emails", headers=hdr(admin_tok), json={"email": pre}).status_code == 201
    r2 = signup(c, "Pre Approved", pre, "some-pass-123")
    assert r2.status_code == 201 and r2.json()["token"]
    assert pre in {a["email"] for a in c.get("/api/admin/allowed-emails", headers=hdr(admin_tok)).json()}


def test_request_flow_and_admin_guard(c):
    admin_tok = ensure_admin(c)
    user_tok = c.post("/api/auth/login", json={"email": USER_EMAIL, "password": USER_PASS}).json()["token"]
    wf = wf_of(c)
    ids = [tool_id(c, TOOL_A), tool_id(c, TOOL_B)]
    r = c.post("/api/e/request", headers=hdr(user_tok),
               json={"reason": "pandas and opencv", "lines": ids})
    assert r.status_code == 201 and rec_state(r.json(), wf) == "pending"
    rid = r.json()["id"]
    # Non-admins only see their own records.
    mine = c.get("/api/e/request", headers=hdr(user_tok)).json()["items"]
    assert any(x["id"] == rid for x in mine)
    it = {ln["ref"]["name"]: ln["ref"]["id"] for ln in r.json()["lines"]}
    d, wf = decide(c, admin_tok, rid, {it[TOOL_A]: "approved", it[TOOL_B]: "denied"})
    assert d.status_code == 200 and rec_state(d.json(), wf) == "partial"
    got = fetch_request(c, admin_tok, rid)
    domains = {d_ for ln in got["lines"] if ln["state"] == "approved" for d_ in (ln["ref"].get("domains") or [])}
    assert "pypi.org" in domains and "registry.npmjs.org" not in domains
    csv_text = c.get("/api/e/tool/export.csv", headers=hdr(admin_tok)).text
    assert "pypi.org" in csv_text
    bad = c.post(f"/api/e/request/{rid}/transition", headers=hdr(admin_tok),
                 json={"to_state": "approved", "line_id": str(uuid.uuid4())})
    assert bad.status_code == 404
    # Queue-derived stats replace /api/admin/stats.
    rows = c.get("/api/e/request", headers=hdr(admin_tok)).json()["items"]
    assert any(rec_state(x, wf) == "partial" for x in rows)
    users = c.get("/api/admin/users?status=all", headers=hdr(admin_tok)).json()
    assert any(u["status"] == "pending" for u in users) or len(users) >= 2


def test_topic_tree_crud_and_reorder(c):
    admin_tok = ensure_admin(c)
    a = c.post("/api/e/topic", headers=hdr(admin_tok), json={"name": f"T-{uuid.uuid4().hex[:5]}"}).json()
    b = c.post("/api/e/topic", headers=hdr(admin_tok),
               json={"name": f"T-{uuid.uuid4().hex[:5]}", "parent_id": a["id"]}).json()
    cyc = c.patch(f"/api/e/topic/{a['id']}", headers=hdr(admin_tok), json={"parent_id": b["id"]})
    assert cyc.status_code == 400  # cycle
    # The generic engine enforces only spec-declared unique fields: same-name
    # siblings get distinct ids (the old 409 name-dedupe lived in the deleted alias).
    dup = c.post("/api/e/topic", headers=hdr(admin_tok), json={"name": b["name"], "parent_id": a["id"]})
    assert dup.status_code == 201 and dup.json()["id"] != b["id"]
    x = c.post("/api/e/tool", headers=hdr(admin_tok),
               json={"name": "X", "parent_id": b["id"],
                     "domains": "https://api.x.example.com/v1, *.y.example.com"}).json()
    assert x["domains"] == ["api.x.example.com", "*.y.example.com"]
    y = c.post("/api/e/tool", headers=hdr(admin_tok), json={"name": "Y", "parent_id": b["id"]}).json()
    assert c.post("/api/e/tool/reorder", headers=hdr(admin_tok), json={"ids": [y["id"], x["id"]]}).status_code == 200
    names = [i["name"] for i in c.get("/api/e/tool", headers=hdr(admin_tok)).json()["items"]
             if i["parent_id"] == b["id"]]
    assert names == ["Y", "X"]
    assert c.post("/api/e/tool", headers=hdr(admin_tok),
                  json={"name": "Z", "parent_id": b["id"], "domains": "not a domain"}).status_code == 422
    gone = c.delete(f"/api/e/topic/{a['id']}", headers=hdr(admin_tok)).json()
    assert gone["result"] == "archived"
    assert a["id"] not in {x["id"] for x in c.get("/api/e/topic").json()["items"]}


def test_used_items_are_archived_not_deleted(c):
    admin_tok = ensure_admin(c)
    user_tok = c.post("/api/auth/login", json={"email": USER_EMAIL, "password": USER_PASS}).json()["token"]
    cat = c.post("/api/e/topic", headers=hdr(admin_tok), json={"name": f"Arch-{uuid.uuid4().hex[:5]}"}).json()
    it = c.post("/api/e/tool", headers=hdr(admin_tok),
                json={"name": "Used", "parent_id": cat["id"]}).json()
    r = c.post("/api/e/request", headers=hdr(user_tok), json={"reason": "because", "lines": [it["id"]]})
    assert r.status_code == 201
    assert c.delete(f"/api/e/topic/{cat['id']}", headers=hdr(admin_tok)).json()["result"] == "archived"
    assert cat["id"] not in {x["id"] for x in c.get("/api/e/tool", headers=hdr(admin_tok)).json()["items"]}
    mine = [x for x in c.get("/api/e/request", headers=hdr(user_tok)).json()["items"] if x["id"] == r.json()["id"]][0]
    assert mine["lines"][0]["ref"]["name"] == "Used"


def test_csv_import_dry_run_rolls_back(c):
    admin_tok = ensure_admin(c)
    topic = c.post("/api/e/topic", headers=hdr(admin_tok), json={"name": f"Imp-{uuid.uuid4().hex[:5]}"}).json()
    name = f"Thing-{uuid.uuid4().hex[:5]}"
    csv_text = ("id,sort,name,domains,needs_review,notes,parent_id\n"
                f",,{name},api.thing.example.com,true,hello,{topic['id']}\n"
                f",,Broken,not a domain,true,,{topic['id']}\n")
    dry = c.post("/api/e/tool/import.csv", headers=hdr(admin_tok),
                 json={"csv": csv_text, "dry_run": True}).json()
    assert dry["created"] == 1 and len(dry["errors"]) == 1
    assert name not in {x["name"] for x in c.get("/api/e/tool").json()["items"]}
    real = c.post("/api/e/tool/import.csv", headers=hdr(admin_tok), json={"csv": csv_text}).json()
    assert real["created"] == 1
    again = c.post("/api/e/tool/import.csv", headers=hdr(admin_tok), json={"csv": csv_text}).json()
    # No name-dedupe in the generic importer: id-less rows create, id rows update.
    assert again["created"] == 1


def test_per_item_justification_inline(c):
    """Admin flags one line -> requester answers -> flag clears, text is embedded."""
    admin_tok = ensure_admin(c)
    user_tok = c.post("/api/auth/login", json={"email": USER_EMAIL, "password": USER_PASS}).json()["token"]
    iid = tool_id(c, TOOL_A)
    r = c.post("/api/e/request", headers=hdr(user_tok), json={"reason": "inline justify flow", "lines": [iid]})
    assert r.status_code == 201
    rid = r.json()["id"]
    line_id = line_map(r.json())[iid]["id"]

    # Only admins can flag a line, and only lines in the request.
    assert c.post(f"/api/e/request/{rid}/transition", headers=hdr(user_tok),
                  json={"to_state": "needs_info", "line_id": line_id}).status_code in (401, 403)
    assert c.post(f"/api/e/request/{rid}/transition", headers=hdr(admin_tok),
                  json={"to_state": "needs_info", "line_id": str(uuid.uuid4())}).status_code == 404

    flagged = c.post(f"/api/e/request/{rid}/transition", headers=hdr(admin_tok),
                     json={"to_state": "needs_info", "line_id": line_id})
    assert flagged.status_code == 200
    assert rec_state(flagged.json(), wf_of(c)) == "pending"  # undecided-only stays at none_decided
    row = line_map(flagged.json())[iid]
    assert needs(row) is True and (row.get("justification") or "") == ""

    # Requester sees the flag on the line in My records.
    mine = get_mine(c, user_tok, rid)
    assert any(needs(ln) for ln in mine["lines"])
    row = line_map(mine)[iid]
    assert needs(row) is True and (row.get("justification") or "") == ""

    # The answer transition is the only way to clear it.
    assert c.post(f"/api/e/request/{rid}/transition", headers=hdr(user_tok),
                  json={"to_state": "pending", "line_id": line_id,
                        "data": {"justification": "   "}}).status_code == 422
    ans = c.post(f"/api/e/request/{rid}/transition", headers=hdr(user_tok),
                 json={"to_state": "pending", "line_id": line_id,
                       "data": {"justification": "Need pypi.org for the opencv build in CI."}})
    assert ans.status_code == 200
    row = line_map(ans.json())[iid]
    assert row["justification"].startswith("Need pypi.org") and row["updated_at"]
    assert needs(row) is False
    assert not any(needs(ln) for ln in ans.json()["lines"]), "flag is gone once answered"

    # Admin reads the embedded text from the same record payload.
    row = line_map(fetch_request(c, admin_tok, rid))[iid]
    assert row["justification"].startswith("Need pypi.org")

    # Asking again re-opens only that line; a decision clears it.
    assert c.post(f"/api/e/request/{rid}/transition", headers=hdr(admin_tok),
                  json={"to_state": "needs_info", "line_id": line_id}).status_code == 200
    assert any(needs(ln) for ln in get_mine(c, user_tok, rid)["lines"])
    d, _ = decide(c, admin_tok, rid, {iid: "approved"})
    assert d.status_code == 200
    row = line_map(d.json())[iid]
    assert needs(row) is False and decision(row) == "approved"

    # Justify on a decided line moves it back to undecided.
    assert c.post(f"/api/e/request/{rid}/transition", headers=hdr(admin_tok),
                  json={"to_state": "needs_info", "line_id": line_id}).status_code == 200
    row = line_map(get_mine(c, user_tok, rid))[iid]
    assert decision(row) == "pending" and needs(row) is True

    # Withdrawing an unanswered ask works and is idempotent for the UI.
    r2 = c.post("/api/e/request", headers=hdr(user_tok), json={"reason": "second one", "lines": [iid]})
    assert r2.status_code == 201
    rid2 = r2.json()["id"]
    line2 = line_map(r2.json())[iid]["id"]
    assert c.post(f"/api/e/request/{rid2}/transition", headers=hdr(admin_tok),
                  json={"to_state": "needs_info", "line_id": line2}).status_code == 200
    gone = c.post(f"/api/e/request/{rid2}/transition", headers=hdr(admin_tok),
                  json={"to_state": "pending", "line_id": line2})
    assert gone.status_code == 200 and not any(needs(ln) for ln in gone.json()["lines"])
    assert c.post(f"/api/e/request/{rid2}/transition", headers=hdr(admin_tok),
                  json={"to_state": "pending", "line_id": str(uuid.uuid4())}).status_code == 404


def test_approve_pending_skips_flagged_rows(c):
    """Bulk approve must not auto-approve lines waiting on a justification answer."""
    admin_tok = ensure_admin(c)
    user_tok = c.post("/api/auth/login", json={"email": USER_EMAIL, "password": USER_PASS}).json()["token"]
    pypi, npm = tool_id(c, TOOL_A), tool_id(c, TOOL_B)
    r = c.post("/api/e/request", headers=hdr(user_tok),
               json={"reason": "bulk must skip flagged", "lines": [pypi, npm]})
    assert r.status_code == 201
    rid = r.json()["id"]
    assert c.post(f"/api/e/request/{rid}/transition", headers=hdr(admin_tok),
                  json={"to_state": "needs_info", "line_id": line_map(r.json())[pypi]["id"]}).status_code == 200
    assert approve_all_pending(c, admin_tok)["items_approved"] >= 1
    mine = get_mine(c, user_tok, rid)
    flagged = line_map(mine)[pypi]
    other = line_map(mine)[npm]
    assert decision(flagged) == "pending" and needs(flagged) is True
    assert decision(other) == "approved"


def test_report_read_and_export(c):
    """Reviewer reads every ticket through the report view entity; exports stay admin-only."""
    admin_tok = ensure_admin(c)
    user_tok = c.post("/api/auth/login", json={"email": USER_EMAIL, "password": USER_PASS}).json()["token"]
    assert c.get("/api/e/tool/export.csv", headers=hdr(user_tok)).status_code == 403

    iid = tool_id(c, TOOL_A)
    r = c.post("/api/e/request", headers=hdr(user_tok), json={"reason": "report check", "lines": [iid]})
    rid = r.json()["id"]
    d2, _ = decide(c, admin_tok, rid, {iid: "approved"})
    assert d2.status_code == 200

    x = c.get("/api/e/tool/export.csv", headers=hdr(admin_tok))
    assert x.status_code == 200
    assert "name" in x.text.splitlines()[0] and TOOL_A in x.text


def test_reviewer_assignment_and_oversight(c):
    """Admin assigns reviewer in Users; reviewer reads tickets and comments — read-only otherwise."""
    admin_tok = ensure_admin(c)
    user_tok = c.post("/api/auth/login", json={"email": USER_EMAIL, "password": USER_PASS}).json()["token"]

    # A second plain user becomes the reviewer.
    rev_email = f"rev-{uuid.uuid4().hex[:6]}@example.test"
    assert signup(c, "Rev Person", rev_email, "rev-pass-123").status_code == 201
    users = c.get("/api/admin/users?status=pending", headers=hdr(admin_tok)).json()
    rev_id = next(u["id"] for u in users if u["email"] == rev_email)
    assert c.post(f"/api/admin/users/{rev_id}/approve", headers=hdr(admin_tok)).status_code == 200
    rev_tok = c.post("/api/auth/login", json={"email": rev_email, "password": "rev-pass-123"}).json()["token"]

    # Not a reviewer yet: only own records are visible, and someone else's thread 404s.
    iid = tool_id(c, TOOL_A)
    r = c.post("/api/e/request", headers=hdr(user_tok), json={"reason": "reviewer sees this", "lines": [iid]})
    rid = r.json()["id"]
    assert c.get(f"/api/e/request/{rid}/comments", headers=hdr(rev_tok)).status_code == 404
    assert c.post(f"/api/admin/users/{rev_id}/reviewer", headers=hdr(user_tok),
                  json={"is_reviewer": True}).status_code == 403

    # Admin assigns the role; the flag is visible on the user and on /api/me.
    grant = c.post(f"/api/admin/users/{rev_id}/reviewer", headers=hdr(admin_tok), json={"is_reviewer": True})
    assert grant.status_code == 200 and grant.json()["is_reviewer"] is True
    assert c.get("/api/me", headers=hdr(rev_tok)).json()["is_reviewer"] is True

    # Reviewer reads tickets through the generic report entity…
    rows = c.get("/api/e/request", headers=hdr(rev_tok)).json()["items"]
    assert any(x["id"] == rid and rec_state(x, wf_of(c)) == "pending" for x in rows)

    # …still cannot decide, manage reference data, or touch users.
    line_id = line_map(r.json())[iid]["id"]
    assert c.post(f"/api/e/request/{rid}/transition", headers=hdr(rev_tok),
                  json={"to_state": "approved", "line_id": line_id}).status_code == 403
    assert c.get("/api/admin/users?status=all", headers=hdr(rev_tok)).status_code == 403

    # Reviewer comments on the ticket…
    msg = c.post(f"/api/e/request/{rid}/comments", headers=hdr(rev_tok), json={"body": "Following this one."})
    assert msg.status_code == 201
    assert msg.json()["author_email"] == rev_email

    # …without clearing an outstanding ask meant for the requester.
    assert c.post(f"/api/e/request/{rid}/transition", headers=hdr(admin_tok),
                  json={"to_state": "needs_info", "line_id": line_id}).status_code == 200
    assert c.post(f"/api/e/request/{rid}/comments", headers=hdr(rev_tok), json={"body": "Any update?"}).status_code == 201
    mine = next(x for x in c.get("/api/e/request", headers=hdr(user_tok)).json()["items"] if x["id"] == rid)
    assert any(needs(ln) for ln in mine["lines"]), "reviewer chatter must not answer the ask"

    # The requester's own answer still clears it.
    assert c.post(f"/api/e/request/{rid}/transition", headers=hdr(user_tok),
                  json={"to_state": "pending", "line_id": line_id,
                        "data": {"justification": "On it."}}).status_code == 200

    # Role can be taken away again.
    ungrant = c.post(f"/api/admin/users/{rev_id}/reviewer", headers=hdr(admin_tok), json={"is_reviewer": False})
    assert ungrant.status_code == 200 and ungrant.json()["is_reviewer"] is False
    assert c.get(f"/api/e/request/{rid}/comments", headers=hdr(rev_tok)).status_code == 404
