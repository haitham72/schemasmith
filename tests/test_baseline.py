"""Fixed requests and decisions freeze the allowlist export CSV byte for byte."""
import os
from pathlib import Path

from app.db import pool
from examples.access_requests.seed import seed_catalog
from tests.test_api import decide, ensure_admin, hdr, signup, tool_id

FIXTURE = Path(__file__).parent / "fixtures" / "allowlist_baseline.txt"


def scripted_allowlist(client):
    # This matrix is deliberately independent of random IDs and earlier test requests.
    with pool.connection() as conn:
        conn.execute("truncate ss_workflow_events, comments, x_request_lines, x_request, sessions, users, allowed_emails")
        seed_catalog(conn, dynamic=True)
    admin = ensure_admin(client)
    people = ["baseline-one@example.test", "baseline-two@example.test"]
    tools = {name: tool_id(client, name) for name in
             ["PyPI (all packages)", "npm (all packages)", "LM Studio", "Hugging Face Hub", "Docker Hub"]}
    scripted = [
        (people[0], {"PyPI (all packages)": "approved", "npm (all packages)": "denied", "LM Studio": "approved"}),
        (people[1], {"PyPI (all packages)": "approved", "Hugging Face Hub": "approved", "Docker Hub": "pending"}),
    ]
    for index, (email, decisions) in enumerate(scripted, 1):
        result = client.post("/api/admin/allowed-emails", headers=hdr(admin), json={"email": email})
        assert result.status_code == 201, result.text
        result = signup(client, f"Demo Requester {index}", email, "demo-test-password")
        assert result.status_code == 201 and result.json()["token"], result.text
        token = result.json()["token"]
        result = client.post("/api/e/request", headers=hdr(token), json={
            "reason": f"Synthetic baseline request {index}", "lines": [tools[name] for name in decisions]})
        assert result.status_code == 201, result.text
        rid = result.json()["id"]
        decide(client, admin, rid, {tools[name]: state for name, state in decisions.items()})
    result = client.get("/api/exports/allowlist.csv", headers=hdr(admin))
    assert result.status_code == 200, result.text
    return result.content


def test_allowlist_parity_baseline(_database):
    actual = scripted_allowlist(_database)
    if os.environ.get("UPDATE_BASELINE") == "1":
        FIXTURE.parent.mkdir(exist_ok=True)
        FIXTURE.write_bytes(actual)
    assert FIXTURE.exists(), "Capture the parity baseline once before changing the engine."
    assert actual == FIXTURE.read_bytes()
