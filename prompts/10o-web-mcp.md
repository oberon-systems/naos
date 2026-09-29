# Prompt 10o — MCP

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`,
`docs/08-mcp-gate.md`, `06c` to `06f` and the boards in
`dev/web/templates/base`.

No board shows the MCP registry or a live attach yet. Draw them
first and build nothing until they are approved:

- `MCP — List`: built-in and external servers, their url, credential name,
  limits, the policies and Runs that name each, and a popup per server;
- `MCP — Register`: the external server form;
- in the `mcp` policy popup and form, the secrets it grants to agents, by
  name, with the note that a read value belongs to the agent; the secrets
  themselves are managed by `10q-web-secrets.md`;
- `Run detail — Overview · MCP`: the servers the Run holds now, the policy
  history and `Change MCP policy` with its confirm.

Every server, secret, policy and Run mentioned on these screens opens it on
click.

The UI is never an authorization boundary. Every action goes through API
authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: every shipped screen matches an approved board; an operator
registers a server, grants secrets to agents and changes the MCP policy of a
running Run; no page ever carries a secret value.
