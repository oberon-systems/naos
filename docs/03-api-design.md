# 03 — API Design

## Prompt

Implement the control-plane API as a thin HTTP layer over application/domain services.

## Stack

Python 3.12+, FastAPI, Pydantic, SQLModel. `NAOS_DATABASE_URL` names any
database SQLAlchemy supports. The schema is plain tables with no triggers,
stored procedures or foreign keys, so the same SQL runs everywhere. The Run
lifecycle is a [transitions](https://github.com/pytransitions/transitions)
state machine.

## Time and ordering

- Every timestamp, in the database and on the wire, is an integer count of
  seconds since the Unix epoch, UTC.
- Runs carry a unique, increasing `seq`. Runners receive PENDING Runs in
  `seq` order, and `GET /runs` lists them newest first.

## Settings

The API reads its settings from `NAOS_*` environment variables once per
process. List values are JSON. Every route depends only on the values it uses.

| Variable | Default | Meaning |
|---|---|---|
| `NAOS_DATABASE_URL` | required | Database URL, for example `postgresql+psycopg://naos@db.example.com/naos` |
| `NAOS_ALLOWED_MOUNT_ROOTS` | `[]` | Host directories a mount policy may name |
| `NAOS_OPERATOR_TOKEN_SHA256` | unset | SHA-256 of the operator token; unset closes every operator route |
| `NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256` | unset | SHA-256 of the enrollment token; unset closes registration |
| `NAOS_RUNNER_TOKEN_TTL_SECONDS` | `86400` | Runner token lifetime, 60 to 604800 |
| `NAOS_LEASE_TTL_SECONDS` | `60` | Lease extension per heartbeat, 5 to 3600 |
| `NAOS_LEASE_SWEEP_INTERVAL_SECONDS` | `15` | Background lease sweep period, 1 to 3600 |
| `NAOS_RUN_CREDENTIAL_TTL_SECONDS` | `300` | Lifetime of a credential issued to a runner, 60 to 3600 |
| `NAOS_CONSOLE_LIMIT_BYTES` | `8388608` | Console output kept per Run, 4096 to 1073741824 |

## Operator credentials

Every route under `/api/v1` outside `/api/v1/runners` takes the operator
token as a Bearer credential. The API holds only its SHA-256 in
`NAOS_OPERATOR_TOKEN_SHA256`; while that is unset, or the token does not
match, every such route answers 401 before its handler runs.

## Responsibilities

Own Agents, Images, Profiles, Policies, Runs, Runners, Leases, audit records, and merge requests.

## Suggested endpoints

```text
POST /api/v1/runs
GET /api/v1/runs
GET /api/v1/runs/summary
GET /api/v1/runs/{run_id}
POST /api/v1/runs/{run_id}/stop
GET /api/v1/runners
POST /api/v1/runners/register
POST /api/v1/runners/{runner_id}/heartbeat
GET /api/v1/runners/{runner_id}/runs
GET /api/v1/runs/{run_id}/console
WS /api/v1/runs/{run_id}/attach
POST /api/v1/runners/{runner_id}/runs/{run_id}/console
GET /api/v1/runs/{run_id}/events
GET /api/v1/runs/{run_id}/merge
POST /api/v1/runs/{run_id}/merge
POST /api/v1/runs/{run_id}/merge/reject
POST /api/v1/policies
GET /api/v1/policies
GET /api/v1/policies/{policy_id}
GET /api/v1/profiles
POST /api/v1/profiles
GET /api/v1/profiles/{profile_id}
PUT /api/v1/profiles/{profile_id}
POST /api/v1/profiles/{profile_id}/runs
POST /api/v1/images
GET /api/v1/images
GET /api/v1/images/{image_id}
POST /api/v1/secrets
GET /api/v1/secrets/{name}
GET /api/v1/audit
```

Do not allow clients to arbitrarily set Run status. Validate legal transitions centrally.

Use transactions for atomic transitions and design mutations to be idempotent.

## Reading runs

`GET /runs` lists newest first and answers with what an operator screen shows
beside the Run itself, resolved by the API rather than by the caller:

| Field | Meaning |
|---|---|
| `seq` | The Run number, unique and increasing |
| `workspace` | The workspace the mount policy names, or null |
| `runner` | `id` and `name` of the runner holding the lease, or null |
| `lease_id` | The lease that fences the Run, or null while it queues |
| `profile_id` | The profile the spec was copied from, or null |
| `merge` | `changed` and `conflicts` of the collected diff, or null |
| `started_at` | When the Run reached STARTING, or null while it queues |
| `finished_at` | When it reached a terminal state, or null |

`started_at` and `finished_at` bracket the Run; `created_at` is when it was
queued, so the two never have to be told apart afterwards. The whole page
costs a fixed number of queries, whatever its length.

`status` filters by one status and `state` by the set a screen offers:
`active` is STARTING, STARTED, STOPPING and COLLECTING, `queued` is PENDING,
`waiting_merge` and `failed` are themselves. CANCELLED belongs to no state and
shows up unfiltered.

`GET /runs/summary` is the fleet at a glance, in one call: `counts` per
status, `open` for the non-terminal ones, `oldest_pending_at`, `failed_24h`
and `last_failure_reason`, the reason of the newest failure in that window.

## Console

The runner ships the output of a VM's `ttyS0` as it appears
([04](04-runner-design.md#console)). The API keeps it per Run, up to
`NAOS_CONSOLE_LIMIT_BYTES`, and accepts it only from the runner whose lease
holds the Run.

`GET /runs/{run_id}/console` answers with the whole log as `text/plain`, and
with the text of it rather than the screen it drew: escape sequences and the
lines that hold nothing else are stripped, and a report that only redrew the
screen is never stored in the first place.

`WS /runs/{run_id}/attach` is the live view. It takes the operator token in the
`Authorization` header, writes a `console_attached` audit event with actor
`operator`, sends the stored log so far as binary frames, and then every byte
the runner reads, raw - redraws and lone echoed spaces included, since a
terminal needs all of them and only the store is normalised. Nothing is held
for a terminal that is not open. The socket closes once the Run has left
STOPPING and every byte was sent.

A viewer speaks back. A text frame is the grid it asks for,
`{"cols": n, "rows": n, "view": "panel" | "window"}`, and the answer carries
`driving`, which says whether this viewer holds the guest's one size. A binary
frame is what its operator typed, and it reaches the guest only from the viewer
that is driving. A detached window outranks a panel and the claim ages out after
fifteen seconds, so closing the window hands the keyboard back to the panel.
Taking it writes one `console_typing` event naming the kind of viewer; the keys
themselves are never stored or audited, though whatever the guest echoes back
stands in the log like any other output.

`WS /runners/{runner_id}/runs/{run_id}/console` is the runner's own socket,
authorized by its bearer token and refused unless its lease holds the Run. The
runner sends binary frames of eight bytes of offset followed by the console
bytes at that offset, and the API answers `{"offset": n}` with how far it now
holds. The API sends the size as `{"cols": n, "rows": n}` and the driver's keys
as binary frames, which the runner writes into the VM's console socket. The
console report of [04](04-runner-design.md#console) stays the fallback for a
runner whose socket is down; both carry offsets, so neither loses a byte.

```bash
wscat -c ws://api.example.com/api/v1/runs/run_0123456789abcdef0123456789abcdef/attach \
  -H "Authorization: Bearer $NAOS_TOKEN"
```

## Listing runners

`GET /runners` answers with every runner, newest first: its `status` (`live`,
`stale` or `revoked`), the `capacity` of its last heartbeat, the `runs` it
holds as `id`, `seq` and `status`, `last_heartbeat_at`, the `lease_acquired_at`
and `lease_expires_at` window and `revoked_at`. `capacity` outlives the lease,
so the slots of a runner that stopped answering are still known. No token
hash, current or previous, is part of the answer.

## Idempotency

- `POST /runs` requires an `Idempotency-Key` header. The same key with the same
  spec returns the existing Run with 200; with another spec it returns 409.
- `POST /runs/{run_id}/stop` returns the current Run when there is nothing to
  stop.
- `POST /policies` deduplicates by content: the same document returns
  the existing policy with 200.
- `POST /profiles/{profile_id}/runs` takes an `Idempotency-Key` like
  `POST /runs`; the key also covers the profile, so reusing it for another
  profile returns 409.
- `POST /profiles` with a taken name and the same spec returns the existing
  profile with 200, with another spec 409. `PUT /profiles/{profile_id}` with
  the stored spec is a no-op.
- Transitions are compare-and-swap on the expected status. A repeated
  transition that already happened is a no-op.
- `POST /images` with the same `id`, `version`, `digest` and `url` returns the
  existing image with 200. The same `id` or `digest` with other values
  returns 409.

## Policies

One endpoint creates every kind of policy. `kind` discriminates the body, and
`document` is validated against that kind:

```bash
curl -fsS "$api/api/v1/policies" -d '{"kind": "network", "document": {"allow": [{"protocol": "https", "host": "example.com"}]}}'
```

The API resolves the document into its canonical form before storing it, so
equivalent documents share one digest and therefore one id. Ids carry the kind:
`mntpol_`, `netpol_`, `shellpol_`, `mcppol_`.

A Run names a policy per kind under `spec.mounts`, `spec.network`, `spec.shell`
and `spec.mcp`, and the reference is immutable once the Run starts. The runner
receives the resolved document as a snapshot rather than the id. The network
document is described in [06](06-network-gate.md), the shell document in
[07](07-shell-gate.md), the MCP document in [08](08-mcp-gate.md).

`GET /policies` lists every policy newest first; `kind` narrows it to one
kind. The web form reads it to offer a policy per kind.

## Profiles

A profile is a named, reusable Run spec without `image` and `runner`: the
`runtime`, the four policy references, `merge` and `timeout`. A Run copies the
profile and never references it, so a later update reaches no started Run.

- `POST /profiles` takes `name` and `spec`; `name` matches
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and is unique. The policies it names
  must exist, otherwise it returns 422.
- `GET /profiles` lists by name; `q` matches a substring of the name or the id,
  ignoring case. Each profile carries `active_runs`, the Runs copied from it
  that are not terminal, `active_run` with `id`, `seq` and `status` of the
  oldest of them, and `last_run_at`, null when it never ran.
- `PUT /profiles/{profile_id}` takes a new `spec`. While any Run copied from
  the profile is not terminal it returns 409 naming that Run; the check and
  the write are one statement.
- `POST /profiles/{profile_id}/runs` takes `image` and an optional `runner`
  and creates a PENDING Run from the stored spec. The Run records the profile
  it was copied from.

A Run spec may name a `runner` id. The Run is then offered to that runner only;
unset lets any runner claim it. An unknown or revoked runner returns 422.

## Secrets

A secret is a provider credential an MCP policy names by `name`. The API
stores it as given, without encryption for now, and never returns its value.

- `POST /api/v1/secrets` takes `name`, `value` and an optional `expires_at`
  and answers 201 with `id`, `name`, `expires_at` and `created_at`.
- `name` matches `^[a-z0-9][a-z0-9._-]{0,63}$`, `value` is 1 to 8192 visible
  ASCII characters; anything else gets 422.
- A name that already exists gets 409, and `GET /api/v1/secrets/{name}`
  returns the same metadata or 404.

## Images

The API keeps the catalog of images a Run may boot: what to boot and where to
download it from. It stores no image bytes; the runner downloads the file
itself and trusts the digest, not the host. The images are built as described
in [packer/README.md](../packer/README.md).

- An operator registers an image with `POST /api/v1/images` and a body of
  `id`, `version`, `digest` and `url`. The API answers 201 and never contacts
  the url.
- The url must use https and must not carry credentials; any other url gets
  422.
- `id`, `version`, `digest` and `url` are immutable and image rows are never
  deleted: no endpoint or service writes them, like the Run spec.
- `POST /runs` requires a registered image with the same `id` and `digest`,
  otherwise it returns 422.

## Merge

The operator side of a WAITING_MERGE Run. All three answer with `run_id`,
`entries`, `decision`, `conflicts`, `report` and `updated_at`.

- `GET /api/v1/runs/{run_id}/merge` returns the collected diff and where
  the merge stands, or 404 before the diff arrives.
- `POST /api/v1/runs/{run_id}/merge` takes `paths` and optional
  `resolutions`, a map of a selected path to `skip`, `take` or `export`. A
  selection that does not fit the diff gets 422, and a Run that is not
  waiting or already has a pending decision gets 409.
- `POST /api/v1/runs/{run_id}/merge/reject` is a decision with no paths:
  the Run completes and the workspace stays as it is.

```bash
curl -fsS "$api/api/v1/runs/$run/merge" -d '{"paths": ["notes.txt"], "resolutions": {"notes.txt": "take"}}'
```

## Runner interface

Runners use the same API under `/api/v1/runners`. A runner token is not an
operator principal and opens nothing outside its own runner.

```text
POST /api/v1/runners/register                                enrollment token
POST /api/v1/runners/{runner_id}/heartbeat                   runner token
GET  /api/v1/runners/{runner_id}/runs                       runner token
POST /api/v1/runners/{runner_id}/runs/{run_id}/transition  runner token
POST /api/v1/runners/{runner_id}/runs/{run_id}/diff        runner token
POST /api/v1/runners/{runner_id}/runs/{run_id}/merge       runner token
POST /api/v1/runners/{runner_id}/events                      runner token
```

### Runner credentials

- Registration takes the enrollment token as a Bearer credential. The API
  holds only its SHA-256 in `NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256`; while that
  is unset, registration is closed.
- Registration returns a random runner token once. The API stores its SHA-256
  and expires it after `NAOS_RUNNER_TOKEN_TTL_SECONDS`, 86400 by default.
- Past half its lifetime a heartbeat returns a replacement. The previous
  token stays valid until its own expiry, and a heartbeat made with it gets a
  fresh replacement, so a lost response never locks the runner out.
- A runner token used on another runner's path gets 403.

### Leases

- A runner holds at most one live lease. A heartbeat extends it by
  `NAOS_LEASE_TTL_SECONDS`, 60 by default; a heartbeat after expiry opens a
  new lease with a new id.
- Expiry is detected on every runner call and by a background sweep every
  `NAOS_LEASE_SWEEP_INTERVAL_SECONDS`. PENDING Runs of the expired lease
  return to the pool; STARTING, STARTED, STOPPING and COLLECTING Runs become
  FAILED with the reason `runner lease expired`. WAITING_MERGE Runs stay, and
  the next lease of the same runner takes them over, since only that runner
  holds their changes.
- A heartbeat assigns unassigned PENDING Runs up to the capacity the runner
  reports, and that capacity is kept on the runner. Each assignment is
  compare-and-swap, so a Run never lands on two leases.
- `GET .../runs` returns the desired state: every non-terminal Run on the
  live lease, with its spec, the `image_url` of its image, the resolved
  policy documents, `credentials` and `merge`, the merge decision of a
  WAITING_MERGE Run or null.
- `credentials` maps each secret the MCP policy names to `value` and
  `expires_at`, only for PENDING, STARTING and STARTED Runs, so the start that
  follows a claim already has them. A missing or expired secret is left out,
  and `expires_at` is at most `NAOS_RUN_CREDENTIAL_TTL_SECONDS` away, so a
  runner that loses its lease loses its credentials with it.

### Runner transitions

A runner may only make these transitions; any other pair gets 409.

| From | To |
|---|---|
| PENDING | STARTING |
| STARTING | STARTED |
| STARTED | STOPPING |
| STOPPING | COLLECTING |
| STARTING, STARTED, STOPPING, COLLECTING, WAITING_MERGE | FAILED, with a reason |

Every transition names the lease and is compare-and-swap on both the status
and the lease. A stale lease gets 409, a Run held by another runner gets 404,
and repeating a transition that already happened is a no-op.

### Merge reports

A runner never sets WAITING_MERGE or COMPLETED itself; it reports what it did
and the API moves the Run ([09](09-overlay-and-merge.md#merge)).

- `POST .../diff` takes `lease_id` and the collected `entries`, at most
  100000. It stores the diff once, applies the merge policy and moves a
  COLLECTING Run to WAITING_MERGE; a repeated report is a no-op.
- `POST .../merge` takes `lease_id` and `outcome`. `applied` carries the
  `applied`, `skipped`, `exported` and `backed_up` paths and completes the
  Run. `conflict` carries `conflicts`, each a `path` and a `reason`, keeps
  the Run waiting and clears the decision.

### Audit events

`GET /api/v1/runs/{run_id}/events` and `GET /api/v1/audit` read the audit
trail, and `POST .../events` takes the runner's own events; the catalogue and
the schemas are in [11](11-observability.md#audit-trail).

Secrets must never be returned accidentally.

Acceptance: invalid input, forbidden transitions, duplicate requests, and immutable policy behavior are tested.
