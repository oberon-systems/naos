# 11 — Observability

- [Prompt](#prompt)
- [Audit trail](#audit-trail)
- [API events](#api-events)
- [Runner events](#runner-events)
- [Runner spool](#runner-spool)
- [Reading the trail](#reading-the-trail)
- [Future work](#future-work)

## Prompt

Implement security-oriented audit events.

Record:

- Run lifecycle;
- VM lifecycle;
- gate decisions;
- mounts;
- merge actions;
- credentials;
- runner health.

Correlate with run_id, vm_id, runner_id, and gate_id where applicable.

Never log tokens, passwords, API keys, private credentials, or sensitive raw payloads.

Eventually expose metrics for active Runs, startup time, duration, gate requests/denials, overlay size, resource use, runner health, and queue latency.

## Audit trail

Every audit event is one row of the `audit_events` table in the API. A row
holds `seq`, the order of arrival, an event `id`, the time `at`, its
`source` (`api` or `runner`), the `event` name, the `actor` (`operator`,
`runner` or `system`), the `run_id`, `vm_id` and `runner_id` it concerns, and
`data`.

Both sides store only what a fixed schema allows. The API checks each event
name and its `data` keys against an allowlist, and a runner event with an
unknown name, an extra field or a wrongly typed one is refused whole. No
field takes free text from a request, so tokens, secret and credential
values, and payloads of gate calls never reach the trail.

## API events

The API writes its events in the same transaction as the change they
describe, so a change and its event land or fail together.

| Event | Actor | Data |
|---|---|---|
| `run_created` | operator | none |
| `run_transition` | operator, runner, system | `from`, `to`, `reason` |
| `run_stop_requested` | operator | `status` |
| `runner_registered` | runner | none |
| `lease_acquired` | runner | `lease_id` |
| `lease_expired` | system | `lease_id` |
| `token_rotated` | runner | none |
| `waiting_rebound` | system | `lease_id` |
| `credentials_issued` | runner | `names` of the secrets, never the values |
| `diff_reported` | runner | `entries`, `rejected`, `sensitive` counts, merge `policy`, `decided` |
| `merge_decided` | operator | `paths` and `resolutions` counts |
| `merge_reported` | runner | `outcome`, `conflicts` and the `applied`, `skipped`, `exported`, `backed_up` counts |
| `policy_created` | operator | `policy_id`, `kind` |
| `image_registered` | operator | `image_id`, `version`, `digest` |
| `secret_created` | operator | `name` only |

A lease that expires fails its active Runs, and each of those is a
`run_transition` by `system` with the reason `runner lease expired`.

## Runner events

The runner writes each event as a JSON line of its log under the `audit`
target, and appends it to its spool. The events are those of the runtime
([04](04-runner-design.md#runtime)), the console and the gates
([06](06-network-gate.md), [07](07-shell-gate.md), [08](08-mcp-gate.md)):
`run_claimed`, `run_failed`, `orphan_destroyed`, `lease_fenced`,
`runner_registered`, `runner_credentials_dropped`, `console_attached`, and
every gate decision, `network_allowed`, `network_denied`, `shell_allowed`,
`shell_denied` and `mcp_call`.

`POST /api/v1/runners/{runner_id}/events` takes a batch of up to 1000 of
them. An event that names a Run this runner never held is refused and
listed in `refused`; an event whose `id` is already stored is skipped, so a
repeated batch is harmless.

```json
{"accepted": 998, "refused": ["evt_0123456789abcdef0123456789abcdef"]}
```

## Runner spool

The spool is `audit.jsonl` in `NAOS_AGENT_STATE_DIR`, mode 0600, one event
per line. After each reconcile cycle the agent posts up to ten batches and
drops what the API took; a failed post keeps them for the next cycle, and a
batch the API rejects as malformed is dropped with an error in the log.

The spool is capped at 64 MiB. When it is full, the oldest events go, and an
`audit_dropped` event with their count takes their place. `naos-agent
console` appends to the same spool when the full agent settings are
available, with `audit.lock` next to it serialising both processes.

## Reading the trail

Both endpoints need the operator token.

- `GET /api/v1/runs/{run_id}/events` is the timeline of one Run, ordered by
  `at` and then `seq`, up to `limit` rows (1000 by default, at most 10000).
  An unknown Run gets 404.
- `GET /api/v1/audit` lists every event in `seq` order, filtered by
  `runner_id`, `event` and `since` (an `at` in seconds), `limit` rows at a
  time (100 by default, at most 1000). Pass the last `seq` as `after` for the
  next page.

```bash
curl -fsS -H "Authorization: Bearer $operator" "$api/api/v1/runs/$run/events"
curl -fsS -H "Authorization: Bearer $operator" "$api/api/v1/audit?event=network_denied&limit=100"
```

Runner events are posted after the cycle that wrote them, so the timeline
of a Run catches up a few seconds behind the runner's log.

## Future work

- Ship runner audit events through [Vector](https://vector.dev) as an
  alternative to pushing them to the API, for hosts that already run a log
  pipeline. The API push stays the default.
