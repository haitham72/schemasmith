"""Bring-your-own-key designer and guest designer access, against a local fake provider.

No network and no paid calls: llm.OPENAI_BASE_URL is pointed at a loopback server.
"""
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import psycopg
import pytest

from app import llm

SPEC = (Path(__file__).resolve().parents[1] / "examples/equipment_checkout/spec.json").read_text(encoding="utf-8")
USER_KEY = "-".join(["sk", "test", "0123456789abcdef", "TESTKEY"])  # built at runtime: not a real secret
PASS = "a-long-password-1"
seen = []


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        seen.append({"path": self.path, "auth": self.headers.get("authorization"),
                     "extra": self.headers.get("x-llm-base-url")})
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": SPEC}}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def fake_url():
    server = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/v1" % server.server_port
    server.shutdown()


@pytest.fixture()
def c(_database, monkeypatch, fake_url):
    monkeypatch.setenv("GUEST_LOGIN", "1")
    monkeypatch.setenv("SCHEMA_EDIT", "1")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setattr(llm, "OPENAI_BASE_URL", fake_url)
    seen.clear()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("truncate users, sessions, allowed_emails cascade")
    return _database


def hdr(token, key=None):
    h = {"Authorization": "Bearer " + token}
    if key:
        h["X-OpenAI-Key"] = key
    return h


def admin_token(c):
    r = c.post("/api/auth/signup", json={"name": "First Admin", "email": "byok-admin@example.test", "password": PASS})
    assert r.status_code == 201 and r.json()["is_admin"]
    return r.json()["token"]


def guest_token(c):
    return c.post("/api/auth/guest").json()["token"]


GEN = {"use_case": "Our media lab lends equipment.", "mode": "create"}


def test_header_key_is_used_for_the_call(c):
    r = c.post("/api/spec/generate", headers=hdr(admin_token(c), USER_KEY), json=GEN)
    assert r.status_code == 200, r.text
    assert seen[0]["auth"] == "Bearer " + USER_KEY
    assert seen[0]["path"] == "/v1/chat/completions"


def test_key_is_never_stored_logged_or_echoed(c, caplog):
    caplog.set_level(logging.DEBUG)
    tok = admin_token(c)
    ok = c.post("/api/spec/generate", headers=hdr(tok, USER_KEY), json=GEN)
    bad = c.post("/api/spec/generate", headers=hdr(tok, USER_KEY), json={"use_case": "", "mode": "create"})
    assert ok.status_code == 200 and bad.status_code == 422
    assert USER_KEY not in ok.text and USER_KEY not in bad.text and USER_KEY not in caplog.text
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        tables = [r[0] for r in conn.execute("select tablename from pg_tables where schemaname = 'public'").fetchall()]
        for table in tables:
            rows = conn.execute(f'select t::text from "{table}" t').fetchall()
            assert not any(USER_KEY in row[0] for row in rows), table


def test_user_key_cannot_change_the_base_url(c, monkeypatch):
    # The server's own LLM_BASE_URL (an unreachable port) must not apply to a user key either.
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    tok = admin_token(c)
    h = hdr(tok, USER_KEY)
    h["X-LLM-Base-Url"] = "http://127.0.0.1:1/v1"
    assert c.post("/api/spec/generate", headers=h, json=GEN).status_code == 200
    assert seen and seen[0]["extra"] is None  # the header is ignored, not forwarded
    assert c.post("/api/spec/generate", headers=hdr(tok, USER_KEY), json={**GEN, "base_url": "http://127.0.0.1:1/v1"}).status_code == 422
    # Malformed keys are refused before any network call and never echoed.
    seen.clear()
    r = c.post("/api/spec/generate", headers=hdr(tok, "not a key\nX: y"), json=GEN)
    assert r.status_code in (400, 422) and not seen and "not a key" not in r.text


def test_guest_designer_flag(c, monkeypatch):
    admin_token(c)
    g = guest_token(c)
    monkeypatch.delenv("GUEST_DESIGNER", raising=False)
    assert c.get("/api/spec").json()["guest_designer"] is False
    for path, body in (("/api/spec/generate", GEN), ("/api/spec/plan", {"spec": json.loads(SPEC)})):
        assert c.post(path, headers=hdr(g, USER_KEY), json=body).status_code == 403, path
    monkeypatch.setenv("GUEST_DESIGNER", "1")
    assert c.get("/api/spec").json()["guest_designer"] is True
    r = c.post("/api/spec/generate", headers=hdr(g, USER_KEY), json=GEN)
    assert r.status_code == 200 and seen[0]["auth"] == "Bearer " + USER_KEY
    # Guests must bring their own key; the server's model is never spent on them.
    assert c.post("/api/spec/generate", headers=hdr(g), json=GEN).status_code == 403
    assert c.post("/api/spec/plan", headers=hdr(g), json={"spec": r.json()["spec"]}).status_code == 200
    # Migration safety: guests cannot confirm risky or destructive changes.
    body = {"spec": r.json()["spec"], "plan_hash": "a" * 64, "confirm_destructive": "b" * 64}
    assert c.post("/api/spec/apply", headers=hdr(g), json=body).status_code == 403
    # Everything else stays closed to the guest.
    assert c.get("/api/admin/users", headers=hdr(g)).status_code == 403
    assert c.get("/api/me", headers=hdr(g)).json()["is_admin"] is False
    # A stale plan hash is still a 409 for a guest.
    stale = {"spec": r.json()["spec"], "plan_hash": "a" * 64}
    assert c.post("/api/spec/apply", headers=hdr(g), json=stale).status_code == 409


def test_guest_designer_needs_schema_edit(c, monkeypatch):
    monkeypatch.setenv("GUEST_DESIGNER", "1")
    monkeypatch.setenv("SCHEMA_EDIT", "0")
    admin_token(c)
    assert c.post("/api/spec/generate", headers=hdr(guest_token(c), USER_KEY), json=GEN).status_code == 403
