# Contributing

Keep the engine small: FastAPI, Postgres and bundled vanilla JavaScript. A frontend
build or CDN dependency changes the offline deployment model and needs discussion
before implementation. Specs and model output remain untrusted data.

Use Python 3.12 and a virtual environment. Install development dependencies:

```sh
python -m pip install -r requirements-dev.txt
```

Pure/stub tests run without a model. Database tests use only a disposable Postgres
16 database and explicit opt-in; set `DATABASE_URL` to that database yourself.

```sh
python -m pytest -q --ignore=tests/e2e
RUN_POSTGRES_TESTS=1 python -m pytest -q --ignore=tests/e2e
python -m playwright install chromium
RUN_POSTGRES_TESTS=1 RUN_E2E=1 python -m pytest -q tests/e2e --browser chromium
for file in static/*.js; do node --check "$file"; done
```

The environment-prefix examples assume a POSIX shell. Chromium needs its system
dependencies installed; CI uses `python -m playwright install --with-deps chromium`.
Node is test-only. Stub LLM tests use a loopback server and synthetic fixtures.
Do not replace them with paid/cloud model calls.

Include the behavior changed, a reproducible synthetic example, relevant checks
and unverified areas in a pull request. Add meaningful tests for permission,
migration or parser changes. Keep SQL identifiers in `psycopg.sql.Identifier` and
values in parameter arguments. Escape every spec label and user value rendered
in the browser. Model output must pass `app.spec.validate` before use.

When changing the spec models, regenerate and commit `spec/schema.json`, update
the reference/examples, and run schema-drift and invalid-spec tests. Never include
credentials, real personal/client data, `.env`, private process documents or
database dumps. Maintain `LICENSE` notices. CI runs the same commands plus gitleaks.
