# Prompt 10f — Run Logs and Audit

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/11-observability.md`, and the
board `Run detail — Logs & Audit` on page `Runs` in `dev/web/templates/base`.

Build only what the board shows. When a screen named here has no board,
report the missing board and build nothing for it.

Build the Logs & Audit tab over `GET /runs/{run_id}/events`: the filters All
/ Audit / Logs / Errors, the entry count, `Export`, and the table TIME, KIND,
EVENT, ACTOR, DETAIL with the audit and log kinds the board distinguishes.

Render the detail column from the typed event payload. An unknown event name
or an unexpected field is shown as refused, not rendered as free text.

The UI is never an authorization boundary. Every action goes through API
authorization. Never display secrets.

Acceptance: a Run can be reconstructed from the tab alone, and no entry
renders a credential or a value the audit never carried.
