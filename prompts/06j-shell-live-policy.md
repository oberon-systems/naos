# Prompt 06j - Live Shell Policy

Read `AGENTS.md`, `docs/03-api-design.md`, `docs/04-runner-design.md`,
`docs/07-shell-gate.md` and `06f-mcp-live-attach.md`.

An operator edits the `shell` policy of a Run that is already running,
without a restart. The API of 06f already takes the change; this prompt makes
the runner apply it.

- `POST /api/v1/runs/{run_id}/policies` accepts `kind` `shell` on a STARTED
  Run, with a stored policy or an edited document, instead of 409.
- The desired state carries the new resolved document; the runner applies it
  on its next reconcile pass.
- The gate swaps the capabilities between calls, never inside one, and the broker sends
  `notifications/tools/list_changed`.
- The mounts stay as they are: the mount policy never changes.

The runner writes `shell_policy_configured` when the gate holds the new
policy.

Update `docs/03-api-design.md` and `docs/07-shell-gate.md`.

Acceptance: a capability added to a running Run answers its next call and a removed one is denied from the next call on; a call in flight finishes under the policy it
started with; `make smoke` changes the `shell` policy of a STARTED Run.
