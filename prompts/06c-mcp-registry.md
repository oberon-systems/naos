# Prompt 06c — MCP Registry

Read `AGENTS.md`, `docs/03-api-design.md`, `docs/08-mcp-gate.md` and
`06-mcp-gate.md`.

The agent sees one MCP server, `naos`, and reaches every other one through
it. Today an external server lives inline in each `mcp` policy. Make the
servers a catalog of the stack instead, which policies name.

Add `mcp_servers` to the API:

- `POST /api/v1/mcp-servers` registers an external server: `name`, `url`,
  optional `credential` (a secret name), `timeout_seconds`,
  `max_calls_per_minute`, with the rules of `docs/08-mcp-gate.md`;
- `GET /api/v1/mcp-servers` lists built-in and external servers, newest
  first; `GET /api/v1/mcp-servers/{name}` answers one or 404;
- `PATCH` changes `url`, `credential` and limits of an external server,
  `POST .../disable` takes it out of every Run that has not started yet;
- built-in servers (`shell`, `network`, `secrets`) are listed with `kind:
  built-in`, cannot be created, changed or disabled, and carry no url.

An `mcp` policy names servers by registry name and keeps the tools and
resources it grants. Resolving the policy for a Run copies the registry entry
at that moment into the resolved document, so a later `PATCH` never changes a
Run that already holds its policy. An unknown or disabled name is 422 at Run
creation and a deny in the broker.

The registry holds no secret value: `credential` stays a reference, resolved
as `docs/03-api-design.md` issues credentials today. It leaves the `mcp`
policy and lives on the registry entry only: it is naos's access to that
server, the broker adds it as `Authorization: Bearer`, and the agent never
lists or receives it. Every other secret reaches the agent through the
`secrets` server of `06e-mcp-secrets.md`.

Every registry write is audited with actor `operator` and never carries a
secret value. Update `docs/03-api-design.md` and `docs/08-mcp-gate.md`.

Acceptance: an operator registers an external server once and names it from
several policies; a Run keeps the server entry it started with; an unknown or
disabled server never reaches the network; `make smoke` runs a Run against a
registered server.
