# Prompt 10j — Audit

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/11-observability.md`, and the
board `Audit — List` on page `Audit` in `dev/web/templates/base`.

Build only what the board shows. When a screen named here has no board,
report the missing board and build nothing for it.

Build the Audit trail over `GET /audit`: the tiles Events 24h / Gate denials
/ Refused / Spool lag, the live tail with `Export`, the search and the
filters All / Lifecycle / Gates / Merge / Runner, the table TIME, EVENT,
ACTOR, SOURCE, RUN, RUNNER, DATA, and the detail pane with when, correlation,
the typed data block and the links onward to the run timeline and the policy.

The trail is append-only and the data block is typed: render the known fields
of an event and mark a refused one as refused. No field is free text.

The UI is never an authorization boundary. Every action goes through API
authorization. Never display secrets.

Acceptance: an operator can follow a gate denial to the Run and the policy
that refused it, and no entry renders a token or a gate payload.
