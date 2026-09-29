# Prompt 06e — Secrets MCP

Read `AGENTS.md`, `docs/01-security-model.md`, `docs/03-api-design.md`,
`docs/08-mcp-gate.md`, `06c-mcp-registry.md` and `06d-mcp-rules.md`.

Agents need secrets. Serve them as the built-in `secrets` server of the
broker: the agent asks for a secret through `naos` like for anything else,
and the policy decides. Nothing puts a secret in place for the agent.

- The `mcp` policy grants secrets by exact name under `secrets`; a pattern
  or a wildcard is 422.
- `secrets__list` answers the names the Run was granted, never values.
- `secrets__get` takes `name` and answers the value of a granted secret; any
  other name, a missing or an expired secret is a tool error.
- `06d-mcp-rules.md` applies: `secrets__get` may be denied per name and
  carries its own budget.

The API issues to the runner only the secrets the Run's policy names, with
the TTL it already gives credentials, and only while the Run is PENDING,
STARTING or STARTED; the runner holds them in memory for that Run alone.

A value `secrets__get` returns belongs to the agent from then on: it may reach
the model provider, the agent's own files and so the upper disk. The web says
so where a policy grants secrets. naos itself never writes a value to the VM
disk, the console log, the audit or the runner's state directory.

naos puts two kinds of secret in place itself, and neither is ever listed or
returned to the agent: the `credential` of a registry server
(`06c-mcp-registry.md`) and the provider credentials of the model gateway
(`06h-model-gateway.md`).

Every `secrets__get` writes `secret_read` with the Run, the secret name and
the decision, never the value.

Update `docs/08-mcp-gate.md`, `docs/03-api-design.md` and
`docs/01-security-model.md`.

Acceptance: a granted secret comes back only to its Run, a name the policy
does not grant and an expired secret are denied, a server credential is never
listed or returned, no value lands in a naos log, the audit or the runner's
state; `make smoke` reads one granted secret and is refused another.
