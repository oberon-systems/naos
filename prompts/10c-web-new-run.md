# Prompt 10c — New Run

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`, and the boards
`New Run — 1 Profile`, `New Run — 2 Configure` and `New Run — 3 Save & run` on
page `Runs` in `dev/web/templates/base`.

Build only what the boards show. When a screen named here has no board,
report the missing board and build nothing for it.

Build the New Run dialog behind the `New Run` button at `/runs/new`: an
overlay under htmx, the same panel inside the shell without it. Every step
fits the window and scrolls inside:

1. Profile — the search, the profile list with runtime and active runs, and
   the pinned `Create new profile` row.
2. Configure — the fields copied from the picked profile, edited ones marked
   with the profile value; image and runner, `auto` by default, belong to the
   Run, not the profile.
3. Save & run — the changes against the profile, `Save as new` under a unique
   name or `Update <profile>`, then the Run created from the saved profile. An
   unedited profile is not saved again.

`packages/api` lacks what the dialog needs; add it first:

- a profiles resource: list with search, create and update; a name is unique,
  and an update is refused while a Run copied from that profile is active;
- the profile a Run was copied from, recorded on the Run, so active runs per
  profile can be counted;
- an operator listing of policies, `GET /policies`;
- an optional runner on the Run spec; unset lets any runner claim the Run.

A Run copies the profile and never references it, so an update reaches no
started Run. Each dialog sends one `Idempotency-Key`, so a double submit
creates one Run.

The UI is never an authorization boundary. Every action goes through API
authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: an operator picks or creates a profile, edits it, saves it and
gets a PENDING Run with that spec; the API, not only the form, refuses to
update a profile with an active Run; `make smoke` creates a Run this way.
