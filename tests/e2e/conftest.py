"""Isolated disposable database per example; skips without RUN_E2E=1.

Each example gets a freshly wiped database state (sequential, same disposable
test database): all engine tables, every ``x_*`` record table and the migration
ledger are dropped, then ``core.sql`` is re-applied and the example spec is
planned and applied. DATABASE_URL must point at a disposable test database
(its name must contain ``test``) -- never a real one.
"""
import os

import pytest

EXAMPLES = ["access_requests", "equipment_checkout"]


def _guard_database_url():
    if os.environ.get("RUN_E2E") != "1":
        pytest.skip("Browser/API smoke needs RUN_E2E=1 on a disposable test database")
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    if "test" not in dsn.split("@")[-1].split("?")[0].lower():
        pytest.skip("Refusing to wipe a database whose name does not contain 'test'")
    return dsn


@pytest.fixture(scope="session")
def any_db():
    _guard_database_url()
    return os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
def client_for():
    """Install one example spec on the disposable database, yield a TestClient."""
    _guard_database_url()
    from pathlib import Path

    import psycopg
    from fastapi.testclient import TestClient

    from app.main import app
    from app.migrate import apply, plan
    from app.spec import validate

    # Staged tests run with PYTHONPATH at the product root.
    root = Path(os.environ.get("SCHEMASMITH_TARGET", str(Path(__file__).resolve().parents[2])))

    def install(name):
        spec = validate((root / "examples" / name / "spec.json").read_bytes())
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            tables = [r[0] for r in conn.execute(
                "select tablename from pg_tables where schemaname = 'public'").fetchall()]
            for table in tables:
                if table.startswith("x_") or table in (
                        "users", "sessions", "allowed_emails", "comments",
                        "ss_spec", "ss_migrations", "ss_workflow_events"):
                    conn.execute(f'drop table if exists "{table}" cascade')
            conn.execute((root / "app/core.sql").read_text(encoding="utf-8"))
            reviewed = plan(conn, spec)
            apply(conn, spec, reviewed.plan_hash)
        return spec

    with TestClient(app) as client:
        yield client, install


def start_server():
    """Run the app in its own uvicorn process (the in-process TestClient owns the DB pool).

    Returns (base_url, stop). The process reads DATABASE_URL from the environment.
    """
    import socket
    import subprocess
    import sys
    import time
    import urllib.request
    from pathlib import Path

    root = Path(os.environ.get("SCHEMASMITH_TARGET", str(Path(__file__).resolve().parents[2])))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if key != "EXAMPLE"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "error"],
        cwd=root, env=env)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("test server exited during startup")
        try:
            urllib.request.urlopen(base + "/api/spec", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("test server did not start")

    def stop():
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except Exception:
            proc.kill()

    return base, stop
