# 03 — API Design

## Prompt

Implement the control-plane API as a thin HTTP layer over application/domain services.

## Stack

Python 3.12+, FastAPI, Pydantic, SQLModel. `NAOS_DATABASE_URL` names any
database SQLAlchemy supports. The schema is plain tables with no triggers,
stored procedures or foreign keys, so the same SQL runs everywhere, and
[Alembic](https://alembic.sqlalchemy.org) owns it ([Schema](#schema)). The Run
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
| `NAOS_DATABASE_AUTO_MIGRATE` | `false` | Bring an empty database to head on start ([Schema](#schema)) |
| `NAOS_ALLOWED_MOUNT_ROOTS` | `[]` | Host directories a mount policy may name |
| `NAOS_OPERATOR_TOKEN_SHA256` | unset | SHA-256 of the operator token; unset closes every operator route |
| `NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256` | unset | SHA-256 of the enrollment token; unset closes registration |
| `NAOS_RUNNER_TOKEN_TTL_SECONDS` | `86400` | Runner token lifetime, 60 to 604800 |
| `NAOS_LEASE_TTL_SECONDS` | `60` | Lease extension per heartbeat, 5 to 3600 |
| `NAOS_LEASE_SWEEP_INTERVAL_SECONDS` | `15` | Background lease sweep period, 1 to 3600 |
| `NAOS_RUN_CREDENTIAL_TTL_SECONDS` | `300` | Lifetime of a credential issued to a runner, 60 to 3600 |
| `NAOS_CONSOLE_LIMIT_BYTES` | `8388608` | Console output kept per Run, 4096 to 1073741824 |

## Schema

Alembic owns the schema and every change to it; the api never creates a table
itself. The revisions live in `naos_api/migrations/versions/`, ship in the
wheel and the api image, and run on the connection `NAOS_DATABASE_URL` names.
There is no `alembic.ini`.

On start the api compares the revision in the database with the head it ships:

| Database | What the api does |
|---|---|
| At head | Starts |
| Empty | Upgrades to head when `NAOS_DATABASE_AUTO_MIGRATE` is true, refuses otherwise |
| Behind head | Refuses and names both revisions |
| At a revision it does not know | Refuses and names both revisions |
| Tables but no revision | Refuses and points at `naos-api migrate` |

`NAOS_DATABASE_AUTO_MIGRATE` is on in the tests, `make smoke` and
`make kickstart`, and off by default. A revision the api does not know usually
means a newer api migrated the database, so `migrate` refuses it as well.

| Command | What it does |
|---|---|
| `naos-api migrate`, `make migrate` | Brings the database to head |
| `naos-api downgrade` | Takes the database one revision back |

A database `create_all` built before Alembic took over has tables but no
revision. `naos-api migrate` builds revision `0001` on an empty SQLite,
compares tables, columns, nullability and indexes with it, and stamps the
database at `0001` when they match. When they differ it names the tables and
stamps nothing.

A revision whose change cannot be undone says so in its `downgrade()`, which
then refuses. Revision `0001` goes back to an empty database.

### Changing a model

Until the first stack-wide release no revision is added: a model change edits
`0001`, and a database built from an older `0001` is thrown away. From the
first release on, every model change comes with its own revision in the same
commit. Autogenerate may draft it, and a person reviews it.

`make test` runs every revision from empty to head and back, and fails when
autogenerate finds a difference between head and the models. The `migrations`
pre-commit hook runs the same test whenever the models or the migrations
change.

## Operator credentials

Every route under `/api/v1` except the runner's own routes
([Runner credentials](#runner-credentials)) takes the operator token as a Bearer
credential. `GET /runners` and the revoke and drain routes are operator routes. The API holds only its SHA-256 in
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
POST /api/v1/runs/{run_id}/policies
GET /api/v1/runners
POST /api/v1/runners/{runner_id}/revoke
POST /api/v1/runners/{runner_id}/drain
POST /api/v1/runners/register
POST /api/v1/runners/{runner_id}/heartbeat
GET /api/v1/runners/{runner_id}/runs
GET /api/v1/runs/{run_id}/console
WS /api/v1/runs/{run_id}/attach
POST /api/v1/runners/{runner_id}/runs/{run_id}/console
GET /api/v1/runs/{run_id}/events
GET /api/v1/runs/{run_id}/gates/{gate}
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
DELETE /api/v1/profiles/{profile_id}
POST /api/v1/profiles/{profile_id}/runs
POST /api/v1/images
GET /api/v1/images
GET /api/v1/images/{image_id}
POST /api/v1/secrets
GET /api/v1/secrets
GET /api/v1/secrets/{name}
POST /api/v1/secrets/{name}/rotate
PATCH /api/v1/secrets/{name}
DELETE /api/v1/secrets/{name}
POST /api/v1/mcp-servers
GET /api/v1/mcp-servers
GET /api/v1/mcp-servers/{name}
PATCH /api/v1/mcp-servers/{name}
POST /api/v1/mcp-servers/{name}/disable
POST /api/v1/mcp-servers/{name}/enable
GET /api/v1/audit
GET /api/v1/audit/summary
GET /api/v1/audit/{event_id}
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
| `mcp_document` | The registry entries, the rules and the names of the granted secrets the Run holds, as the runner reads them, or null without an `mcp` policy |
| `policy_history` | On `GET /runs/{run_id}` only: every policy change of the Run, oldest first, as `seq`, `kind`, `previous_id`, `policy_id` and `created_at` |
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

`GET /runs/{run_id}/gates/{gate}` is what one gate did for one Run, counted
from the audit on every read and never stored. The only `gate` so far is
`model`: `calls`, `denied`, `input_tokens` and `output_tokens` summed over the
Run's `model_call` events, and `refusals`, the last five denied calls newest
first, each with `id`, `at`, `provider`, `model` and `category`. The budget
itself is in the model policy ([13](13-model-gateway.md#budget-and-rate)).

## Console

The runner ships the output of a VM's `ttyS0` as it appears
([04](04-runner-design.md#console)). The API keeps it per Run, up to
`NAOS_CONSOLE_LIMIT_BYTES`, and accepts it only from the runner whose lease
holds the Run.

`GET /runs/{run_id}/console` answers with the whole log as `text/plain`, and
with the text of it rather than the screen it drew: escape sequences and the
lines that hold nothing else are stripped, and a report that only redrew the
screen is never stored in the first place. The serial stream keeps no time, so
each line starts with the UTC time the API received the chunk the line began
in, as `2026-01-01T00:00:00Z  naos login: naos`.

`WS /runs/{run_id}/attach` is the live view. It takes the operator token in the
`Authorization` header, writes a `console_attached` audit event with actor
`operator`, sends the stored log so far as binary frames, and then every byte
the runner reads, raw - redraws and lone echoed spaces included, since a
terminal needs all of them and only the store is normalised. Nothing is held
for a terminal that is not open. The socket closes once the Run has left
STOPPING and every byte was sent.

A viewer speaks back. A text frame is the grid it asks for,
`{"cols": n, "rows": n, "view": "panel" | "window", "take": bool}`, and the answer carries
`driving`, which says whether this viewer holds the guest's one size. A binary
frame is what its operator typed, and it reaches the guest only from the viewer
that is driving. A frame with `take` moves the claim to its viewer whatever kind
it is: a terminal sends it when its operator opens it or clicks into it, and its
periodic frames only keep a claim it already holds. The claim ages out after
fifteen seconds, so closing the terminal that held it frees the keyboard.
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
and `lease_expires_at` window, `revoked_at` and `drained_at`. `capacity`
outlives the lease, so the slots of a runner that stopped answering are still
known.

`placement` repeats what the runner reported with the `address` the API saw
it from, and `heartbeat_seconds` how often it beats; both are empty until
the runner reports them. `lease_id` names the live lease, or for a stale runner its latest lease, whose
deadline is `lease_lapsed_at`. The token is described by its windows only:
`token_expires_at`, `token_rotates_at` (the first heartbeat past half the
lifetime gets a replacement) and `prev_token_expires_at` while the previous
token is still accepted. No token value or hash is part of the answer.

## Revoking and draining runners

`POST /runners/{runner_id}/revoke` refuses the runner's tokens from then on and
ends its live lease at once. Runs on that lease fail with `runner revoked`,
PENDING Runs assigned to it go back to the queue, and a Run of that runner
waiting for its merge fails too, since its changes stay on a host that can no
longer take a lease. The agent fences its VMs as on any lost lease.

`POST /runners/{runner_id}/drain` keeps the runner and its lease, but assigns
it no new Run; the Runs it holds finish as usual. A drained runner offers no
free slot, and a Run pinned to it is refused with 422.

Both answer with the runner as `GET /runners` lists it, 404 for an unknown
runner, and repeat harmlessly. Draining a revoked runner returns 409. A
revoked agent registers again under a new identity while its enrollment token
is valid ([04](04-runner-design.md#lease-fencing)).

`GET /runs?runner={runner_id}` lists the Runs that any lease of that runner
held, newest first. `GET /audit?order=desc` reads the trail newest first, so
a screen can show the latest entries of one runner.

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
`mntpol_`, `netpol_`, `shellpol_`, `mcppol_`, `modelpol_`.

A policy may carry a `name`, lowercase letters, digits, `.`, `_` and `-`, at
most 64 characters and unique within its kind. Nothing overwrites a stored
policy: a changed document is another policy with another id. A name that
another policy of the kind holds is 409, and so is a second name for a
document that already has one. An unnamed policy takes the name of a later
`POST` with the same document, and `policy_named` records it.

A Run names a policy per kind under `spec.mounts`, `spec.network`, `spec.shell`,
`spec.mcp` and `spec.model`, and the spec never changes after that.
`spec.model` is required: a VM gets its model gateway only when it boots
([13](13-model-gateway.md#transport)), so a Run without one could never take
a model later, and `POST /runs` answers 422. A profile may leave it out, but a
Run started from that profile is refused the same way. The
runner receives the resolved document as a snapshot rather than
the id. For `mcp` that snapshot is the Run's own, taken when the Run is
created ([MCP servers](#mcp-servers)) and again when an operator changes the
policy ([Changing a policy of a started Run](#changing-a-policy-of-a-started-run)). The network document is described in [06](06-network-gate.md), the
shell document in [07](07-shell-gate.md), the MCP document in
[08](08-mcp-gate.md), the model document in [13](13-model-gateway.md).

`GET /policies` lists every policy newest first; `kind` narrows it to one
kind, and `q` to the policies whose id, digest or name holds the substring, ignoring
case. Each policy, here and in `GET /policies/{policy_id}`, carries
`profiles`, the ids of the profiles naming it now, and `runs_open` and
`runs_total`, the Runs that hold it now, not terminal and ever.
`GET /profiles?policy=` and `GET /runs?policy=` list those profiles and Runs.
The web form reads the list to offer a policy per kind.

## Changing a policy of a started Run

An operator edits the policy of a gate while the Run works, without a restart.
`POST /api/v1/runs/{run_id}/policies` takes `kind` and exactly one of:

- `document`, the edited document. By default it is temporary: validated
  like `POST /policies`, it belongs to this Run alone, creates no policy and
  has no id. With `save` true it is stored as a policy first, under `name`
  when one is given, and the Run holds that policy;
- `policy_id`, a policy that is already stored.

The policy a Run or a profile uses is never overwritten either way: an edit
changes this Run only, and a saved edit is a new policy.

```bash
curl -fsS "$api/api/v1/runs/$run/policies" -d '{"kind": "mcp", "document": {"rules": [{"server": "alpha", "tool": "search", "effect": "allow"}]}}'
curl -fsS "$api/api/v1/runs/$run/policies" -d '{"kind": "mcp", "save": true, "name": "alpha-search", "document": {"rules": [{"server": "alpha", "tool": "search", "effect": "allow"}]}}'
curl -fsS "$api/api/v1/runs/$run/policies" -d '{"kind": "mcp", "policy_id": "mcppol_0123456789abcdef0123456789abcdef"}'
curl -fsS "$api/api/v1/runs/$run/policies" -d '{"kind": "shell", "document": {"allow": ["read_file", "grep"]}}'
curl -fsS "$api/api/v1/runs/$run/policies" -d '{"kind": "network", "document": {"allow": [{"protocol": "https", "host": "example.com"}]}}'
curl -fsS "$api/api/v1/runs/$run/policies" -d '{"kind": "model", "policy_id": "modelpol_0123456789abcdef0123456789abcdef"}'
```

| Answer | When |
|---|---|
| 200 with the Run | the Run holds the policy from now on, or already held exactly it |
| 404 | the Run does not exist |
| 409 | the Run is not STARTED, or the `name` to save under is taken |
| 422 | `kind` is `mount`, the policy does not exist or is of another kind, the document is invalid, belongs to another kind or names an unknown or disabled server, or `save` or `name` comes without what it needs |

For `mcp` the API resolves the registry at that moment and replaces the
Run's `mcp_document`, so the next desired state carries the new servers,
rules, credentials and granted secrets. `spec.mcp` keeps the policy the Run
was created with. Every change adds a row to `policy_history`, where
`policy_id` is null for a temporary document, and writes `policy_changed`
([11](11-observability.md)). The runner applies the change on its next
reconcile pass ([08](08-mcp-gate.md#live-changes)).

For `network`, `shell` and `model` the document is resolved like
`POST /policies` resolves it. The Run keeps it in the `policy_history` row of the change, and
the desired state carries the document of the latest row of that kind, or the
policy the Run was created with when there is none. `spec.network`,
`spec.shell` and `spec.model` keep that first policy. The runner swaps the
rules ([06](06-network-gate.md#live-changes)), the capabilities
([07](07-shell-gate.md#live-changes)) or the providers
([13](13-model-gateway.md#live-changes)) on its next reconcile pass; the
mount policy never changes. The credentials of the desired state follow the
held `model` document, so the key of a provider that was taken away is no
longer issued.

## Profiles

A profile is a named, reusable Run spec without `image` and `runner`: the
`runtime`, the five policy references, `merge` and `timeout`. A Run copies the
profile and never references it, so a later update reaches no started Run.

- `POST /profiles` takes `name` and `spec`; `name` matches
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and is unique. The policies it names
  must exist, otherwise it returns 422.
- `GET /profiles` lists by name; `q` matches a substring of the name or the id,
  ignoring case. Each profile carries `active_runs`, the Runs copied from it
  that are not terminal, `active_run` with `id`, `seq` and `status` of the
  oldest of them, `last_run_at`, null when it never ran, and `runs_total`
  and `runs_24h`, the Runs copied from it ever and in the last day.
- `PUT /profiles/{profile_id}` takes a new `spec`. While any Run copied from
  the profile is not terminal it returns 409 naming that Run; the check and
  the write are one statement.
- `DELETE /profiles/{profile_id}` answers 204. While any Run copied from the
  profile is not terminal it returns 409 naming those Runs, with the same
  one-statement check as the update. Runs keep `profile_id` and their own
  spec after the profile is gone.
- `POST /profiles/{profile_id}/runs` takes `image` and an optional `runner`
  and creates a PENDING Run from the stored spec. The Run records the profile
  it was copied from.
- `GET /runs?profile=` lists the Runs copied from a profile, and
  `GET /audit?profile_id=` returns its created, updated and deleted events
  and every event of those Runs.
- A gate the spec leaves at `null` has no policy, and nothing passes it.

A Run spec may name a `runner` id. The Run is then offered to that runner only;
unset lets any runner claim it. An unknown or revoked runner returns 422.

## MCP servers

The registry is the catalog of MCP servers of the stack. The rules of an
`mcp` policy name servers by registry `name` ([08](08-mcp-gate.md#rules));
the url, the credential and the limits live on the registry entry
([08](08-mcp-gate.md#external-servers)).

- `POST /api/v1/mcp-servers` registers an external server: `name`, `url`,
  optional `credential`, `timeout_seconds` and `max_calls_per_minute`. It
  answers 201, 409 for a name that is taken and 422 for a field that breaks
  the rules of [08](08-mcp-gate.md#external-servers).
- `GET /api/v1/mcp-servers` lists the external servers newest first, then
  the built-in ones. `GET /api/v1/mcp-servers/{name}` answers one or 404.
- `PATCH /api/v1/mcp-servers/{name}` takes any of `url`, `credential`,
  `timeout_seconds` and `max_calls_per_minute`; `credential` may be null,
  the others may not. An empty body gets 422.
- `POST /api/v1/mcp-servers/{name}/disable` sets `disabled_at` and takes the
  server out of every PENDING Run. `POST .../enable` clears it. Both are
  no-ops when nothing changes.
- The built-in servers `shell`, `network` and `secrets` are listed with
  `kind` `built-in` and no url. Registering, changing, disabling or enabling
  one gets 409. A rule may name one, for the tools it has.

Each external server carries `id`, `name`, `kind` `external`, `url`,
`credential`, `timeout_seconds`, `max_calls_per_minute`, `disabled_at`,
`created_at`, `updated_at` and `policies`, the ids of the `mcp` policies
whose rules name it. A built-in server carries `policies` too.

`POST /policies` validates the rules of an `mcp` document and answers 422
for a server the registry does not hold, an unknown tool or argument of a
built-in server, an invalid pattern and a rule that can never match
([08](08-mcp-gate.md#rules)). It stores the rules in a fixed order, so
equivalent documents share one digest. `POST /runs` copies the registry
entry of every server an allow rule names into the Run beside the rules, so
a later `PATCH` never reaches a Run that exists. A name that is unknown or
disabled at that moment gets 422. A Run without an `mcp` policy has no rules
and so no tools at all.

A disable reaches only Runs that are still PENDING and removes the server's
entry, never a rule: from STARTING on a Run keeps the entry it has until an
operator changes its policy. The runner reads the desired state again after its
claim and starts the Run from that answer, so a disable that lands before the
claim is always in effect.

`credential` is a secret name and never a value. It is naos's own access to
that server: the runner gets the value with the Run's credentials, and the
agent never lists or receives it.

## Secrets

A secret is a value a registry server or a model policy names as its
credential, or an `mcp` policy grants to the agent
([08](08-mcp-gate.md#secrets)), always by `name`. The API stores it as given, without encryption for now, and never
returns its value: no response, error or audit event carries it.

- `POST /api/v1/secrets` takes `name`, `value` and an optional `expires_at`
  and answers 201 with the secret as `GET` reads it. A name that already
  exists gets 409.
- `name` matches `^[a-z0-9][a-z0-9._-]{0,63}$`. `value` is 1 to 8192
  characters of visible ASCII, spaces, tabs and line breaks, so a JSON key
  file fits, and not only whitespace. Anything else gets 422.
- `GET /api/v1/secrets` lists by name and takes `q`, matched against the
  name, the id and what names the secret, `state` and `used`.
- `GET /api/v1/secrets/{name}` returns one secret with its `runs`, or 404.
- `POST /api/v1/secrets/{name}/rotate` takes a new `value` and sets
  `rotated_at`. The name and the id stay, and the next issue to a runner
  carries the new value; a Run holding the old one keeps it until its TTL.
- `PATCH /api/v1/secrets/{name}` takes `expires_at` only, a timestamp or
  null for never.
- `DELETE /api/v1/secrets/{name}` answers 204. While anything in `named_by`
  or `held_by` exists it answers 409, names them in `detail` and records
  `secret_delete_refused`.

Each secret carries these fields beside `id`, `name`, `expires_at`,
`created_at` and `rotated_at`:

| Field | Meaning |
| --- | --- |
| `state` | `valid`, `expiring` within 7 days, or `expired` |
| `named_by` | `kind` `reg` with the registry server `id` and its name in `server`, `kind` `model` with the model policy `id` and the provider in `server`, or `kind` `grant` with the `mcp` policy `id` and `server` null |
| `held_by` | The PENDING, STARTING and STARTED Runs it was issued to: `run_id`, `seq`, `status`, `profile_id` |
| `runs` | `GET` of one secret only: every Run it was ever issued to, with `issued` count and `last_at` |

`GET /api/v1/audit?secret=<name>` returns the secret's own events, the
`secret_read` events of the runners and the `credentials_issued` events that
name it.

## Images

The API keeps the catalog of images a Run may boot: what to boot and where to
download it from. It stores no image bytes; the runner downloads the file
itself and trusts the digest, not the host. The project builds its own images
as described in [packer/README.md](../packer/README.md), but an image may come
from anyone.

- An operator registers an image with `POST /api/v1/images` and a body of
  `id`, `version`, `digest` and `url`. The API answers 201 and never contacts
  the url.
- `name`, `size_bytes` and `built_at` are optional facts the operator states.
  The API checks their shape only; nothing measures or verifies them.
- The url must use https and must not carry credentials; any other url gets
  422.
- Every field is immutable and image rows are never deleted: no endpoint or
  service writes them, like the Run spec. The same `id` or `digest` with any
  other value gets 409.
- `GET /images` adds `runs_open` and `runs_total`, the Runs that boot the
  image now and ever. `GET /runs?image=` lists those Runs, and
  `GET /audit?image_id=` returns the registration and every event of them.
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
- Once the operator revokes a runner, both of its tokens get 401.

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
  reports, and that capacity is kept on the runner. It may also carry
  `interval_seconds`, how often the runner beats, and `placement`, which
  registration takes too (see [Runner placement](#runner-placement)). Each assignment is
  compare-and-swap, so a Run never lands on two leases.
- `GET .../runs` returns the desired state: every non-terminal Run on the
  live lease, with its spec, the `image_url` of its image, the resolved
  policy documents, `credentials` and `merge`, the merge decision of a
  WAITING_MERGE Run or null.
- `credentials` maps each secret the Run's MCP snapshot and model policy name to `value` and
  `expires_at`, only for PENDING, STARTING and STARTED Runs, so the start that
  follows a claim already has them. `secrets` does the same for the secrets
  the Run's `mcp` policy grants to the agent, and one `credentials_issued`
  event names both. A missing or expired secret is left out,
  and `expires_at` is at most `NAOS_RUN_CREDENTIAL_TTL_SECONDS` away, so a
  runner that loses its lease loses its credentials with it.

### Runner placement

`placement` is what the agent says about where it runs: `host`, `zone`,
`platform`, `version` and up to 16 `labels`. The API refuses a request
whose placement carries a control character or a label outside
`[A-Za-z0-9._-]`, since these values reach an operator's screen. It records
the peer address it saw the request from as `address`. A heartbeat without
`placement` keeps the last one.

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

`GET /api/v1/runs/{run_id}/events`, `GET /api/v1/audit` and its `summary`
and `{event_id}` read the audit trail, and `POST .../events` takes the runner's own events; the catalogue and
the schemas are in [11](11-observability.md#audit-trail).

Secrets must never be returned accidentally.

Acceptance: invalid input, forbidden transitions, duplicate requests, and immutable policy behavior are tested.
