# Spec version 1

The authoritative machine reference is [spec/schema.json](../spec/schema.json),
generated from [app/spec.py](../app/spec.py). Unknown keys are rejected at every
model boundary. Send JSON literals only: no SQL, expressions, formulas or code.
Validation returns errors shaped `{path, code, message}`, using JSON paths.

This reference covers the full validated model, including the report/workbook
column configuration below. Every key named here is accepted by `validate`;
anything else is rejected.

## Document and identifiers

| Key | Meaning |
| --- | --- |
| `spec_version` | Required integer `1`. |
| `app` | Required object: `title` (display label), `slug` (identifier), optional `signup_domain` (literal domain or null). |
| `roles` | Required unique role identifiers, 1–20, including `admin`. Every non-admin role needs an entry view. |
| `entities` | Required list, at most 20 entities with unique names. |
| `workflow` | Optional single workflow object, or null. |
| `views` | Required list, at most 80 views. |
| `exports` | Optional list, default empty, at most 40 exports. |
| `dashboard` | Optional list, default empty, at most 40 tiles. |

Identifiers match `^[a-z][a-z0-9_]{0,40}$`. Engine names, `ss_` prefixes and SQL
reserved words are denied even though SQL identifiers are quoted. Labels are
1–240 characters. A complete UTF-8 document is at most 64 KiB. Duplicate JSON
keys and nonfinite numbers are invalid. References and role names must resolve;
entity parent cycles and generated-line-table name collisions are rejected.

## Entities and fields

Every entity has `name`, `kind` (`reference` or `record`), `fields` (at most 30),
optional `label`, and optional `renamed_from` (explicit migration rename hint).
Field names are unique within an entity. Record metadata cannot be redeclared:
`id`, `parent_id`, `owner_id`, `created_at`, `updated_at`, `archived_at`, `state`,
`sort`, and `active` belong to the engine.

Reference entities may declare `parent` (another entity) or `hierarchy` with
`max_depth` (1–12, default 4). Combining both is ambiguous and rejected. Record
entities may declare `owner` (a role) and `lines`: `entity_ref` points to the
selected reference entity, `min` is 0–500 (default 1), and `max` is 1–500 (default
80). `min` must not exceed `max`. Declared fields stay on the header. Lines have
their own generated table, parent record ID and reference ID.

Every field has `name`, `type`, optional `label`, optional `renamed_from`,
`required` (default false), `unique` (default false), and optional literal
`default`. Null defaults are allowed only for optional fields. Additional keys:

| Type | JSON value / extra keys |
| --- | --- |
| `text` | String; optional `min`/`max` character bounds. |
| `int` | Integer, excluding booleans; optional numeric `min`/`max`. |
| `number` | Finite number; optional numeric `min`/`max`. |
| `bool` | Boolean. |
| `date` | ISO date `YYYY-MM-DD`. |
| `datetime` | ISO timestamp containing `T` and an explicit timezone. |
| `enum` | String; required unique `values` list of 1–64 labels. |
| `ref` | UUID; required `to` entity name. |
| `domain` | Domain string, optionally with `*.` prefix. |
| `email` | Email-shaped string. |
| `url` | HTTP(S) URL with hostname. |
| `list<text>`, `list<int>`, `list<number>`, `list<bool>`, `list<date>`, `list<datetime>`, `list<domain>`, `list<email>`, `list<url>` | Array of the scalar type. |
| `list<enum>` | Array using the declared `values`. |
| `list<ref>` | Array of UUIDs targeting the declared `to` entity. |

Defaults must match the type and bounds. Text and numeric minimums must not exceed
maximums. No arbitrary SQL default/CHECK expression is accepted. Runtime forms and
record APIs validate typed data again; active references must exist.

## Workflow and permissions

`workflow` has required `entity` (record entity), `level` (`line` or `record`),
`initial`, `states` (1–12 unique identifiers), and `transitions` (at most 144).
Every state must be reachable from initial. Line workflows require `lines`.

A transition has `from` (nonempty list), `to`, and `roles` (nonempty list), plus
optional `collects` and `requires`, each naming one field. `collects` marks an
answer to collect in the destination state; entering that state does not require
the answer. `requires` requires a fresh nonempty value in the transition payload.
Declared fields use their normal type/bounds and update the header; implicit
fields are bounded text on the workflow target. Unknown transition data is denied.

Roles are exact. Admin has no automatic permission for an owner-only edge. If
authorization relies on the entity's owner role, `owner_id` must match the actor.
Transitions lock the header and then the affected line in a transaction. Record
archive status, current state, target state, line membership and authorization are
checked before changes. Record-level flows do not accept a line ID.

Optional `rollup` has `all` (state-to-label map), `mixed` and `none_decided`.
When none of the line states appears in `all`, return `none_decided`; a unanimous
mapped state returns its value; any decided mixture returns `mixed`. In Example A,
only approved/denied count as decisions, so pending/needs_info alone stays pending.
Optional `limits.per_user_per_day` is 1–10,000. Creation counts all records,
including archives, per owner and UTC calendar day; a transaction advisory lock
serializes concurrent submissions. Direct database writes bypass app policy.

## Views

All views have `type`, optional identifier `name`, optional `label`, and optional
nonempty `roles`. Omitted roles allow the view to serve all roles; data access is
still enforced by API guards. Navigation visibility is not authorization.

| `type` | Additional keys |
| --- | --- |
| `browse_pick` | Required `entity` (reference data) and `submit` (record); optional `tree` entity. |
| `my_records` | Required `entity`; displays owned records. |
| `queue` | Required `entity`; displays permitted review/transition actions. |
| `report` | Required `entity`; optional `columns`; optional `where`; `formats` defaults to `["csv","xlsx"]`; optional `workbook` (below). Declared `roles` are enforced. |
| `reference_editor` | Required nonempty `entities`; optional `import: "csv"`. |
| `exports` | No additional keys; exposes spec exports. |
| `dashboard` | No additional keys; exposes spec dashboard tiles. |

## Report columns and workbooks

A report column selects one value plus its display label; there is no free SQL
or formula language:

| Key | Meaning |
| --- | --- |
| `source` | Required selector string, 1–240 chars (a bare string is shorthand for `{source, label}` with the same text). |
| `label` | Required display label, 1–240 chars. |
| `empty` | Placeholder for missing values, default `""`, at most 240 chars. |

A report `where` holds literal filters (default empty): `state` must match the
entity's workflow states; other keys name declared fields with finite JSON
scalar values.

`workbook` configures the formatted XLSX download:

| Key | Meaning |
| --- | --- |
| `person_title` | Required label for the per-person sheet heading. |
| `state_source` | Required selector locating the workflow state on each row. |
| `state_labels` | Required nonempty map of workflow state → safe display label. |
| `state_colors` | Optional map of state → `positive`, `negative` or `waiting`; keys must reference a declared state label. |
| `matrix` | Required reviewer-matrix sheet config (below). |
| `summary` | Required overview-sheet config (below). |

`matrix` has `sheet`, `title`, `key` (row-identity selector), `columns`
(nonempty column list), `state_labels` (nonempty, workflow states only),
`count_value` (must be one of the declared safe state labels) and `total_label`
(default `"Total Allowed"`).

`summary` has `sheet`, `title`, `headers` (1–8 labels), `metrics` (nonempty)
and optional `per_person` with `title`, `name_label` (default `"Person"`) and
`metrics`. Each metric has `kind` (`owners`, `groups`, `items`, `records` or
`state`), `label`, optional `source` (required for `groups`), and optional
`state` (required for `state`; must be a workflow state).

Dropdown labels must omit commas and double quotes and fit 253 characters;
violations are validation errors, not silent truncation.

## Exports and dashboard

An export has `name`, optional `label`, required `from` (selector), optional
`where` (literal filters, default empty), `distinct` (default false), optional
`columns`, and required nonempty `formats` from `txt`, `csv`, `xlsx`. Declared
exports require admin. A selector starts with an entity, then traverses
references or `lines`, optionally naming the line reference entity, and ends in
a declared field or supported engine metadata. Example:
`request.lines.tool.domains`. It is a validated selector, never an SQL expression.

Each export column groups rows over a closed selector:

| Key | Meaning |
| --- | --- |
| `source` | Required selector, 1–240 chars; `$value` selects the grouped value itself. |
| `label` | Required display label. |
| `aggregate` | `value` (default), `join_distinct` or `none_true`. |
| `distinct_by` | Optional selector scoping the distinct grouping. |
| `true_label` / `false_label` | Labels for the `none_true` aggregate, defaults `"yes"` / `"no"`, at most 80 chars each. |
| `empty` | Placeholder for missing values, default `""`, at most 240 chars. |

Selectors stay inside one root entity: root fields, `lines` chains, `ref`
hops, `owner` id/name/email, and `parent` / `parent_path` walks. Anything else
is rejected.

A dashboard tile has `label`, `count` (entity name), and optional `where`.
`where.state` must match that entity's workflow states; other filters name declared
fields and contain finite JSON scalars. The special date filter `<today` in the
equipment example selects overdue dates; it does not enable general expressions.

## Reading the examples

[Access requests](../examples/access_requests/spec.json) demonstrates the three-table
shape: topic → tool reference data, owned request headers, and per-tool lines.
`reason` is required text on the header. Justification is implicit line text.
Administrative reset/redecision edges explicitly preserve the original editor's
behavior; requester answers keep their required justification and ownership check.
The rollup and ten-per-day limit are data in this spec. Its `approvals` report
declares six columns (category path, item name, target domains, business reason,
status, reviewer notes) plus the full workbook (reviewer matrix, summary and
per-person sheets); its `allowlist` export follows tool domains only from
approved lines, deduplicates values, and declares three grouped columns
(`domain`, `granted_by`, `verified`).

[Equipment checkout](../examples/equipment_checkout/spec.json) uses the same engine
with an asset reference field, due date and condition enum. Its workflow is on the
checkout header: requested → approved → checked_out → returned, or rejected.
Its `loans` report declares three columns (equipment, due, purpose); the
`overdue` export selects checked-out records whose due date is before today and
offers CSV+XLSX. There is no availability reservation, late fee calculation or
automatic state scheduler.
