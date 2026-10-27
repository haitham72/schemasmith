"""Postgres pool, engine bootstrap and optional spec/example loading."""
import importlib
import os
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DSN = os.environ["DATABASE_URL"]

pool = ConnectionPool(DSN, min_size=1, max_size=8, kwargs={"row_factory": dict_row}, open=False)

# Whitelisted first-boot examples: name -> loader in examples.<name>.seed.
# Only these modules are ever imported; EXAMPLE cannot point anywhere else.
EXAMPLE_LOADERS = {
    "access_requests": "seed_catalog",
    "equipment_checkout": "seed_assets",
}


def init_db() -> None:
    from .migrate import active, apply, plan, table_name, tables
    from .spec import validate

    pool.open(wait=True, timeout=60)
    root = Path(__file__).resolve().parent.parent
    with pool.connection() as conn:
        conn.execute((root / "app/core.sql").read_text(encoding="utf-8"))
        stored = active(conn)
        if stored:
            # The DDL is persisted transactionally. Verify the stored spec's
            # tables; silently recreating missing tables would hide data loss.
            spec = validate(stored["spec"])
            for name in tables(spec):
                if conn.execute("select to_regclass(%s) as name", (table_name(name),)).fetchone()["name"] is None:
                    raise RuntimeError("An active spec table is missing; restore the database backup")
        else:
            example = os.environ.get("EXAMPLE", "").strip()
            if not example:
                return
            if example not in EXAMPLE_LOADERS:
                raise RuntimeError("EXAMPLE must be access_requests, equipment_checkout, or empty")
            spec = validate((root / "examples" / example / "spec.json").read_bytes())
            reviewed = plan(conn, spec)
            apply(conn, spec, reviewed.plan_hash)
            loader = getattr(importlib.import_module("examples." + example + ".seed"),
                             EXAMPLE_LOADERS[example])
            loader(conn)
