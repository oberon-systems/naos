# Prompt 10a — Web Shell

Read `AGENTS.md`, `docs/10-web-ui.md`, and the mockups in
`dev/web/templates/base` — the shell repeats on every list board: `Start Page
— Dashboard` (page `Runs`), `Runners — List`, `Images — List`, `Profiles —
List`, `Audit — List`.

Build only what a mockup board shows. When a screen named here has no board,
report the missing board and build nothing for it.

Build the HTMX application shell: top bar with the `naos` brand, the nav
Runs / Runners / Images / Profiles / Audit, the api endpoint chip, and the
`New Run` action; the page header with title, primary action and a one-line
subtitle; the four-tile summary row; and the shared table, card, chip, pill
and footer-note vocabulary the boards reuse.

Take the tiles, filter pills, state chips and footer notes from the boards as
components, so every later screen assembles the same parts.

The UI is never an authorization boundary. Every action goes through API
authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: every list screen renders from the same shell, nav and component
set, and no page ships markup a board does not show.
