"""Access Console API.

Auth is deliberately simple: email + password + name, stored locally.
An admin approves new sign-ups, or pre-approves emails so those sign-ups
are approved immediately. Sessions are opaque Bearer tokens in `sessions`.
"""
import hashlib
import hmac
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

import psycopg.errors as pgerr
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .db import init_db, pool
from . import llm, records, workflow
from . import export as engine_export
from .migrate import active as active_spec

ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
# Sign-ups are restricted to this email domain (ADMIN_EMAILS always bypass it).
SIGNUP_DOMAIN = os.environ.get("SIGNUP_DOMAIN", "").strip().lower().lstrip("@")
# "Continue as guest": one shared non-admin account, opt-in per deployment.
GUEST_EMAIL = "guest@guest.invalid"
GUEST_SESSION_HOURS = 24
GUEST_MAX_SESSIONS = 200


def guest_login_enabled() -> bool:
    return os.environ.get("GUEST_LOGIN", "0").strip() == "1"


STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_DEPTH = 4


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield
    pool.close()


app = FastAPI(title="Access Console", lifespan=lifespan)


# ---------------------------------------------------------------- auth helpers

EMAIL_RE = re.compile(r"[^\s@,;|<>]+@[^\s@,;|<>]+\.[^\s@,;|<>]+")
PBKDF2_ITER = 200_000


class Who(BaseModel):
    id: str
    name: str
    email: str
    is_admin: bool
    is_reviewer: bool = False
    roles: list[str] = Field(default_factory=list)
    status: str = "approved"
    is_guest: bool = False


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)
    return f"pbkdf2${PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def norm_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise HTTPException(422, "Enter a valid email address.")
    return email


def new_token() -> str:
    return secrets.token_urlsafe(32)


def row_to_who(r) -> Who:
    guest = bool(r.get("is_guest", False))
    if guest:
        # Defense in depth: whatever the row says, a guest is never privileged.
        r = {**r, "is_admin": False, "is_reviewer": False,
             "roles": [x for x in (r.get("roles") or []) if x not in ("admin", "reviewer")]}
    roles = sorted(workflow.roles_for(r))
    if guest:
        roles = [x for x in roles if x not in ("admin", "reviewer")]
    return Who(id=str(r["id"]), name=r["name"], email=r["email"], is_admin="admin" in roles,
               is_reviewer="reviewer" in roles, roles=roles, status=r["status"], is_guest=guest)


def auth_opt(authorization: Optional[str] = Header(default=None)) -> Optional[Who]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    with pool.connection() as c:
        r = c.execute(
            """select u.id, u.name, u.email, u.is_admin, u.is_reviewer, u.roles, u.status, u.is_guest
               from sessions s join users u on u.id = s.user_id
               where s.token = %s and s.expires_at > now()""",
            (token,),
        ).fetchone()
        if r:
            _assign_unset_roles(c, r)
    return row_to_who(r) if r else None


def who_opt(w: Optional[Who] = Depends(auth_opt)) -> Optional[Who]:
    return w


def who(w: Optional[Who] = Depends(auth_opt)) -> Who:
    if not w:
        raise HTTPException(401, "Sign in first.")
    if w.status != "approved":
        raise HTTPException(403, "Your account is waiting for admin approval.")
    return w


def admin(w: Who = Depends(who)) -> Who:
    if w.is_guest:
        raise HTTPException(403, "Guests cannot perform admin actions.")
    return workflow.require_role(w, "admin")


def issue_session(c, user_id) -> str:
    token = new_token()
    c.execute("insert into sessions (token, user_id) values (%s,%s)", (token, user_id))
    # Prune expired sessions opportunistically.
    try:
        c.execute("delete from sessions where expires_at < now() - interval '1 day'")
    except Exception:
        pass
    return token


def user_public(r) -> dict:
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "email": r["email"],
        "is_admin": bool(r["is_admin"]),
        "is_reviewer": bool(r.get("is_reviewer", False)),
        "roles": sorted(workflow.roles_for(r)),
        "signup_ip": r.get("signup_ip") or "",
        "status": r["status"],
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
    }


def _signup_roles(c, is_admin):
    if is_admin:
        return ['admin']
    stored = active_spec(c)
    if not stored:
        return []
    document = stored['spec']
    declared = set(document['roles'])
    return sorted({entity['owner'] for entity in document['entities']
                   if entity['kind'] == 'record' and entity.get('owner') in declared})


def _effective_signup_domain(stored):
    """A nonempty environment setting overrides the active application's value."""
    return SIGNUP_DOMAIN or ((stored or {}).get('spec', {}).get('app', {}).get('signup_domain') or '')


def _assign_unset_roles(c, row):
    """Materialize the first active spec's defaults for pre-design signups.

    The role-management API requires a nonempty array. Empty means no roles have
    yet been assigned, not an explicit permission revocation. Nonempty assignments
    are preserved across spec changes and can be changed only by an administrator.
    """
    if row.get('roles') or row['is_admin']:
        return
    defaults = _signup_roles(c, False)
    if defaults:
        updated = c.execute('update users set roles = %s where id = %s and cardinality(roles) = 0 returning roles',
                            (defaults, row['id'])).fetchone()
        if updated:
            row['roles'] = updated['roles']
        else:
            current = c.execute('select roles from users where id = %s', (row['id'],)).fetchone()
            if current:
                row['roles'] = current['roles']


# ---------------------------------------------------------------- auth endpoints

class Signup(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=6, max_length=128)


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=128)


@app.post("/api/auth/signup", status_code=201)
def signup(body: Signup, request: Request):
    name = body.name.strip()
    if len(name) < 2:
        raise HTTPException(422, "Enter your name.")
    email = norm_email(body.email)
    if email == GUEST_EMAIL:
        raise HTTPException(422, "That email address is reserved.")
    if len(body.password) < 6:
        raise HTTPException(422, "Password needs at least 6 characters.")
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "") or "")
    with pool.connection() as c:
        c.execute("select pg_advisory_xact_lock(%s)", (739311603,))
        domain = _effective_signup_domain(active_spec(c))
        if domain and email not in ADMIN_EMAILS and not email.endswith('@' + domain):
            raise HTTPException(422, 'Sign-up is limited to @' + domain + ' work emails.')
        if c.execute("select 1 from users where lower(email) = %s", (email,)).fetchone():
            raise HTTPException(409, "That email is already registered. Sign in instead.")
        # Decide status: ADMIN_EMAILS and pre-approved emails skip the queue.
        # The very first account becomes admin so the dashboard is never locked out.
        total = c.execute("select count(*) as n from users where not is_guest").fetchone()["n"]
        pre = c.execute("select 1 from allowed_emails where email = %s", (email,)).fetchone()
        is_admin = email in ADMIN_EMAILS
        if total == 0:
            is_admin = True
        approved = bool(pre) or is_admin or total == 0
        status = "approved" if approved else "pending"
        assigned_roles = _signup_roles(c, is_admin)
        try:
            r = c.execute(
                """insert into users (name, email, password_hash, is_admin, status, signup_ip, roles, decided_at, decided_by)
                   values (%s,%s,%s,%s,%s,%s,%s, case when %s then now() else null end, case when %s then 'auto' else null end)
                   returning id, name, email, is_admin, is_reviewer, roles, status, signup_ip, created_at""",
                (name, email, hash_password(body.password), is_admin, status, ip[:45], assigned_roles, approved, approved),
            ).fetchone()
        except Exception as e:
            if isinstance(e, pgerr.UniqueViolation):
                raise HTTPException(409, "That email is already registered. Sign in instead.")
            raise
        token = issue_session(c, r["id"]) if status == "approved" else None
    out = user_public(r)
    out["token"] = token
    return out


@app.post("/api/auth/login")
def login(body: Login):
    email = norm_email(body.email)
    if email == GUEST_EMAIL:
        raise HTTPException(401, "Wrong email or password.")
    with pool.connection() as c:
        r = c.execute("select id, name, email, password_hash, is_admin, is_reviewer, roles, status, created_at from users where lower(email) = %s", (email,)).fetchone()
        if not r or not check_password(body.password, r["password_hash"]):
            raise HTTPException(401, "Wrong email or password.")
        if r["status"] == "pending":
            raise HTTPException(403, "Your account is waiting for admin approval. Ask an admin to approve it.")
        if r["status"] == "rejected":
            raise HTTPException(403, "Your account was not approved. Ask an admin if you need access.")
        _assign_unset_roles(c, r)
        token = issue_session(c, r["id"])
    out = user_public(r)
    out["token"] = token
    return out


@app.post("/api/auth/guest")
def guest_login():
    """Open a short-lived session on the single shared guest account.

    No new user rows are ever created (one reserved row), sessions expire after
    GUEST_SESSION_HOURS and at most GUEST_MAX_SESSIONS stay live (oldest evicted).
    The guest gets only the ordinary member roles, never admin or reviewer.
    """
    if not guest_login_enabled():
        raise HTTPException(404, "Guest login is disabled.")
    with pool.connection() as c:
        c.execute("select pg_advisory_xact_lock(%s)", (739311603,))
        roles = [x for x in _signup_roles(c, False) if x not in ("admin", "reviewer")]
        r = c.execute(
            """insert into users (name, email, password_hash, is_admin, is_reviewer, is_guest, status, roles, decided_at, decided_by)
               values ('Guest', %s, '!', false, false, true, 'approved', %s, now(), 'auto')
               on conflict (lower(email)) do update
                 set roles = excluded.roles, is_admin = false, is_reviewer = false, is_guest = true, status = 'approved'
               returning id, name, email, is_admin, is_reviewer, roles, status, signup_ip, created_at""",
            (GUEST_EMAIL, roles),
        ).fetchone()
        token = new_token()
        c.execute("insert into sessions (token, user_id, expires_at) values (%s,%s, now() + make_interval(hours => %s))",
                  (token, r["id"], GUEST_SESSION_HOURS))
        c.execute("delete from sessions where user_id = %s and (expires_at < now() or token not in "
                  "(select token from sessions where user_id = %s order by created_at desc limit %s))",
                  (r["id"], r["id"], GUEST_MAX_SESSIONS))
    out = user_public(r)
    out["is_guest"] = True
    out["token"] = token
    return out


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(default=None)):
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        with pool.connection() as c:
            c.execute("delete from sessions where token = %s", (token,))
    return {"ok": True}


# ---------------------------------------------------------------- helpers

DOMAIN_RE = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")


# ---------------------------------------------------------------- public

@app.get("/api/health")
def health():
    with pool.connection() as c:
        c.execute("select 1")
    return {"ok": True}


@app.get("/api/meta")
def meta():
    with pool.connection() as c:
        stored = active_spec(c)
    return {'signup_domain': _effective_signup_domain(stored), 'guest_login': guest_login_enabled(), 'title': stored['spec']['app']['title'] if stored else 'schemasmith'}


@app.get("/api/me")
def me(w: Optional[Who] = Depends(who_opt)):
    return w


# ---------------------------------------------------------------- admin: users + pre-approved emails

@app.get("/api/admin/users")
def admin_users(status: Literal["pending", "approved", "rejected", "all"] = "all", _: Who = Depends(admin)):
    with pool.connection() as c:
        rows = c.execute(
            "select id, name, email, is_admin, is_reviewer, roles, status, signup_ip, created_at, decided_at, decided_by from users where not is_guest order by created_at desc limit 500"
        ).fetchall()
    if status != "all":
        rows = [r for r in rows if r["status"] == status]
    return rows


def _refuse_guest(row):
    if row and row.get("is_guest"):
        raise HTTPException(400, "The guest account cannot be changed.")


@app.post("/api/admin/users/{user_id}/approve")
def approve_user(user_id: uuid.UUID, w: Who = Depends(admin)):
    with pool.connection() as c:
        r = c.execute("select id, email, status, is_guest from users where id = %s", (user_id,)).fetchone()
        if not r:
            raise HTTPException(404, "User not found.")
        _refuse_guest(r)
        c.execute("update users set status = 'approved', decided_at = now(), decided_by = %s where id = %s", (w.email, user_id))
        # Remember the email so re-registration skips the queue.
        c.execute("insert into allowed_emails (email, added_by) values (%s,%s) on conflict (email) do nothing", (r["email"].lower(), w.email))
        return c.execute(
            "select id, name, email, is_admin, is_reviewer, roles, status, signup_ip, created_at, decided_at, decided_by from users where id = %s", (user_id,)
        ).fetchone()


@app.post("/api/admin/users/{user_id}/reject")
def reject_user(user_id: uuid.UUID, w: Who = Depends(admin)):
    with pool.connection() as c:
        row = c.execute("select id, is_guest from users where id = %s", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found.")
        _refuse_guest(row)
        c.execute("update users set status = 'rejected', decided_at = now(), decided_by = %s where id = %s", (w.email, user_id))
        return c.execute(
            "select id, name, email, is_admin, is_reviewer, roles, status, signup_ip, created_at, decided_at, decided_by from users where id = %s", (user_id,)
        ).fetchone()


class AdminFlag(BaseModel):
    is_admin: bool


class ReviewerFlag(BaseModel):
    is_reviewer: bool


class UserRoles(BaseModel):
    model_config = ConfigDict(extra='forbid')
    roles: list[str] = Field(min_length=1, max_length=20)


@app.post('/api/admin/users/{user_id}/roles')
def set_user_roles(user_id: uuid.UUID, body: UserRoles, w: Who = Depends(admin)):
    with pool.connection() as c:
        c.execute('select pg_advisory_xact_lock(%s)', (739311604,))
        row = c.execute('select id,is_admin,roles,status,is_guest from users where id = %s for update', (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'User not found.')
        _refuse_guest(row)
        stored = active_spec(c)
        declared = set(stored['spec']['roles']) if stored else {'admin', 'reviewer'}
        if len(body.roles) != len(set(body.roles)) or not set(body.roles) <= declared:
            raise HTTPException(422, 'Assign distinct roles declared by the active spec.')
        assigned = sorted(body.roles)
        if row['status'] == 'approved' and (row['is_admin'] or 'admin' in row['roles']) and 'admin' not in assigned:
            _protect_last_admin(c)
        return c.execute('update users set roles = %s,is_admin = %s,is_reviewer = %s where id = %s '
                         'returning id,name,email,is_admin,is_reviewer,roles,status,signup_ip,created_at,decided_at,decided_by',
                         (assigned, 'admin' in assigned, 'reviewer' in assigned, user_id)).fetchone()


def _protect_last_admin(c):
    count = c.execute("select count(*) as n from users where (is_admin or 'admin' = any(roles)) and status = 'approved'").fetchone()['n']
    if count <= 1:
        raise HTTPException(400, 'You are the only admin. Make someone else admin first.')


@app.post("/api/admin/users/{user_id}/reviewer")
def set_reviewer(user_id: uuid.UUID, body: ReviewerFlag, w: Who = Depends(admin)):
    """Admin assigns (or removes) the reviewer role: read-only approvals oversight + export."""
    with pool.connection() as c:
        r = c.execute("select id, is_guest from users where id = %s", (user_id,)).fetchone()
        if not r:
            raise HTTPException(404, "User not found.")
        _refuse_guest(r)
        c.execute("update users set is_reviewer = %s, roles = case when %s then "
                  "array(select distinct role from unnest(roles || array['reviewer']) role order by role) "
                  "else array_remove(roles, 'reviewer') end where id = %s",
                  (body.is_reviewer, body.is_reviewer, user_id))
        return c.execute(
            "select id, name, email, is_admin, is_reviewer, roles, status, signup_ip, created_at, decided_at, decided_by from users where id = %s", (user_id,)
        ).fetchone()


@app.post("/api/admin/users/{user_id}/admin")
def set_admin(user_id: uuid.UUID, body: AdminFlag, w: Who = Depends(admin)):
    with pool.connection() as c:
        c.execute('select pg_advisory_xact_lock(%s)', (739311604,))
        r = c.execute("select id, is_guest from users where id = %s", (user_id,)).fetchone()
        if not r:
            raise HTTPException(404, "User not found.")
        _refuse_guest(r)
        if str(user_id) == w.id and not body.is_admin:
            # Don't let the last admin path lock itself out accidentally via UI; allow but warn via count check.
            _protect_last_admin(c)
        c.execute("update users set is_admin = %s, roles = case when %s then "
                  "array(select distinct role from unnest(roles || array['admin']) role order by role) "
                  "else array_remove(roles, 'admin') end where id = %s",
                  (body.is_admin, body.is_admin, user_id))
        return c.execute(
            "select id, name, email, is_admin, is_reviewer, roles, status, signup_ip, created_at, decided_at, decided_by from users where id = %s", (user_id,)
        ).fetchone()


@app.get("/api/admin/allowed-emails")
def list_allowed(_: Who = Depends(admin)):
    with pool.connection() as c:
        return c.execute("select email, added_by, created_at from allowed_emails order by email").fetchall()


class AllowEmail(BaseModel):
    email: str = Field(min_length=3, max_length=120)


@app.post("/api/admin/allowed-emails", status_code=201)
def add_allowed(body: AllowEmail, w: Who = Depends(admin)):
    email = norm_email(body.email)
    with pool.connection() as c:
        domain = _effective_signup_domain(active_spec(c))
        if domain and email not in ADMIN_EMAILS and not email.endswith('@' + domain):
            raise HTTPException(422, 'Only @' + domain + ' emails can sign up, so pre-approving anything else is pointless.')
        c.execute("insert into allowed_emails (email, added_by) values (%s,%s) on conflict (email) do nothing", (email, w.email))
        # Auto-approve anyone already waiting with this email.
        c.execute("update users set status = 'approved', decided_at = now(), decided_by = %s where lower(email) = %s and status = 'pending'", (w.email, email))
        return c.execute("select email, added_by, created_at from allowed_emails where email = %s", (email,)).fetchone()


@app.delete("/api/admin/allowed-emails/{email}")
def remove_allowed(email: str, _: Who = Depends(admin)):
    with pool.connection() as c:
        c.execute("delete from allowed_emails where email = %s", (email.strip().lower(),))
    return {"ok": True}


# ---------------------------------------------------------------- admin: requests


# ---------------------------------------------------------------- spec-driven reports, exports and dashboard
#
# Every column, label, status mapping and tile below comes from the active
# spec's views/exports/dashboard declarations. The engine module interprets
# the closed selector graph; no spec string is ever interpolated into SQL.

def _report_view(spec, name):
    matches = [v for v in spec.views if v.type == "report" and (v.name or v.entity) == name]
    if len(matches) != 1:
        raise HTTPException(404, "Unknown report.")
    return matches[0]


def _guard_report(w, view, spec):
    if view.roles:
        workflow.require_role(w, *view.roles, spec=spec)
    return w


@app.get("/api/reports/{name}")
def spec_report(name: str, w: Who = Depends(who)):
    with pool.connection() as c:
        spec = records.current_spec(c)
        view = _report_view(spec, name)
        _guard_report(w, view, spec)
        headers, rows = engine_export.report_rows(c, spec, view)
    return {"view": view.name or view.entity, "headers": headers, "rows": rows}


@app.get("/api/reports/{name}.{fmt}")
def spec_report_file(name: str, fmt: Literal["csv", "xlsx"], w: Who = Depends(who)):
    with pool.connection() as c:
        spec = records.current_spec(c)
        view = _report_view(spec, name)
        _guard_report(w, view, spec)
        download = engine_export.export_report(c, spec, view, fmt)
    return Response(download.content, media_type=download.mime,
                    headers={"Content-Disposition": 'attachment; filename="' + download.filename + '"'})


@app.get("/api/exports/{name}.{fmt}")
def spec_export_file(name: str, fmt: Literal["txt", "csv", "xlsx"], w: Who = Depends(who)):
    with pool.connection() as c:
        spec = records.current_spec(c)
        matches = [e for e in spec.exports if e.name == name]
        if len(matches) != 1:
            raise HTTPException(404, "Unknown export.")
        workflow.require_role(w, "admin", spec=spec)
        download = engine_export.export_declared(c, spec, name, fmt)
    return Response(download.content, media_type=download.mime,
                    headers={"Content-Disposition": 'attachment; filename="' + download.filename + '"'})


@app.get("/api/dashboard")
def spec_dashboard(w: Who = Depends(who)):
    with pool.connection() as c:
        spec = records.current_spec(c)
        tiles = engine_export.dashboard(c, spec)
    return {"tiles": tiles}


# ---------------------------------------------------------------- static frontend


@app.get('/api/spec')
def public_spec():
    with pool.connection() as c:
        stored = active_spec(c)
    return {'spec': stored['spec'] if stored else None, 'version': stored['id'] if stored else None,
            'schema_edit': os.environ.get('SCHEMA_EDIT', '0') == '1',
            'designer_configured': bool(os.environ.get('LLM_MODEL', '').strip()),
            'guest_designer': guest_login_enabled() and os.environ.get('GUEST_DESIGNER', '0').strip() == '1'}


records.mount(app, pool, who, who_opt)
llm.mount(app, pool, admin, who)


class TransitionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    to_state: str = Field(min_length=1, max_length=41, pattern=r'^[a-z][a-z0-9_]{0,40}$')
    line_id: uuid.UUID | None = None
    data: dict = Field(default_factory=dict)


@app.post('/api/e/{entity}/{record_id}/transition')
def transition_record(entity: str, record_id: uuid.UUID, body: TransitionInput, w: Who = Depends(who)):
    with pool.connection() as c:
        spec = records.current_spec(c)
        return workflow.transition(c, spec, records.entity_of(spec, entity), record_id, w,
                                   body.to_state, line_id=body.line_id, data=body.data)

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
