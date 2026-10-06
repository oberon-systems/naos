# Prompt 06k - Live Model Policy

Read `AGENTS.md`, `docs/03-api-design.md`, `docs/04-runner-design.md`,
`docs/13-model-gateway.md` and `06f-mcp-live-attach.md`.

An operator edits the `model` policy of a Run that is already running,
without a restart. The API of 06f already takes the change; this prompt makes
the runner apply it.

- `POST /api/v1/runs/{run_id}/policies` accepts `kind` `model` on a STARTED
  Run, with a stored policy or an edited document, instead of 409.
- The desired state carries the new resolved document; the runner applies it
  on its next reconcile pass.
- The gateway swaps the policy between requests; a stream in flight ends under the policy it started with.
- A model that stays as it was keeps the token budget it spent, and the key of a removed provider leaves the runner.

The runner writes `model_policy_configured` when the gate holds the new
policy.

Update `docs/03-api-design.md` and `docs/13-model-gateway.md`.

Acceptance: a model added to a running Run answers its next request and a removed one is refused from the next request on; a call in flight finishes under the policy it
started with; `make smoke` changes the `model` policy of a STARTED Run.
