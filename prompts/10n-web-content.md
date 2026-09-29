# Prompt 10n — Content

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`,
`docs/04-runner-design.md`, `docs/09-overlay-and-merge.md`,
`10l-web-changes.md`, `10m-web-merge.md`, `10e-web-terminal.md` and the boards
on page `Runs` in `dev/web/templates/base`.

No board shows content yet. Draw `Run detail — Changes · content` first, next
to `Run detail — Changes`, and build nothing until it is approved.

An operator cannot decide a merge from size, mode and sha256 alone. This
prompt lifts the rule of `10l-web-changes.md` that no endpoint reads
contents: the Changes tab shows the content of the selected entry, the
agent's version from the upper disk next to the host's version, as a stream
the operator scrolls through. No size cap, no truncation: the viewer reads
what it scrolls to.

1. Runner — it reads the agent's version with its own read-only ext4 reader
   of `docs/09-overlay-and-merge.md`, never by mounting the upper disk, and
   the host's version from the workspace, read-only and confined to it. It
   answers a range `{side, offset, length}` of one entry with the bytes at
   that offset and the side's total size. This works while the Run waits for
   a merge and after it, from `archive/<vm_id>/`.
2. API — `WS /runs/{run_id}/merge/entries/{index}/content` for the operator,
   brokered to the runner the way `WS /runs/{run_id}/attach` is: operator
   token, the runner's own socket under its lease, never a port on the runner.
   A text frame asks a range, a binary frame answers it with its offset. Every
   open writes a `merge_content_viewed` audit event with the path and never
   the bytes.
3. Web — the pane in the Changes tab: the host and agent sides scrolled
   together, lines that differ marked, a jump to the next difference, bytes
   that are not text shown as hex. Sensitive paths are shown like any other
   entry: they are the ones to read before merging. A rejected entry and a
   directory have no content.

Contents come from an untrusted guest. Render them as inert text: no markup,
no links, no terminal escapes interpreted, every byte kept.

The UI is never an authorization boundary. Every action goes through API
authorization. Never display secrets.

Acceptance: an operator reads the full content of any modified, created or
renamed file on both sides before deciding, however large it is; every view
is audited; `make smoke` reads the content of a merged path through the web.
