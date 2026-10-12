# Architecture decision record

## Context
A small freight operations system must create shipments, advance state safely, and preserve an auditable history of every transition. It must be easy for reviewers to run locally without cloud credentials.

## Decision: modular monolith with SQLite
An HTTP layer delegates to a shipment domain store. SQLite provides ACID transactions, and the event log is committed in the same transaction as the shipment state change. This avoids the dual-write failure that would occur if a database update and a separate event broker publish were attempted without an outbox.

## Reliability mechanisms
- **Idempotency:** A client-supplied key maps to a canonical request hash and shipment ID. Reuse with different content is rejected.
- **State machine:** Only approved transitions are accepted. Terminal states cannot be reopened.
- **Optimistic locking:** A caller can supply `expected_version` to prevent stale updates.
- **Atomic audit trail:** Each state update and its corresponding event are persisted together.
- **Input validation:** Required fields, piece counts, and origin/destination constraints are checked.

## Limitations and next steps
This is a demonstration, not production-grade infrastructure. The event table is an internal audit log, **not Kafka**. No distributed messaging, identity provider, access control, or observability backend is included. A production evolution would move to PostgreSQL, introduce an outbox dispatcher to Kafka, implement consumer deduplication, add OAuth/OIDC and role permissions, use OpenTelemetry, and load-test operational SLOs. Do not claim these capabilities exist in this version.

## Interview discussion topics
- Why a single transaction is safer than dual writes
- What guarantees idempotency provides, and what it does not
- How to handle concurrent updates and stale clients
- How to migrate from the modular monolith to separately deployed services
- How to introduce retries, dead-letter handling, and backpressure when Kafka is added
