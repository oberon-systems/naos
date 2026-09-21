# Prompt 10 — HTMX UI

Read `AGENTS.md` and `docs/10-web-ui.md`.

The UI is built one mocked-up screen at a time. The mockups are the Penpot
export in `dev/web/templates/base`, readable as JSON in the repo; a board is
addressed by its page and board name, and `make -C dev/web up` is needed only
to draw a new one.

A screen is built only when a board exists for it. When a prompt names a
screen the export has no board for, report the missing board and build
nothing for it.

Order:

1. `10a-web-shell.md` — shell, nav, summary tiles, shared components.
2. `10b-web-runs.md` — Runs list (`Start Page — Dashboard`).
3. `10c-web-new-run.md` — New Run dialog: profile, configure, save & run.
4. `10d-web-run-detail.md` — run detail overlay, Overview tab.
5. `10e-web-terminal.md` — Terminal tab, detached window, host hint.
6. `10f-web-run-logs.md` — Logs & Audit tab.
7. `10g-web-runners.md` — Runners list and runner detail.
8. `10h-web-images.md` — Images catalog.
9. `10i-web-profiles.md` — Profiles and resolved policies.
10. `10j-web-audit.md` — Audit trail.

Not covered, because no board exists yet: the filesystem diff viewer, merge
approval, per-gate event screens, and any secrets UI. Draw the board first.

The UI is never an authorization boundary. Every action goes through API
authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: every shipped screen matches a board, and the screens without one
are named rather than invented.
