# Prompt 10f — Runners

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/04-runner-design.md`, and the
boards `Runners — List` and `Runner detail — alpha` on page `Runners` in
`dev/web/templates/base`.

Build only what the boards show. When a screen named here has no board,
report the missing board and build nothing for it.

Build the Runners list: the tiles Live / Stale / Revoked / Slots busy, the
search and the filters All / Live / Stale / Revoked, the table RUNNER, STATE,
SLOTS, LEASE, HEARTBEAT, TOKEN with the `Revoke` row action, and the side
pane with lease, capacity, heartbeat, token, current runs and recent events.
Build the runner detail overlay with the same facts plus placement, platform,
labels and the `Drain` and `Revoke` actions.

Tokens are never rendered — only their rotation windows. `packages/api` has
no operator-facing runner listing, revoke or drain endpoint today; name those
prerequisites and stop rather than reusing the runner's own credentials.

The UI is never an authorization boundary. Every action goes through API
authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: an operator can see fleet state and revoke a runner, and no view
ever renders a token value.
