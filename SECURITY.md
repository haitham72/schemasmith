# Security

This is an early self-hosted release. The operator is responsible for trusted
administrator access, backups, TLS, network boundaries and database credentials.
Use a private installation until the deployment and security checks required for
your environment have been completed. No independent security audit is claimed.

## Threat model

Untrusted inputs include model responses, spec JSON, CSV uploads, record values,
labels and requests from authenticated users. The model endpoint is selected by
a trusted operator. An administrator can change the application design and should
be treated as privileged; database administrators can bypass application policy.

- A closed validated spec accepts known keys/types/literal defaults and bounded
  identifiers. Model text is never evaluated as Python, JavaScript or SQL.
- Migration SQL uses `psycopg.sql.Identifier` for identifiers and bound values.
  A plan hash binds review to apply; normal destructive changes archive objects.
  Risky/destructive overrides are explicit. Review and retain backups.
- Schema generation/plan/apply require admin authorization and `SCHEMA_EDIT=1`; guests
  are allowed only when `GUEST_DESIGNER=1` and cannot confirm risky or destructive migrations.
- A user-supplied OpenAI key (`X-OpenAI-Key`) is used for one request, never stored or
  logged, and is sent only to the fixed OpenAI endpoint; it cannot select a base URL.
- `GUEST_LOGIN` / `GUEST_DESIGNER` are for local demos. Turn them off on shared deployments.
  The in-process rate limiter is not a shared multiworker or perimeter limiter.
- Record/workflow guards enforce roles and owner checks. Transition graph roles
  are exact; administrator status alone does not grant an owner-only transition.
- UI renderers must escape all spec strings and stored values. Navigation
  visibility cannot replace server-side authorization. CSV/XLSX cells must not
  turn untrusted values into spreadsheet formulas.
- `.env` contains secrets and is excluded from version control/container context.
  Local passwords and opaque session tokens require HTTPS on a shared network.

The installation is one application, not a multi-tenant boundary. It provides no
SSO or general database row-level-security sandbox. A configured remote model
receives prompts/current specs; see [LLM privacy](docs/llm.md). Dependency/model
downloads may require external access even when ordinary runtime screens do not.

## Reporting

Use the repository's private vulnerability-reporting channel when it is enabled.
If private reporting has not yet been enabled, request a private contact without
posting exploit details or sensitive records in a public issue. Include affected
version, reproduction steps and a synthetic minimal fixture. Do not include API
keys, passwords, database dumps or other people's data. There is no published
response-time guarantee or security support schedule yet.
