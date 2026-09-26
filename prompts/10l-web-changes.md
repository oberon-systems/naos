# Prompt 10l — Changes

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`,
`docs/09-overlay-and-merge.md`, and the board `Run detail — Changes` on page
`Runs` in `dev/web/templates/base`.

Build only what the board shows. When a screen named here has no board,
report the missing board and build nothing for it.

Build the `Changes` tab of the run detail overlay over
`GET /runs/{run_id}/merge`, the filesystem diff viewer. The tab appears once
the Run's `merge` is not null and carries the count of changes; the header,
the other tabs and their actions stay as `10d-web-run-detail.md` built them.
The `Review` and `Diff` row actions of `Start Page — Dashboard` open the
overlay on this tab.

The body is the state banner, the list of entries and the right column:

- the list keeps the API order, sorted by path bytes, with the filters All /
  Created / Modified / Deleted / Renamed / Rejected / Sensitive, the change
  pill, the path, `from` for a rename, the target for a symlink, `dir` for a
  directory and the size for a file;
- a rejected entry is greyed with its `reason` and cannot be picked; a
  sensitive one carries the `sensitive` pill;
- the Selected entry card shows only fields of the entry: change, kind,
  size, `mode` with `base_mode`, `sha256` with `base_sha256`, `target` with
  `base_target`;
- the Sensitive card and the Rejected card list those entries.

The diff holds metadata, never content: file contents stay on the upper disk
of the runner. Render no line diff and add no endpoint that reads contents.

A path comes from an untrusted guest. Render it as escaped text, never as a
link or markup, and keep every byte the API returns.

The UI is never an authorization boundary. Every action goes through API
authorization. Never display secrets.

Acceptance: an operator reads every collected change of a Run, tells created,
modified, deleted, renamed and rejected apart and sees which paths are
sensitive; a hostile path renders as inert text.
