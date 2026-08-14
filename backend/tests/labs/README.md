# Deterministic local BOLA lab

The regression lab runs an ephemeral HTTP server bound to `127.0.0.1` and exposes
`GET /orders/{order_id}` with Alice/order 1001 and Bob/order 2001. The test runs
the same scenario in secure and deliberately vulnerable modes.

From `backend`, run:

```bash
.venv/bin/pytest tests/integration/test_bola_lab.py
```

The lab is test-only, needs no third-party service, and is always stopped by its
context manager. PostgreSQL must be available using the existing `DATABASE_URL`.
