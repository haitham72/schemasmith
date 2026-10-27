![schemasmith: describe a use case, get the database and the app](assets/hero.png)

# schemasmith

**Describe your use case; AI designs the database and the app builds itself.**

[![CI](https://github.com/haitham72/schemasmith/actions/workflows/ci.yml/badge.svg)](https://github.com/haitham72/schemasmith/actions/workflows/ci.yml)
[![MIT](assets/badge-license.svg)](LICENSE)
[![Python 3.12](assets/badge-python.svg)](requirements.txt)
[![Postgres 16](assets/badge-postgres.svg)](docker-compose.yml)

![Demo: prompt a use case, review the plan, apply, get the app](assets/demo.gif)

Real recording of the running app (light theme, a stand-in model server, a dummy key).

## Why

Internal tools are usually a table, a form, an approval and an export. Writing
them by hand is slow, and a no-code builder hides the schema. schemasmith lets
a model draft a validated spec, shows you the exact migration plan, and renders
the app from the spec: Postgres, FastAPI and vanilla JS, with no frontend build
and no CDN.

## Features

- **Prompt first.** One screen: describe the use case, paste an OpenAI key, Generate.
  Then review the plan and apply. Any OpenAI-compatible endpoint or local Ollama works too.
- **Migration safety.** Plans are classified (additive, risky, destructive). Removed
  tables and columns are archived, not dropped. Apply needs the reviewed plan hash;
  a stale or altered plan is rejected with a 409. A real DROP needs a separate confirmation.
- **No model-written SQL.** The model returns a JSON spec; a closed validator checks it and
  the engine generates DDL with quoted identifiers and bound parameters.
- **XSS-tested rendering.** Every spec label and stored value is escaped; a hostile-spec
  and live-browser XSS suite runs in CI.
- **Workflows and roles.** States, exact roles, owner checks, per-line decisions, daily limits,
  comments, reports and TXT/CSV/XLSX exports, all from the spec.
- **Guest mode and themes.** "Continue as guest" for demos (see below). Light theme first,
  dark behind a toggle.

## Quickstart

Requires Docker with Compose v2. Three commands in a POSIX shell (the second writes a
random database password without printing it):

```sh
cp .env.example .env
python -c 'from pathlib import Path; import secrets; p=Path(".env"); p.write_text(p.read_text().replace("DB_PASSWORD=\n", "DB_PASSWORD="+secrets.token_hex(32)+"\n", 1))'
docker compose up -d --build
```

Open [localhost:8000](http://localhost:8000) and click **Continue as guest** (or sign up; the
first real account becomes admin). On an empty install you land on the prompt screen.
To start from a ready-made app instead, set `EXAMPLE=access_requests` (or
`equipment_checkout`) in `.env` before the first start; see [the examples](examples/README.md).
Without host Python, put a long random URL-safe `DB_PASSWORD` in `.env` by hand. Keep `.env` private.

| Prompt | Review the design | Review the plan |
| --- | --- | --- |
| ![Prompt screen](assets/screenshots/03-design-prompt-filled.png) | ![Generated design](assets/screenshots/04-designer-spec.png) | ![Migration plan](assets/screenshots/05-designer-plan.png) |

| Browse (access requests example) | Request form | Your requests |
| --- | --- | --- |
| ![Catalog](assets/screenshots/07-catalog.png) | ![Request form](assets/screenshots/08-request-form.png) | ![My requests](assets/screenshots/09-my-request.png) |

*Enhanced renders of the actual UI.*

## Bring your own OpenAI key

Paste a key on the prompt screen. It stays in your browser (sessionStorage; localStorage
only if you tick "remember on this device") and is sent in an `X-OpenAI-Key` header for
that request only. The server never stores or logs it, scrubs it from errors, and sends it
only to `https://api.openai.com/v1`: the endpoint is fixed, so a user key cannot be
pointed at another host. The model is `gpt-4o-mini` unless `OPENAI_MODEL` is set.
Self-hosters can instead configure the server with `LLM_BASE_URL`, `LLM_MODEL` and
`LLM_API_KEY` (any OpenAI-compatible endpoint). For a local model:

```sh
docker compose --profile llm up -d
docker compose exec ollama ollama pull qwen3:8b
# in .env: LLM_BASE_URL=http://ollama:11434/v1  LLM_MODEL=qwen3:8b
docker compose up -d --force-recreate web
```

See [local model setup](docs/llm.md).

## Guest mode

`GUEST_LOGIN=1` shows "Continue as guest": one shared, non-admin account with 24-hour
sessions. Guests can browse and create or edit their own records. They cannot manage users
or settings, cannot be promoted, and never count as the first signup. `GUEST_DESIGNER=1`
also lets a guest run the designer with their own key (never the server's), though a guest
cannot confirm risky or destructive migrations. Both are on in `.env.example` for local
demos. **Turn both off (`0`) and set `SCHEMA_EDIT=0` for any shared or real deployment.**

## Architecture

```mermaid
flowchart LR
  A[Use case + key] --> B[LLM]
  B --> C[JSON spec]
  C --> D[Closed validator]
  D --> E[Migration plan + hash]
  E --> F[Human review]
  F --> G[(Postgres)]
  C --> H[Forms, queues, reports, exports]
  G --> H
```

`app/` is FastAPI (`spec.py` validator, `migrate.py` planner, `records.py` and
`workflow.py` runtime, `export.py`, `llm.py` the only model boundary); `static/` is the
bundled vanilla-JS UI and fonts. Details: [spec reference](docs/spec.md),
[migrations](docs/migrations.md), [security](SECURITY.md).

## Configuration

Full table in [docs/configuration.md](docs/configuration.md). The ones you will touch:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DB_PASSWORD` | required | Postgres password (no default) |
| `EXAMPLE` | empty | Preload `access_requests` or `equipment_checkout` on a new database |
| `SCHEMA_EDIT` | `0` (`1` in the example env) | Enable designer generate, plan and apply |
| `GUEST_LOGIN`, `GUEST_DESIGNER` | `0` (`1` in the example env) | Guest sign-in; guest designer |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model for user-supplied keys |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | empty | Server-side OpenAI-compatible model (Ollama, LM Studio, ...) |
| `ADMIN_EMAILS`, `SIGNUP_DOMAIN` | empty | Pre-set admins; restrict signups to a domain |

## Development and tests

```sh
python -m pip install -r requirements-dev.txt
# a disposable Postgres 16 database whose name contains "test":
export DATABASE_URL=postgresql://user:pass@localhost:5432/schemasmith_test
EXAMPLE=access_requests SIGNUP_DOMAIN=example.test RUN_POSTGRES_TESTS=1 python -m pytest -q --ignore=tests/e2e
python -m playwright install chromium
RUN_E2E=1 python -m pytest -q tests/e2e
for f in static/*.js; do node --check "$f"; done
```

Current result on Postgres 16: **493 passed, 5 skipped**, plus **6 browser e2e passed**. No test
calls a paid API; the designer tests use a loopback fake server. See [CONTRIBUTING](CONTRIBUTING.md).

## Known limitations

- Designer quality with 7-8B local models is unverified. The pipeline is tested against a
  stub server; expect to review and edit generated specs.
- The guest account is shared: guests see each other's records, and with `GUEST_DESIGNER=1` a
  guest can change the shared schema. Use it for demos only.
- Postgres only, local accounts only (no SSO, no password reset), a deliberately narrow field
  and view set, and no multi-tenancy. An early release with no independent security audit.

See also the [comparison with Retool, NocoDB, Appsmith and Budibase](docs/comparison.md).

## License

[MIT](LICENSE). Source: <https://github.com/haitham72/schemasmith>.
