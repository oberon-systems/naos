# Issue 02 - A stalled terminal grows its queue without bound

Read `AGENTS.md`, the Console section of `docs/03-api-design.md` and
`packages/api/src/naos_api/console_hub.py` before changing anything.

## Problem

Every open terminal of a run gets its own `Output` queue in `ConsoleHub.viewer`,
an `asyncio.Queue` with no `maxsize`. `ConsoleHub.publish` puts every raw frame
the runner sends into every such queue, and the `attach` loop in
`packages/api/src/naos_api/routes/console.py` drains it by sending the frame to
the viewer's socket. When that socket stops taking bytes - a frozen tab, a
suspended laptop, a network stall - `send_bytes` blocks, the loop stops
draining, and the queue keeps every frame that arrives.

## Blast radius

The runner reads the guest's log every 50 ms in chunks of up to 64 KiB, and a
tmux session redraws its screen even when nothing happens, so one stalled
viewer can hold on the order of a megabyte a second in the API's memory. It
lasts until the socket finally errors out, which on a half-open TCP connection
can take minutes. Several stalled viewers, or one on a noisy run, can exhaust
the API process and take every other run's console down with it.

## Fix

Bound the per-viewer queue and give a viewer that falls behind a way back:

- Cap `Output` by bytes held rather than by frame count, since frames range from
  one byte to 64 KiB; a few MiB per viewer is plenty for a burst.
- `publish` runs the put through `call_soon_threadsafe`, so a full queue must
  not raise inside the loop: wrap the put in a function that, on overflow,
  empties the queue and marks the viewer as behind.
- The `attach` loop, on seeing that mark, drops what it holds and reads the
  store from its own offset - the path it already takes on a timeout - then
  rejoins the live feed. Overlaps are already cut by offset.
- What the store returns is normalised, so a viewer that resyncs misses the
  redraws of that stretch; the next full tmux redraw, or the viewer's next
  claim, restores the screen. Accept that rather than keeping raw bytes around.

Leave the other queues as they are unless the same failure applies: the runner
`Outbox` and the web relay's viewer outbox only carry keys and sizes.

## Acceptance

- A viewer that never reads while the runner pushes several MiB keeps the
  API's memory for that viewer under the cap.
- Once it reads again it receives the rest of the log from the store, in order
  and without duplicates, then the live frames after it.
- A viewer that keeps up sees every raw byte exactly as today.
- The existing console tests and smoke pass unchanged.
