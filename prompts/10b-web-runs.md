# Prompt 10b — Runs List

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`, and the board
`Start Page — Dashboard` on page `Runs` in `dev/web/templates/base`.

Build only what the board shows. When a screen named here has no board,
report the missing board and build nothing for it.

Build the Runs list over `GET /runs`: the tiles Active runs, Queued, Waiting
merge and Failed · 24h; the filters All / Active / Queued / Waiting merge /
Failed; the table RUN, STATUS, SPEC, RUNNER, STARTED, DURATION with the row
action the board gives each state (Open, Review, Logs, Diff, Cancel); the
Runners side card; and the lifecycle footer.

Row secondary lines — lease left, heartbeat age, failure reason, merge
summary — come from the API, never from a guess in the template.

The Runners side card needs an operator-facing runner listing; `packages/api`
exposes only the runner-facing endpoints today. Name that prerequisite and
stop rather than widening runner authentication.

The UI is never an authorization boundary. Every action goes through API
authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: the list renders every state the board shows, each filter narrows
it server-side, and no row action bypasses API authorization.
