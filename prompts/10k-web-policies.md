# Prompt 10k — Policies

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`,
`docs/05-vm-and-qemu.md`, `docs/06-network-gate.md`, `docs/07-shell-gate.md`,
`docs/08-mcp-gate.md`, and the boards on page `Policies` in
`dev/web/templates/base`: `Policies — List`, `Policy detail — Network document`,
`Policy detail — Mount document`, `Policy detail — Shell document`,
`Policy detail — MCP document`, `Policy detail — Used by`,
`Policy form — Network`, `Policy form — Mount`, `Policy form — Shell`,
`Policy form — MCP` and `Policy form — Already exists`.

Build only what the boards show. When a screen named here has no board,
report the missing board and build nothing for it.

`packages/api` lacks what the page needs; add it first:

- `GET /policies` takes `q`, a substring of the id or the digest ignoring
  case, and adds `profiles`, the profiles naming the policy now, and
  `runs_open` and `runs_total`, the Runs whose spec names it;
- `GET /profiles?policy=` and `GET /runs?policy=` list those profiles and Runs.

Build the Policies list at `/policies` over `GET /policies`, newest first: the
`Policies` nav item between `Profiles` and `Audit`, the tiles Mount / Network
/ Shell / MCP, the search, the filters All / Mount / Network / Shell / MCP as
`kind`, and the table POLICY, KIND, DOCUMENT, USED BY, CREATED with the
`Open` row action. The DOCUMENT line reuses `summary_of` from
`naos_web.profiles`; add no second summarizer.

Build the policy popup at `/policies/{policy_id}` over
`GET /policies/{policy_id}`: the header with id, kind, digest and usage,
`New from this` and `Copy id`, and the tabs Document / Used by. Document
renders the resolved document of each kind as its board does, with the
Identity card and the canonical JSON; the MCP Secrets card reads
`GET /secrets/{name}` for `expires_at` only. Used by lists the profiles and
the Runs from the new filters.

Build the form at `/policies/new`: the kind switch, the editor of each kind
and `Create policy` posting `POST /policies`. A 201 opens the new policy; a
200 opens `Policy form — Already exists`; a 422 shows the API message the way
the profile form does. `New from this` opens the form of the same kind
filled from the document; a mount document maps back as its first mount to
`workspace` and the rest to `home`, relative to `/home/naos`.

A policy is immutable: no view offers an edit or a delete. A credential is a
secret name; never render or request a secret value.

The UI is never an authorization boundary. Every action goes through API
authorization. Never display secrets.

Acceptance: an operator finds a policy by id or digest, reads its resolved
document and who uses it, and creates a policy of each kind; an equivalent
document shows the existing id instead of a new one; no view renders a secret
value or a way to change a stored policy.
