# Bundled examples

Examples are independent starting points for separate installations. They contain
synthetic/demo data, never real accounts, requests, or checkout histories.

| Example | Shape | Workflow |
| --- | --- | --- |
| `access_requests` | Hierarchical topics, developer tools, request headers with selected-tool lines | Admin approve/deny/ask, requester justification, per-line rollup, ten submissions per user per UTC day |
| `equipment_checkout` | Fifteen assets with unique tags and an enum condition; checkout headers reference assets | Requested → approved → checked out → returned, or requested → rejected |

On a new database set `EXAMPLE=access_requests` or `EXAMPLE=equipment_checkout` in
`.env`, then start Compose. Bootstrap validates/applies the selected spec before
loading reference data. The access seed retains the original neutral catalog and
nesting; known domains are sample entries requiring operator review. Equipment
assets are entirely synthetic. Broken equipment is included to exercise enum
rendering; the example does not implement availability or condition-based policy.

On a database with an active spec, changing `EXAMPLE` does nothing. Use another
Compose project with distinct web/database ports or a separate deployment to try
the other example. Do not remove a database volume just to switch examples.

Leave `EXAMPLE` empty to start with no spec. The first signup becomes admin.
Configure a local model and enable schema editing to describe an application, or
review a bundled JSON spec through the designer's JSON/plan workflow. A record-level
checkout is created through its form; equipment fields are not request line items.

Each `seed.py` is a small Python loader called once at bootstrap. The loaders only
target generated `x_` tables, accept `dynamic=True` by default for compatibility,
and refuse the obsolete legacy mode. They stop if the primary reference table
already contains rows; they do not synchronize or overwrite an edited example.
