"""Guest login: shared non-admin account, never the first-signup admin, server-enforced.

Needs the Postgres-backed app (DATABASE_URL on a disposable test database) with an
example installed (EXAMPLE=access_requests), like the rest of the suite.
"""
import os
import uuid

import psycopg
import pytest

from app import main

ADMIN_EMAIL = "guest-suite-admin@example.test"
PASS = "a-long-password-1"


def hdr(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def c(_database, monkeypatch):
    monkeypatch.setenv("GUEST_LOGIN", "1")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("truncate users, sessions, allowed_emails cascade")
    return _database


def guest(c):
    r = c.post("/api/auth/guest")
    assert r.status_code == 200, r.text
    return r.json()


def first_signup(c, email=ADMIN_EMAIL):
    r = c.post("/api/auth/signup", json={"name": "First Person", "email": email, "password": PASS})
    assert r.status_code == 201, r.text
    return r.json()


def test_disabled_by_default(c, monkeypatch):
    monkeypatch.delenv("GUEST_LOGIN")
    assert c.get("/api/meta").json()["guest_login"] is False
    assert c.post("/api/auth/guest").status_code == 404
    monkeypatch.setenv("GUEST_LOGIN", "true")  # only the exact value 1 enables it
    assert c.post("/api/auth/guest").status_code == 404
    monkeypatch.setenv("GUEST_LOGIN", "1")
    assert c.get("/api/meta").json()["guest_login"] is True


def test_guest_is_not_admin(c):
    g = guest(c)
    assert g["is_admin"] is False and g["is_guest"] is True
    me = c.get("/api/me", headers=hdr(g["token"])).json()
    assert me["is_admin"] is False and me["is_guest"] is True
    assert "admin" not in me["roles"] and "reviewer" not in me["roles"]


def test_first_real_signup_is_still_admin_after_guest(c):
    guest(c)
    guest(c)
    first = first_signup(c)
    assert first["is_admin"] is True and first["token"]
    # ... and the next real signup is not.
    second = c.post("/api/auth/signup", json={"name": "Second Person", "email": "second-" + uuid.uuid4().hex[:6] + "@example.test", "password": PASS})
    assert second.status_code == 201 and second.json()["is_admin"] is False


def test_guest_cannot_use_admin_or_designer_endpoints(c, monkeypatch):
    monkeypatch.setenv("SCHEMA_EDIT", "1")  # even with the designer switched on
    admin_tok = first_signup(c)["token"]
    g = guest(c)
    h = hdr(g["token"])
    assert c.get("/api/admin/users", headers=h).status_code == 403
    assert c.get("/api/admin/allowed-emails", headers=h).status_code == 403
    assert c.post("/api/admin/allowed-emails", headers=h, json={"email": "x@example.test"}).status_code == 403
    assert c.post(f"/api/admin/users/{g['id']}/admin", headers=h, json={"is_admin": True}).status_code == 403
    assert c.post(f"/api/admin/users/{g['id']}/roles", headers=h, json={"roles": ["admin"]}).status_code == 403
    for path in ("/api/spec/generate", "/api/spec/plan", "/api/spec/apply"):
        assert c.post(path, headers=h, json={}).status_code == 403, path
    # An admin cannot promote or alter the guest account either, and it is not listed.
    ah = hdr(admin_tok)
    assert c.post(f"/api/admin/users/{g['id']}/admin", headers=ah, json={"is_admin": True}).status_code == 400
    assert c.post(f"/api/admin/users/{g['id']}/roles", headers=ah, json={"roles": ["admin"]}).status_code == 400
    assert c.post(f"/api/admin/users/{g['id']}/reviewer", headers=ah, json={"is_reviewer": True}).status_code == 400
    assert c.post(f"/api/admin/users/{g['id']}/reject", headers=ah).status_code == 400
    assert g["id"] not in {u["id"] for u in c.get("/api/admin/users", headers=ah).json()}
    assert c.get("/api/me", headers=h).json()["is_admin"] is False


def test_guest_can_browse_and_create_records_but_not_catalog(c):
    first_signup(c)
    g = guest(c)
    h = hdr(g["token"])
    tools = c.get("/api/e/tool", headers=h).json()["items"]
    assert tools, "an example must be installed (EXAMPLE=access_requests)"
    r = c.post("/api/e/request", headers=h, json={"reason": "guest trial", "lines": [tools[0]["id"]]})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert rid in {x["id"] for x in c.get("/api/e/request", headers=h).json()["items"]}
    # Catalog (admin-owned) data stays read-only for guests.
    assert c.post("/api/e/topic", headers=h, json={"name": "Nope-" + uuid.uuid4().hex[:5]}).status_code == 403


def test_guest_accounts_and_sessions_are_bounded(c, monkeypatch):
    monkeypatch.setattr(main, "GUEST_MAX_SESSIONS", 3)
    tokens = [guest(c)["token"] for _ in range(6)]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert conn.execute("select count(*) from users where is_guest").fetchone()[0] == 1
        assert conn.execute("select count(*) from sessions").fetchone()[0] == 3
        assert conn.execute("select count(*) from sessions where expires_at > now() + interval '25 hours'").fetchone()[0] == 0
    assert c.get("/api/me", headers=hdr(tokens[-1])).json()["is_guest"] is True
    assert c.get("/api/me", headers=hdr(tokens[0])).json() is None  # oldest evicted


def test_guest_email_cannot_be_registered_or_password_login(c):
    r = c.post("/api/auth/signup", json={"name": "Imposter", "email": main.GUEST_EMAIL, "password": PASS})
    assert r.status_code == 422
    guest(c)
    assert c.post("/api/auth/login", json={"email": main.GUEST_EMAIL, "password": "!"}).status_code == 401
