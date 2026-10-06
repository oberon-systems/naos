# Prompt 06i - Live Network Policy

Read `AGENTS.md`, `docs/03-api-design.md`, `docs/04-runner-design.md`,
`docs/06-network-gate.md` and `06f-mcp-live-attach.md`.

An operator edits the `network` policy of a Run that is already running,
without a restart. The API of 06f already takes the change; this prompt makes
the runner apply it.

- `POST /api/v1/runs/{run_id}/policies` accepts `kind` `network` on a STARTED
  Run, with a stored policy or an edited document, instead of 409.
- The desired state carries the new resolved document; the runner applies it
  on its next reconcile pass.
- The gate swaps the policy between requests, never inside one.
- A rule that stays as it was keeps the request budget it spent.

The runner writes `network_policy_configured` when the gate holds the new
policy.

Update `docs/03-api-design.md` and `docs/06-network-gate.md`.

Acceptance: a host added to a running Run answers its next request and a removed one is denied from the next request on; a call in flight finishes under the policy it
started with; `make smoke` changes the `network` policy of a STARTED Run.
