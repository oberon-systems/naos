# Prompt 02 — Runner Lifecycle

Read `AGENTS.md`, `docs/00-architecture.md`, and `docs/04-runner-design.md`.

Implement Rust runner registration, authentication, lease renewal, heartbeat, assigned-Run retrieval, reconciliation, and orphan detection.

Tolerate API/queue/runner restarts and duplicate messages. Persistent API state is authoritative.

Acceptance: expired leases are detectable, restart reconciliation works, duplicate work cannot create duplicate runtime state, and orphan resources are identified.
