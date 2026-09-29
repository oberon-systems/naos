# Prompt 06f — Live Attach

Read `AGENTS.md`, `docs/03-api-design.md`, `docs/04-runner-design.md`,
`docs/08-mcp-gate.md`, `06c-mcp-registry.md` and `06d-mcp-rules.md`.

An operator adds an MCP server to a Run that is already running, or takes one
away, without a restart.

- `POST /api/v1/runs/{run_id}/mcp` takes the id of an `mcp` policy and makes
  it the Run's policy from then on; only a STARTED Run accepts it, anything
  else is 409. The Run keeps the history of every policy it held.
- The desired state carries the new resolved policy and its credentials; the
  runner applies it on its next reconcile pass.
- The broker swaps the policy between calls, never inside one, and sends
  `notifications/tools/list_changed` and, with resources,
  `notifications/resources/list_changed`, so the agent lists again.
- A server taken away closes its session, drops its credentials from the
  gate and denies from the next call on.

Every change writes `mcp_policy_changed` with actor `operator`, the Run and
the old and new policy ids, and the runner writes `mcp_policy_configured`
when the broker holds it.

Update `docs/03-api-design.md` and `docs/08-mcp-gate.md`.

Acceptance: a server added to a running Run answers its next call, a removed
one is denied from the next call and its credential is gone from the runner;
a call in flight finishes under the policy it started with; `make smoke` adds
and removes a server on a STARTED Run.
