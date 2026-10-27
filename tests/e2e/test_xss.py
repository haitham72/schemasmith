"""Browser XSS coverage: hostile labels and record values render inert.

Self-contained: installs the access_requests example on the disposable test
database, seeds hostile item/request/comment values through the generic API,
serves the app on its own loopback uvicorn, and drives Chromium. Collected
always; runs only with RUN_E2E=1. Browser assertions skip when Playwright,
Chromium or uvicorn is unavailable.
"""
import os
import threading
import time
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1",
    reason="Live-browser XSS run needs RUN_E2E=1 on a disposable test database",
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

SCRIPT = "<script>window.__xss=1</script>"
IMG = '<img src=x onerror="window.__xss=1">'

# Generic-UI routes: browse, my records, report, overview, queue, users,
# exports, reference editor, plus one catalog detail page.
PAGES = [
    "#/catalog",
    "#/mine",
    "#/review",
    "#/admin",
    "#/admin/requests",
    "#/admin/users",
    "#/admin/allowlist",
    "#/admin/catalog",
]


def hdr(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


@pytest.fixture(scope="module")
def seed(client_for):
    """Fresh access_requests install with hostile values seeded via the API."""
    client, install = client_for
    install("access_requests")
    email = f"xss-admin-{uuid.uuid4().hex[:6]}@example.test"
    r = client.post("/api/auth/signup", json={
        "name": "E2E Admin", "email": email, "password": "xss-driver-pass"})
    assert r.status_code == 201, r.text
    assert r.json()["token"], "first signup on a fresh install is admin"
    admin_tok = r.json()["token"]
    token = uuid.uuid4().hex[:8]
    topic = client.post("/api/e/topic", headers=hdr(admin_tok),
                        json={"name": "Reachability " + token}).json()
    item = client.post("/api/e/tool", headers=hdr(admin_tok), json={
        "name": SCRIPT + " " + token,
        "parent_id": topic["id"],
        "domains": [],
        "needs_review": True,
        "notes": IMG + " " + token,
    })
    assert item.status_code == 201, item.text
    borrower_email = f"xss-borrower-{token}@example.test"
    r = client.post("/api/auth/signup", json={
        "name": "E2E Borrower", "email": borrower_email, "password": "xss-driver-pass"})
    assert r.status_code == 201, r.text
    users = client.get("/api/admin/users?status=pending", headers=hdr(admin_tok)).json()
    uid = next(u["id"] for u in users if u["email"] == borrower_email)
    assert client.post(f"/api/admin/users/{uid}/approve", headers=hdr(admin_tok)).status_code == 200
    login = client.post("/api/auth/login", json={
        "email": borrower_email, "password": "xss-driver-pass"})
    assert login.status_code == 200, login.text
    requester_tok = login.json()["token"]
    request = client.post("/api/e/request", headers=hdr(requester_tok), json={
        "reason": SCRIPT + " " + token,
        "other": IMG + " " + token,
        "lines": [item.json()["id"]],
    })
    assert request.status_code == 201, request.text
    comment = client.post(f"/api/e/request/{request.json()['id']}/comments",
                          headers=hdr(requester_tok),
                          json={"body": SCRIPT + " " + IMG + " " + token})
    assert comment.status_code == 201, comment.text
    return {"admin_token": admin_tok, "requester_token": requester_tok,
            "category_id": topic["id"], "marker": token}


@pytest.fixture(scope="module")
def server(seed):
    from .conftest import start_server

    base, stop = start_server()
    try:
        yield base
    finally:
        stop()


@pytest.fixture(scope="module")
def page(server, seed):
    if not BROWSER_OK:
        pytest.skip("playwright is not installed")
    events = {"dialogs": [], "errors": []}
    with sync_playwright() as runner:
        try:
            browser = runner.chromium.launch()
        except Exception:
            pytest.skip("Chromium binary is not installed")
        context = browser.new_context()
        tab = context.new_page()
        tab.on("dialog", lambda dialog: (events["dialogs"].append(dialog.message), dialog.dismiss()))
        tab.on("pageerror", lambda error: events["errors"].append(str(error)))
        tab.goto(server + "/#/catalog")
        tab.evaluate(
            "token => localStorage.setItem('ac.token', JSON.stringify(token))", seed["admin_token"]
        )
        tab.reload()  # the app reads its token once at boot
        yield tab, events, seed, server
        context.close()
        browser.close()


def visit(tab, events, base, route):
    tab.goto(base + "/" + route)
    tab.reload()  # hash-only navigation does not re-run the boot sequence
    tab.wait_for_load_state("networkidle")
    assert tab.evaluate("window.__xss || 0") == 0, route


def test_no_script_or_handler_executes_on_any_page(page):
    tab, events, seed, base = page
    for route in PAGES:
        visit(tab, events, base, route)
    visit(tab, events, base, "#/catalog/" + seed["category_id"])
    assert events["dialogs"] == []
    assert events["errors"] == []


def test_payloads_render_as_inert_text(page):
    tab, events, seed, base = page
    visit(tab, events, base, "#/catalog/" + seed["category_id"])
    body = tab.locator("body")
    assert (SCRIPT + " " + seed["marker"]) in body.inner_text()
    assert tab.evaluate("document.querySelectorAll(\"img[src='x']\").length") == 0
    assert tab.evaluate(
        "Array.from(document.scripts).filter(s => s.text.includes('__xss')).length"
    ) == 0
    tab.evaluate(
        "token => localStorage.setItem('ac.token', JSON.stringify(token))", seed["requester_token"]
    )
    visit(tab, events, base, "#/mine")
    assert (SCRIPT + " " + seed["marker"]) in tab.locator("body").inner_text()
