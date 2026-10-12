# FreightFlow — Freight Operations Reference Platform

A runnable, self-contained freight shipment lifecycle application. Built as a **portfolio demonstration**, not a railroad production system or a representation of any employer's internal software.

## Features

- Shipment creation, listing, and lookup through JSON REST endpoints
- State machine: `CREATED → DISPATCHED → IN_TRANSIT → DELIVERED`; cancellation from created/dispatched
- Persistent SQLite database with shipment and append-only event history
- Atomic shipment updates and event writes in the same database transaction
- Idempotency key support for safe create retries
- Optimistic concurrency with shipment version numbers
- Browser operations dashboard for creation and progression of shipments
- Automated end-to-end HTTP tests covering normal and failure flows
- No external dependencies; Python 3.10+ standard library only

## Quick start

```bash
python3 -m freightflow.app
```

Open http://127.0.0.1:8080. The SQLite file `freightflow.db` is created on first run. Override with `FREIGHTFLOW_DB=/path/to/data.db` and `PORT=8085`.

## Run automated tests

```bash
python3 -m unittest discover -s tests -v
```

Tests boot a real HTTP server on an ephemeral localhost port, send HTTP requests, and validate database persistence and event history. They do not require internet access.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/api/shipments` | List shipments |
| POST | `/api/shipments` | Create shipment; optional `Idempotency-Key` header |
| GET | `/api/shipments/{id}` | Retrieve shipment |
| POST | `/api/shipments/{id}/status` | Update status with `expected_version` |
| GET | `/api/shipments/{id}/events` | Read event history |

### Create example

```bash
curl -s http://127.0.0.1:8080/api/shipments \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-shipment-001' \
  -d '{"customer":"Acme Logistics","origin":"Fort Worth, TX","destination":"Denver, CO","pieces":12}'
```

Use the returned `id` to progress the shipment:

```bash
curl -s -X POST http://127.0.0.1:8080/api/shipments/REPLACE_ID/status \
  -H 'Content-Type: application/json' \
  -d '{"status":"DISPATCHED","expected_version":0}'
```

## Architecture and trade-offs

See [docs/architecture.md](docs/architecture.md). This is deliberately a **modular monolith**, not a collection of cosmetic microservices. It is easy to run and inspect, and keeps transactional consistency clear. A real deployment would require identity management, PostgreSQL, asynchronous event delivery, tracing, migrations, rate limiting, and security review.

## Authorship and usage

This starter was produced with AI assistance. Review, test, extend, and document your own engineering decisions before presenting it as evidence of personal implementation work. Do not claim it was independently built without assistance or used in production.
