"""Generic WP7 smoke: same flow against both examples, no domain-specific paths.

Per example: sign up the first account (admin), submit as a requester, approve
one line/record and deny another, check the rollup, download an export, and
(only with a real Chromium available) load the branded shell.
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1",
    reason="Browser/API smoke needs RUN_E2E=1 on a disposable test database",
)

BROWSER_OK = False
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    BROWSER_OK = True
except Exception:
    BROWSER_OK = False

try:
    import uvicorn  # noqa: F401
    UVICORN_OK = True
except Exception:
    UVICORN_OK = False


def hdr(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


def signup(client, name, email, password):
    return client.post("/api/auth/signup", json={"name": name, "email": email, "password": password})


def ensure_admin(client):
    email = f"admin-{uuid.uuid4().hex[:6]}@example.test"
    r = signup(client, "Smoke Admin", email, "admin-pass-123")
    assert r.status_code == 201, r.text
    assert r.json()["token"], "first signup is approved immediately"
    return r.json()["token"]


def approve_user(client, admin_tok, email):
    users = client.get("/api/admin/users?status=pending", headers=hdr(admin_tok)).json()
    uid = next(u["id"] for u in users if u["email"] == email)
    assert client.post(f"/api/admin/users/{uid}/approve", headers=hdr(admin_tok)).status_code == 200
    r = client.post("/api/auth/login", json={"email": email, "password": "user-pass-123"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def setup_access(client, admin_tok):
    topic = client.post("/api/e/topic", headers=hdr(admin_tok), json={"name": "Smoke tools"}).json()
    tools = []
    for name, domains in (("Smoke registry A", ["a.smoke.example.test"]),
                          ("Smoke registry B", ["b.smoke.example.test"])):
        r = client.post("/api/e/tool", headers=hdr(admin_tok),
                        json={"name": name, "parent_id": topic["id"], "domains": domains})
        assert r.status_code == 201, r.text
        tools.append(r.json())
    return tools


def setup_equipment(client, admin_tok):
    assets = []
    for tag in ("SMOKE-1", "SMOKE-2"):
        r = client.post("/api/e/asset", headers=hdr(admin_tok),
                        json={"tag": tag, "name": f"Smokelight {tag}", "condition": "good"})
        assert r.status_code == 201, r.text
        assets.append(r.json())
    return assets


def record_state(spec, rec):
    """Client mirror of app.workflow.rollup (same rule shape as /api/spec)."""
    wf = spec["workflow"]
    if wf["level"] == "record":
        return rec["state"]
    states = [ln["state"] for ln in rec["lines"]]
    rule = wf["rollup"]
    if not any(s in rule["all"] for s in states):
        return rule["none_decided"]
    if len(set(states)) == 1 and states[0] in rule["all"]:
        return rule["all"][states[0]]
    return rule["mixed"]


@pytest.mark.parametrize("example", ["access_requests", "equipment_checkout"])
def test_smoke(client_for, example):
    client, install = client_for
    spec = install(example).model_dump(mode="json", by_alias=True, exclude_none=True)
    admin_tok = ensure_admin(client)

    if example == "access_requests":
        tools = setup_access(client, admin_tok)
        email = f"req-{uuid.uuid4().hex[:6]}@example.test"
        assert signup(client, "Smoke User", email, "user-pass-123").status_code == 201
        user_tok = approve_user(client, admin_tok, email)
        r = client.post("/api/e/request", headers=hdr(user_tok),
                        json={"reason": "smoke run", "lines": [t["id"] for t in tools]})
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        lines = {ln["ref"]["id"]: ln["id"] for ln in r.json()["lines"]}
        assert client.post(f"/api/e/request/{rid}/transition", headers=hdr(admin_tok),
                           json={"to_state": "approved", "line_id": lines[tools[0]["id"]] }).status_code == 200
        assert client.post(f"/api/e/request/{rid}/transition", headers=hdr(admin_tok),
                           json={"to_state": "denied", "line_id": lines[tools[1]["id"]] }).status_code == 200
        got = client.get(f"/api/e/request/{rid}", headers=hdr(admin_tok)).json()
        assert record_state(spec, got) == "partial"
        assert {ln["state"] for ln in got["lines"]} == {"approved", "denied"}
        # Requester sees the decided rollup on their own record.
        mine = client.get("/api/e/request", headers=hdr(user_tok)).json()["items"]
        assert any(m["id"] == rid for m in mine)
        # Thread round-trip on the record.
        assert client.post(f"/api/e/request/{rid}/comments", headers=hdr(user_tok),
                           json={"body": "smoke note"}).status_code == 201
        # Export downloads.
        x = client.get("/api/e/tool/export.csv", headers=hdr(admin_tok))
        assert x.status_code == 200 and "a.smoke.example.test" in x.text
    else:
        assets = setup_equipment(client, admin_tok)
        email = f"bor-{uuid.uuid4().hex[:6]}@example.test"
        assert signup(client, "Smoke Borrower", email, "user-pass-123").status_code == 201
        user_tok = approve_user(client, admin_tok, email)
        ids = []
        for asset in assets:
            r = client.post("/api/e/checkout", headers=hdr(user_tok),
                            json={"asset": asset["id"], "due_date": "2027-01-01", "purpose": "smoke"})
            assert r.status_code == 201, r.text
            ids.append(r.json()["id"])
        assert client.post(f"/api/e/checkout/{ids[0]}/transition", headers=hdr(admin_tok),
                           json={"to_state": "approved"}).status_code == 200
        assert client.post(f"/api/e/checkout/{ids[1]}/transition", headers=hdr(admin_tok),
                           json={"to_state": "rejected"}).status_code == 200
        first = client.get(f"/api/e/checkout/{ids[0]}", headers=hdr(admin_tok)).json()
        second = client.get(f"/api/e/checkout/{ids[1]}", headers=hdr(admin_tok)).json()
        assert first["state"] == "approved" and second["state"] == "rejected"
        x = client.get("/api/e/asset/export.csv", headers=hdr(admin_tok))
        assert x.status_code == 200 and "SMOKE-1" in x.text

    # Spec-driven shell contract both examples share.
    pub = client.get("/api/spec").json()
    assert pub["spec"]["app"]["title"]
    assert {v["type"] for v in pub["spec"]["views"]} >= {"browse_pick", "my_records", "queue"}


@pytest.mark.parametrize("example", ["access_requests", "equipment_checkout"])
def test_smoke_browser(client_for, example):
    """Load the branded shell in Chromium; skip without playwright/uvicorn."""
    if not (BROWSER_OK and UVICORN_OK):
        pytest.skip("playwright Chromium or uvicorn is not installed")
    from playwright.sync_api import sync_playwright

    from .conftest import start_server

    client, install = client_for
    spec = install(example).model_dump(mode="json", by_alias=True, exclude_none=True)
    title = spec["app"]["title"]
    base, stop = start_server()
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception:
                pytest.skip("Chromium binary is not installed")
            page = browser.new_page()
            page.goto(base + "/", wait_until="networkidle")
            assert title in page.title() or title in page.content()
            browser.close()
    finally:
        stop()
