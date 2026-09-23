# Issue 01 - The console hub lives in one API process

Read `AGENTS.md`, the Console section of `docs/03-api-design.md` and
`packages/api/src/naos_api/console_hub.py` before changing anything.

## Problem

`ConsoleHub` is an in-memory object: `create_app` in
`packages/api/src/naos_api/app.py` builds one per process and keeps it on
`app.state.console_hub`. It carries both directions of the live console - the
raw output a runner sends to the terminals of a run, and the keys and the grid
size the driving viewer sends back to the runner. That only works while the
runner's socket (`WS /runners/{runner_id}/runs/{run_id}/console`) and every
viewer's socket (`WS /runs/{run_id}/attach`) land in the same process.

## What breaks

Run the API with more than one uvicorn worker, or with several replicas behind
a load balancer, and the sockets of one run spread across processes. A viewer
on another process than the runner loses its keyboard silently: `_typed` in
`routes/console.py` finds no runner socket in its own hub and drops the keys,
and its grid size never reaches the guest either. The same viewer loses the raw
output too and falls back to reading the store every `POLL_SECONDS`, which is
normalised - redraw-only chunks and lone echoed spaces are missing, so the
screen breaks the way it did before the hub existed. The claim itself is
unaffected, since `ConsoleSize` lives in the database.

## Constraints

- Keys must never be stored or audited. A database table used as a key queue is
  not acceptable; a transient bus is.
- The terminal gets every raw byte; only the store and the download are
  normalised. Do not route live output through the store to reach other
  processes.
- One process with one worker must keep working with no new infrastructure.
- Output frames reach 64 KiB, so a bus with a small payload limit needs
  chunking.

## Fix

Keep `ConsoleHub` as the local fan-out and put a shared bus behind it. A process
that holds the runner's socket publishes each raw output frame, with its
offset, to a per-run output channel, and subscribes to a per-run input channel
for keys and size frames. A process that holds a viewer subscribes to the output
channel and publishes the driver's keys and size to the input channel. The
viewer keeps cutting overlaps by offset, so duplicates across the bus stay
harmless.

Pick the bus with the user before building it:

- Redis pub/sub - no payload limit, no persistence, but a new service to run.
- PostgreSQL `LISTEN`/`NOTIFY` - no new service where the API already runs on
  Postgres, but payloads stop at 8000 bytes, so output frames must be split,
  and SQLite deployments get nothing.
- Sticky routing - the load balancer sends every socket of a run to the same
  process by hashing `run_id`, no code in the API, but the deployment then
  depends on a proxy rule.

Make the bus optional: with none configured, the in-memory hub is the whole
story, as today.

## Acceptance

- With two API processes and the runner and the viewer on different ones, a
  key typed in the browser reaches the guest and its echo comes back raw.
- A size claimed on one process reaches the runner on the other.
- A lone echoed space and a tmux redraw reach a viewer on the other process.
- Nothing typed is written to the database or the audit.
- A single-process API passes the existing console tests and smoke unchanged.
