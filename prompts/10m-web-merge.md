# Prompt 10m — Merge

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`,
`docs/09-overlay-and-merge.md`, `10l-web-changes.md`, and the boards on page
`Runs` in `dev/web/templates/base`: `Run detail — Changes`,
`Run detail — Changes · conflicts`, `Run detail — Changes · decision sent`,
`Run detail — Changes · merged`, `Confirm — Merge paths`,
`Confirm — Merge nothing` and `Confirm — Merge with sensitive paths`.

Build only what the boards show. When a screen named here has no board,
report the missing board and build nothing for it.

Build the merge workflow on the `Changes` tab from `10l-web-changes.md`; add
no second view of the diff. The state comes from the merge response alone:

1. Waiting — `decision` and `report` are null. Every live entry that is not
   sensitive starts selected; the checkboxes, `Select all`, `Clear`, the
   `On merge` switch Apply / Skip / Export of the selected entry and the
   decision bar with `Merge nothing` and `Merge <n> paths` are live.
2. Decision sent — `decision` is set and `report` is null. The selection is
   locked and the tab polls the merge until it changes.
3. Conflicts — `conflicts` is not empty. The red banner, every conflicting
   row with its reason and Skip / Take / Export, and `Send decision again`,
   enabled once each conflict has a resolution.
4. Merged — `report` is set. Every entry shows its outcome from `applied`,
   `skipped`, `exported` and `backed_up`, with the Report and Where cards.

`Merge <n> paths` asks `Confirm — Merge paths`, or `Confirm — Merge with
sensitive paths` when the selection holds one, then posts `paths` and
`resolutions` to `POST /runs/{run_id}/merge`. `Merge nothing` asks
`Confirm — Merge nothing`, then posts `POST /runs/{run_id}/merge/reject`.

The API decides what a selection may be. Keep the checkboxes from building a
selection it refuses, and still show its 422 message as it comes back; a 409
reloads the tab in its current state. A sensitive path is never selected for
the operator.

The UI is never an authorization boundary. Every action goes through API
authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: an operator merges a selection, merges nothing, resolves a
conflict with each of skip, take and export and reads the report; no path
reaches the host without a confirmed decision, and `make smoke` merges a Run
this way.
