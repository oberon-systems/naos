# Prompt 10d — Run Detail

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`, and the board
`Run detail — Overview` on page `Runs` in `dev/web/templates/base`.

Build only what the board shows. When a screen named here has no board,
report the missing board and build nothing for it.

Build the run detail overlay over `GET /runs/{run_id}`: the header with
state, spec, runner, elapsed time and attempt; the actions `Stop run` and
`Rerun`; the tab strip Overview / Terminal / Logs & Audit; and the Overview
body — the Run card, the Runner card, the lifecycle timeline, and the Policy
card with merge policy, approvals, timeouts, network egress, secrets,
artifacts and lease fencing.

`Stop run` posts to `/runs/{run_id}/stop` behind a confirmation. Secrets show
as bound names and counts only. The Policy card reads the Run spec, never a
profile that may have changed since.

The UI is never an authorization boundary. Every action goes through API
authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: an operator can inspect and stop a Run from the overlay, and the
Policy card shows the immutable spec of that Run.
