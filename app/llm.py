"""Only model-call boundary; validated proposals never apply themselves.

Three total requests (including compatibility fallback) bound the repair budget.
No prompts, credentials, raw responses or provider error bodies are logged.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import os
import re
from pathlib import Path
import threading
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .spec import Spec, SpecValidationError, validate

MAX_RESPONSE_BYTES = 256 * 1024
MAX_ATTEMPTS = 3
# A user-supplied key (bring your own key) only ever goes to this fixed endpoint.
# There is deliberately no request parameter that can change it (no SSRF).
OPENAI_BASE_URL = "https://api.openai.com/v1"
KEY_RE = re.compile(r"^sk-[A-Za-z0-9_\-]{8,200}$")


@dataclass(frozen=True)
class Settings:
    base_url: str = "http://localhost:11434/v1"
    model: str = ""
    api_key: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls):
        return cls(os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
                   os.getenv("LLM_MODEL", ""), os.getenv("LLM_API_KEY") or None)


class DesignerError(ValueError):
    def __init__(self, code, message, *, attempts=0, errors=None):
        super().__init__(message)
        self.code, self.message, self.attempts = code, message, attempts
        self.errors = errors or []

    def public(self):
        return {"code": self.code, "message": self.message,
                "attempts": self.attempts, "errors": self.errors}


@dataclass
class DesignResult:
    spec: Spec
    attempts: int

    def public(self):
        return {"spec": self.spec.model_dump(mode="json", by_alias=True, exclude_unset=True),
                "attempts": self.attempts}


def system_prompt():
    root = Path(__file__).resolve().parents[1]
    rules = (root / "app/prompts/design.md").read_text(encoding="utf-8")
    schema = json.loads((root / "spec/schema.json").read_text(encoding="utf-8"))
    examples = [json.loads((root / "examples" / name / "spec.json").read_text(encoding="utf-8"))
                for name in ("access_requests", "equipment_checkout")]
    return rules + "\nJSON Schema:\n" + json.dumps(schema) + "\nExamples:\n" + json.dumps(examples)


def extract_json(content):
    """Decode one object, accepting bounded prose/fences without evaluating text."""
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ValueError("Invalid content")
    def distinct_pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    decoder = json.JSONDecoder(object_pairs_hook=distinct_pairs)
    # At most 32 candidates prevents quadratic scanning of arbitrary responses.
    starts = [i for i, char in enumerate(content) if char == "{"][:32]
    for index in starts:
        try:
            obj, _ = decoder.raw_decode(content, index)
            if isinstance(obj, dict):
                return obj
        except (ValueError, RecursionError):
            pass
    raise ValueError("No JSON object")


def _response(client, url, payload, headers):
    deadline = time.monotonic() + 120
    with client.stream("POST", url, json=payload, headers=headers) as response:
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            if time.monotonic() > deadline:
                raise DesignerError("response_timeout", "Model response exceeded the time limit.")
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise DesignerError("response_too_large", "Model response exceeds the size limit.")
            chunks.append(chunk)
        return response.status_code, b"".join(chunks)


def _unsupported_format(status, body):
    if status not in (400, 422):
        return False
    message = body.decode("utf-8", errors="replace").lower()
    return ("response_format" in message or "json_object" in message) and any(
        word in message for word in ("unsupported", "not supported", "unknown", "unrecognized", "not permitted"))


def design(use_case, current_spec=None, mode="create", settings=None, user_key=None):
    if user_key is not None:
        if not KEY_RE.fullmatch(user_key):
            raise DesignerError("invalid_key", "That does not look like an OpenAI API key.")
        # The key is used for this call only: never stored, logged or echoed.
        settings = Settings(OPENAI_BASE_URL, os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini", user_key)
    settings = settings or Settings.from_env()
    if not settings.model.strip():
        raise DesignerError("not_configured", "Add your OpenAI API key, or configure LLM_MODEL on the server, to use the designer.")
    if not isinstance(use_case, str) or not use_case.strip() or len(use_case) > 4000:
        raise DesignerError("invalid_input", "Describe the use case in 1–4000 characters.")
    if mode not in ("create", "revise") or mode == "revise" and current_spec is None:
        raise DesignerError("invalid_input", "Revise requires a current spec and a valid mode.")
    if current_spec is not None:
        try:
            raw = current_spec.model_dump(mode="json", by_alias=True, exclude_unset=True) if isinstance(current_spec, Spec) else current_spec
            current_spec = validate(raw).model_dump(mode="json", by_alias=True, exclude_unset=True)
        except SpecValidationError as exc:
            raise DesignerError("invalid_input", "Current spec is invalid.", errors=exc.errors) from None
    parsed = urlsplit(settings.base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise DesignerError("invalid_config", "LLM_BASE_URL must be an HTTP URL without credentials, query or fragment.")
    messages = [{"role": "system", "content": system_prompt()},
                {"role": "user", "content": json.dumps({"use_case": use_case, "mode": mode, "current_spec": current_spec})}]
    headers = {"Authorization": "Bearer " + settings.api_key} if settings.api_key else {}
    use_format, errors = True, []
    # Ignore proxy environment variables: local model traffic stays on the configured host.
    with httpx.Client(timeout=120.0, trust_env=False, follow_redirects=False) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            payload = {"model": settings.model, "temperature": 0.2, "messages": messages}
            if use_format:
                payload["response_format"] = {"type": "json_object"}
            try:
                status, body = _response(client, settings.base_url.rstrip("/") + "/chat/completions", payload, headers)
            except DesignerError as exc:
                exc.attempts = attempt
                raise
            except httpx.HTTPError:
                raise DesignerError("connection_failed", "Could not reach the configured model.", attempts=attempt) from None
            if use_format and _unsupported_format(status, body):
                use_format = False
                errors = [{"path": "$", "code": "unsupported_format", "message": "Model rejected JSON response format."}]
                continue
            if status in (401, 403):
                raise DesignerError("invalid_key", "The model provider rejected the API key.", attempts=attempt) from None
            if status != 200:
                raise DesignerError("provider_error", "The configured model rejected the request.", attempts=attempt) from None
            content = None
            try:
                envelope = json.loads(body)
                content = envelope["choices"][0]["message"]["content"]
                spec = validate(extract_json(content))
                return DesignResult(spec, attempt)
            except SpecValidationError as exc:
                errors = exc.errors
            except (ValueError, KeyError, IndexError, TypeError, RecursionError):
                errors = [{"path": "$", "code": "invalid_json", "message": "Return one complete JSON spec object."}]
            if attempt < MAX_ATTEMPTS:
                if isinstance(content, str):
                    messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": json.dumps({"validation_errors": errors,
                    "instruction": "Correct these errors; return the full revised JSON spec only."})})
    raise DesignerError("invalid_spec", "Model could not produce a valid spec within three attempts.", attempts=MAX_ATTEMPTS, errors=errors)


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    use_case: str = Field(min_length=1, max_length=4000)
    current_spec: dict | None = None
    mode: Literal["create", "revise"] = "create"


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    spec: dict


class ApplyRequest(PlanRequest):
    plan_hash: str = Field(min_length=64, max_length=64, pattern="^[a-f0-9]{64}$")
    confirm_risky: bool = False
    confirm_destructive: str | None = Field(default=None, min_length=64, max_length=64, pattern="^[a-f0-9]{64}$")


def mount(app, pool, admin_dependency, who_dependency=None):
    """Single-process per-admin limit: 10 designer operations/minute (shared routes)."""
    from . import migrate
    buckets, lock = {}, threading.Lock()

    def permitted(actor=Depends(who_dependency or admin_dependency)):
        guest = bool(getattr(actor, "is_guest", False))
        if guest:
            if os.getenv("GUEST_DESIGNER", "0").strip() != "1":
                raise HTTPException(403, "The designer is not available to guests.")
        elif not actor or not (getattr(actor, "is_admin", False) or "admin" in (getattr(actor, "roles", None) or [])):
            raise HTTPException(403, "Admins only.")
        if os.getenv("SCHEMA_EDIT") != "1":
            raise HTTPException(403, "Schema editing is disabled. Set SCHEMA_EDIT=1 to enable it.")
        now, key = time.monotonic(), str(actor.id)
        with lock:
            # Evict idle keys so changing identities cannot grow memory forever.
            for stale in [k for k, values in buckets.items() if not values or values[-1] <= now - 60]:
                del buckets[stale]
            values = buckets.setdefault(key, deque())
            while values and values[0] <= now - 60:
                values.popleft()
            if len(values) >= 10:
                raise HTTPException(429, "Designer rate limit reached; try again in a minute.", headers={"Retry-After": "60"})
            values.append(now)
        return actor

    def checked(raw):
        try:
            return validate(raw)
        except SpecValidationError as exc:
            raise HTTPException(422, {"code": "invalid_spec", "errors": exc.errors}) from None

    @app.post("/api/spec/generate")
    def generate(body: GenerateRequest, actor=Depends(permitted),
                 x_openai_key: str | None = Header(default=None)):
        if body.current_spec is not None:
            checked(body.current_spec)
        user_key = (x_openai_key or "").strip() or None
        if getattr(actor, "is_guest", False) and not user_key:
            # Guests never spend the server's own model; they bring a key.
            raise HTTPException(403, {"code": "key_required", "message": "Add your own OpenAI API key to generate."})
        try:
            return design(body.use_case, body.current_spec, body.mode, user_key=user_key).public()
        except DesignerError as exc:
            status = 503 if exc.code in ("not_configured", "connection_failed") else 422 if exc.code in ("invalid_spec", "invalid_input") else 400 if exc.code == "invalid_key" else 502
            raise HTTPException(status, exc.public()) from None

    @app.post("/api/spec/plan")
    def plan(body: PlanRequest, actor=Depends(permitted)):
        spec = checked(body.spec)
        try:
            with pool.connection() as conn:
                return migrate.plan(conn, spec).public()
        except migrate.MigrationError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/spec/apply")
    def apply(body: ApplyRequest, actor=Depends(permitted)):
        if getattr(actor, "is_guest", False) and (body.confirm_destructive or body.confirm_risky):
            raise HTTPException(403, "Guests cannot confirm risky or destructive migrations.")
        spec = checked(body.spec)
        try:
            with pool.connection() as conn:
                result = migrate.apply(conn, spec, body.plan_hash, confirm_risky=body.confirm_risky,
                                       confirm_destructive=body.confirm_destructive)
                return result.public()
        except migrate.MigrationError as exc:
            raise HTTPException(409, str(exc)) from None
