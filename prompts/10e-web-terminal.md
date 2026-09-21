# Prompt 10e — Run Terminal

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/01-security-model.md`, and the
boards `Run detail — Terminal`, `Terminal — Detached window` and `Terminal
hint — run on the host` on page `Runs` in `dev/web/templates/base`.

Build only what the boards show. When a screen named here has no board,
report the missing board and build nothing for it.

Build the Terminal tab: the toolbar Follow / Wrap / Timestamps with the
stream selector, the attach endpoint label and `Download log`; the output
pane with the status bar underneath; the detached-window variant with `Back
to the run`; and the hint overlay listing the cli, on-host and raw-websocket
ways to reach the same session.

The pane is read-only and attaches through the API, never to the runner
directly. `packages/api` has no attach endpoint today — name that
prerequisite and stop rather than opening a port on the runner.

The UI is never an authorization boundary. Every action goes through API
authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: the terminal is a read-only view brokered by the API, every
attach leaves an audit entry, and the rendered command hints carry no token.
