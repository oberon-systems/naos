# Prompt 10 — HTMX UI

Read `AGENTS.md` and `docs/10-web-ui.md`.

Build Runs, Run details, console, logs, gate events, diff, merge approval, profiles, and images.

The UI is never an authorization boundary. Every action goes through API authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: an operator can create, inspect, stop, and merge a Run without bypassing backend authorization.
