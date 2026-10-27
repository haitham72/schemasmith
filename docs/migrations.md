# Reviewed migrations

Model output is a spec, never executable SQL. The engine validates it, compares it
with the active version and builds SQL with Psycopg identifiers and bound values.
Generated application tables use `x_`; auth/session/spec/migration tables remain
engine-owned. Applying a migration requires an approved admin and `SCHEMA_EDIT=1`.

| Classification | Examples | Apply behavior |
| --- | --- | --- |
| Additive | New table, nullable/defaulted field, index | Eligible to apply after reviewing its plan/hash; never auto-applied by model generation. |
| Risky | Type change, narrower constraint, unique requirement, required field without default | Counts potentially violating rows; requires explicit risky confirmation and backup. Violating rows block apply. |
| Destructive | Removed field/table or unhinted replacement | Default plan renames data to `_archived_<tag>_<name>`. Real DROP requires `confirm_destructive` equal to the reviewed plan hash. |

The normal path preserves removed data by archiving. The explicit destructive
override is irreversible and must not be treated as routine confirmation.
Archived objects can retain disk space and constraints. They are not a backup.
Back up the database before risky changes and test restoration separately.

## A worked additive change

Add this field to the equipment example's `asset.fields` list:

```json
{"name":"storage_location","type":"text","default":"Shelf A"}
```

The following SQL/parameters are actual output from the deterministic planner,
run locally with `plan(None, new, old_spec=old)`; no database was contacted:

```json
{
  "sql": "alter table \"x_asset\" add column \"storage_location\" text default %s::text",
  "parameters": ["Shelf A"]
}
```

The operation is `add_column`, classified `additive`. Offline `rows` and
`violating_rows` are null because no database counts were possible. This output
demonstrates generation and parameter separation. On the live planning route, inspect the returned
counts, classifications, SQL and parameters before applying.

To reproduce the offline example from the repository root:

```sh
python - <<'PY'
import json
from copy import deepcopy
from pathlib import Path
from app.migrate import plan
old = json.loads(Path("examples/equipment_checkout/spec.json").read_text())
new = deepcopy(old)
new["entities"][0]["fields"].append({"name": "storage_location", "type": "text", "default": "Shelf A"})
print(json.dumps(plan(None, new, old_spec=old).public()["sql"], indent=2))
PY
```

## Review and apply

The API contract is `POST /api/spec/plan` with `{spec: <full object>}`, then
`POST /api/spec/apply` with the same spec and its `plan_hash`. Risky confirmation
is a separate `confirm_risky` boolean; destructive confirmation is the exact hash,
not a boolean. The UI must show a fresh plan after any spec edit.

Apply takes a Postgres transaction advisory lock, replans against the current
database and checks the hash. A changed spec, active version or row-impact count
can require a new review. All DDL and active-spec/migration log writes commit in
one transaction; any failure rolls the transaction back. The lock serializes
engine migrations, not arbitrary external database clients.

An intentional rename uses `renamed_from` on the new entity/field. Omitting that
hint means add/remove, which archives the old object. Archives use stable reviewed
names; identifier-length handling is deterministic. Spec versions are recorded in
`ss_spec`, and operations/SQL in `ss_migrations`.

## Reverting and recovery

Reverting means preparing the previous spec as a new inverse plan and reviewing it
under the same rules. It is not a one-click restore of data removed by an explicit
DROP or of old values changed by the application. Existing archives may require
an operator-reviewed restore/rename strategy; applying an inverse spec does not
automatically reconnect every archived object. Keep database backups, migration
history and application spec versions together.
