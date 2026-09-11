# Prompt 01 — Run Domain

Read `AGENTS.md`, `docs/00-architecture.md`, and `docs/03-api-design.md`.

Implement:

- Run SQLModel;
- RunSpec models;
- lifecycle state machine;
- policy snapshot references;
- create/get/list/stop API;
- persistence and transaction-safe transitions.

Clients must not set status arbitrarily. Security policy becomes immutable once the Run starts. Mutations should be idempotent.

Acceptance: valid transitions succeed, invalid transitions fail, duplicates are safe, policy cannot be mutated during an active Run, and positive/negative tests pass.
