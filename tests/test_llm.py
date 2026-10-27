"""Real loopback HTTP stub; no model, Postgres or Docker required."""
from contextlib import contextmanager
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app import llm
import app.spec

ROOT = Path(app.spec.__file__).resolve().parents[1]
VALID = json.loads((ROOT / "examples/equipment_checkout/spec.json").read_text())


@contextmanager
def server(*responses):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(size))
            calls.append({"path": self.path, "body": request})
            status, content = responses[min(len(calls) - 1, len(responses) - 1)]
            body = json.dumps({"choices": [{"message": {"content": content}}]}).encode() if status == 200 else content.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield llm.Settings(f"http://127.0.0.1:{httpd.server_port}/v1", "stub"), calls
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def test_valid_and_contract():
    with server((200, json.dumps(VALID))) as (settings, calls):
        result = llm.design("Track synthetic equipment", settings=settings)
    assert result.attempts == 1
    assert result.public()["spec"] == VALID
    payload = calls[0]["body"]
    assert calls[0]["path"] == "/v1/chat/completions"
    assert payload["temperature"] == 0.2
    assert payload["response_format"] == {"type": "json_object"}
    prompt = payload["messages"][0]["content"]
    assert "Access Requests" in prompt and "Equipment Checkout" in prompt
    assert "JSON Schema" in prompt or '"$defs"' in prompt
    assert "renamed_from" in prompt and "list<domain>" in prompt


def test_invalid_then_repaired():
    invalid = dict(VALID, roles=["borrower"])
    with server((200, json.dumps(invalid)), (200, json.dumps(VALID))) as (settings, calls):
        result = llm.design("Track synthetic equipment", settings=settings)
    assert result.attempts == 2
    errors = json.loads(calls[1]["body"]["messages"][-1]["content"])["validation_errors"]
    assert any(error["path"] == "$.roles" and error["code"] == "missing_admin" for error in errors)


def test_three_invalid_surface_structured_errors():
    with server((200, '{}')) as (settings, calls):
        with pytest.raises(llm.DesignerError) as caught:
            llm.design("Synthetic use case", settings=settings)
    assert len(calls) == caught.value.attempts == 3
    assert caught.value.code == "invalid_spec"
    assert all(set(error) == {"path", "code", "message"} for error in caught.value.errors)


@pytest.mark.parametrize("wrapper", ["Here is the spec:\n{}\nDone.", "```json\n{}\n```"])
def test_prose_and_fences(wrapper):
    with server((200, wrapper.format(json.dumps(VALID)))) as (settings, calls):
        assert llm.design("Synthetic use case", settings=settings).attempts == 1


def test_unset_model_never_constructs_client(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unconfigured designer must not construct an HTTP client")
    monkeypatch.setattr(llm.httpx, "Client", forbidden)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(llm.DesignerError, match="LLM_MODEL") as caught:
        llm.design("Synthetic use case")
    assert caught.value.attempts == 0


def test_format_fallback_counts_in_budget():
    with server((400, '{"error":"response_format json_object is not supported"}'),
                (200, json.dumps(VALID))) as (settings, calls):
        assert llm.design("Synthetic use case", settings=settings).attempts == 2
    assert "response_format" in calls[0]["body"]
    assert "response_format" not in calls[1]["body"]


def test_arbitrary_provider_error_does_not_retry_or_leak():
    with server((400, '{"error":"sensitive provider detail"}')) as (settings, calls):
        with pytest.raises(llm.DesignerError) as caught:
            llm.design("Synthetic use case", settings=settings)
    assert len(calls) == 1
    assert "sensitive" not in json.dumps(caught.value.public())


def test_large_response_is_bounded():
    with server((200, "x" * llm.MAX_RESPONSE_BYTES)) as (settings, calls):
        with pytest.raises(llm.DesignerError) as caught:
            llm.design("Synthetic use case", settings=settings)
    assert caught.value.code == "response_too_large"
    assert caught.value.attempts == 1


def test_bad_input_never_requests():
    with server((200, json.dumps(VALID))) as (settings, calls):
        for kwargs in ({"use_case": "x" * 4001}, {"use_case": "x", "mode": "revise"}):
            with pytest.raises(llm.DesignerError):
                llm.design(settings=settings, **kwargs)
    assert not calls


def test_revise_supplies_validated_full_current_spec():
    current = app.spec.validate(VALID)
    with server((200, json.dumps(VALID))) as (settings, calls):
        result = llm.design("Rename with a hint if necessary", current, "revise", settings)
    data = json.loads(calls[0]["body"]["messages"][1]["content"])
    assert data["mode"] == "revise" and data["current_spec"] == VALID
    assert isinstance(result.spec, app.spec.Spec)


class NoDatabase:
    def connection(self):
        pytest.fail("Request must not touch DB")


def client_for(actor):
    app = FastAPI()
    def auth():
        if actor is None:
            raise HTTPException(401, "Sign in first.")
        return actor
    llm.mount(app, NoDatabase(), auth)
    return TestClient(app)


@pytest.mark.parametrize("route,body", [("generate", {"use_case": "Synthetic use case"}),
    ("plan", {"spec": VALID}), ("apply", {"spec": VALID, "plan_hash": "a" * 64})])
def test_unauthorized_and_disabled_never_reach_database(monkeypatch, route, body):
    monkeypatch.setenv("SCHEMA_EDIT", "1")
    assert client_for(None).post("/api/spec/" + route, json=body).status_code == 401
    assert client_for(SimpleNamespace(id="reader", roles=["borrower"])).post("/api/spec/" + route, json=body).status_code == 403
    monkeypatch.delenv("SCHEMA_EDIT")
    assert client_for(SimpleNamespace(id="admin", roles=["admin"])).post("/api/spec/" + route, json=body).status_code == 403


def test_generate_disabled_model_and_rate_limit(monkeypatch):
    monkeypatch.setenv("SCHEMA_EDIT", "1")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    client = client_for(SimpleNamespace(id="admin", is_admin=True))
    for _ in range(10):
        response = client.post("/api/spec/generate", json={"use_case": "Synthetic use case"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "not_configured"
    assert client.post("/api/spec/generate", json={"use_case": "Synthetic use case"}).status_code == 429


@pytest.mark.parametrize("content,expected", [(json.dumps(VALID), 200), ('{}', 422)])
def test_generate_route_local_model_never_reaches_db(monkeypatch, content, expected):
    monkeypatch.setenv("SCHEMA_EDIT", "1")
    with server((200, content)) as (settings, calls):
        monkeypatch.setenv("LLM_MODEL", settings.model)
        monkeypatch.setenv("LLM_BASE_URL", settings.base_url)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        response = client_for(SimpleNamespace(id="admin", roles=["admin"])).post(
            "/api/spec/generate", json={"use_case": "Synthetic use case"})
    assert response.status_code == expected
    if expected == 422:
        assert response.json()["detail"]["attempts"] == 3
        assert response.json()["detail"]["errors"]
    else:
        assert response.json()["spec"] == VALID


def test_closed_requests_and_invalid_plan_before_db(monkeypatch):
    monkeypatch.setenv("SCHEMA_EDIT", "1")
    client = client_for(SimpleNamespace(id="admin", roles=["admin"]))
    assert client.post("/api/spec/plan", json={"spec": VALID, "sql": "arbitrary"}).status_code == 422
    response = client.post("/api/spec/plan", json={"spec": {}})
    assert response.status_code == 422
    assert response.json()["detail"]["errors"]


def test_plan_apply_forward_validated_specs_and_confirmations(monkeypatch):
    """Unit forwarding only: this deliberately does not verify database DDL."""
    from app import migrate
    monkeypatch.setenv("SCHEMA_EDIT", "1")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    sentinel, calls = object(), []
    class Pool:
        @contextmanager
        def connection(self):
            yield sentinel
    class Result:
        def public(self):
            return {"plan_hash": "a" * 64, "operations": []}
    def plan(conn, spec):
        assert conn is sentinel and isinstance(spec, app.spec.Spec)
        calls.append("plan")
        return Result()
    def apply(conn, spec, plan_hash, **confirmations):
        assert conn is sentinel and isinstance(spec, app.spec.Spec)
        assert plan_hash == "a" * 64
        assert confirmations == {"confirm_risky": True, "confirm_destructive": "a" * 64}
        calls.append("apply")
        return Result()
    monkeypatch.setattr(migrate, "plan", plan)
    monkeypatch.setattr(migrate, "apply", apply)
    application = FastAPI()
    llm.mount(application, Pool(), lambda: SimpleNamespace(id="admin", roles=["admin"]))
    client = TestClient(application)
    assert client.post("/api/spec/plan", json={"spec": VALID}).status_code == 200
    assert client.post("/api/spec/apply", json={"spec": VALID, "plan_hash": "a" * 64,
        "confirm_risky": True, "confirm_destructive": "a" * 64}).status_code == 200
    assert calls == ["plan", "apply"]
