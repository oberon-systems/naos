# Prompt 10i — Profiles

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/01-security-model.md`, and the
board `Profiles — List` on page `Profiles` in `dev/web/templates/base`.

Build only what the board shows. When a screen named here has no board,
report the missing board and build nothing for it.

Build the Profiles list: the tiles Profiles / Policies / Secrets / Runs 24h,
the search and the filters All / Used / Unused, the table PROFILE, RUNTIME,
POLICIES, MERGE, TIMEOUT, RUNS with the `Edit` row action, and the detail
pane with runtime, merge policy, timeout, the resolved policy documents and
the runs launched from the profile.

A profile is a starting point a Run copies: say so where the board says so,
and never present an edit as reaching a started Run. Secrets appear as names
only. The profiles resource comes from `10c-web-new-run.md`; build on it
and add no second one.

The UI is never an authorization boundary. Every action goes through API
authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: an operator can read a profile and its resolved policies, and no
view renders a secret value or implies an edit changes a started Run.
