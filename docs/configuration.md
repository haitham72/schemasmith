# Configuration

Copy `.env.example` to `.env`. Compose reads that file; direct Python execution
reads process environment variables. Restart/recreate web after changing values.
No default account, administrator email, database password, or model is supplied.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DB_PASSWORD` | required by Compose | Password for the bundled Postgres database. Use a long random URL-safe value, such as 32 hexadecimal bytes. Never commit `.env`. |
| `DB_PORT` | `54321` | Database port published only on `127.0.0.1`; the containers use `db:5432`. |
| `DATABASE_URL` | Compose constructs it; direct execution requires it | Psycopg connection string. A nonempty override connects web to a different database. URL-encode reserved characters in credentials. |
| `ADMIN_EMAILS` | empty | Comma-separated addresses allowed to become administrators. The first account created is admin even when this is empty. |
| `SIGNUP_DOMAIN` | empty | Optional signup email-domain restriction. A nonempty value overrides the active application's `app.signup_domain`; when both are empty, signup is open to any domain, subject to account approval. `ADMIN_EMAILS` always bypass the restriction. |
| `EXAMPLE` | empty | `access_requests` or `equipment_checkout`, loaded only when no active spec exists. Changing this never replaces a populated application. |
| `GUEST_LOGIN` | `0` | Set exactly `1` to show "Continue as guest" on the sign-in screen. Guests share one reserved non-admin account (no email or password), get 24-hour sessions (at most 200 live), receive only the ordinary member roles and can never be admin, reviewer or count as the first signup. Keep it `0` for real deployments. |
| `GUEST_DESIGNER` | `0` | Set exactly `1` (with `GUEST_LOGIN=1` and `SCHEMA_EDIT=1`) to let guests run designer generate, plan and apply with their own OpenAI key. Guests still cannot confirm risky or destructive migrations. Keep `0` on shared deployments: the guest account is shared, so a guest can change the schema for everyone. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used when a user supplies their own key. The endpoint is fixed to `https://api.openai.com/v1` and cannot be changed per request. |
| `SCHEMA_EDIT` | `0` | Set exactly `1` to permit admin designer generate/plan/apply routes. Turn it off when schema work is finished. |
| `LLM_BASE_URL` | Compose: `http://ollama:11434/v1`; direct Python: `http://localhost:11434/v1` | OpenAI-compatible API base URL, including `/v1`. Host LM Studio from Compose: `http://host.docker.internal:1234/v1`. |
| `LLM_MODEL` | empty | Exact model identifier served by the endpoint. Empty disables all model requests. |
| `LLM_API_KEY` | empty | Optional Bearer token for the chosen endpoint. Local runtimes often do not require one. |

The optional `llm` Compose profile starts an Ollama container on the same default
network as web. Its API is not published on a host port. With the profile off,
the hostname is unused as long as `LLM_MODEL` is empty. Images, Python packages
and model weights require downloads before an offline deployment can run.

The default web binding is `127.0.0.1:8000`. For a shared deployment put an HTTPS
reverse proxy in front and deliberately choose its network/binding. A trusted
operator controls the database and model endpoint; schemasmith is a single
application installation rather than a multi-tenant service.

Developer-only process variables are `RUN_POSTGRES_TESTS=1` (disposable database
checks), `RUN_E2E=1` (Chromium smoke), and `DATABASE_URL` for the test database.
These are not production configuration and are intentionally absent from `.env.example`.
Designer limits are fixed in code: three total requests per generation, 120 seconds
per provider response, 256 KiB response cap, and ten designer operations per admin
per minute in each web process. Multiple workers multiply that final allowance.
