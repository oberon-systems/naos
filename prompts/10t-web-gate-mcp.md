# Prompt 10t — MCP Gate Events

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`,
`docs/08-mcp-gate.md`, `docs/11-observability.md`, `06c` to `06g`,
`10o-web-mcp.md`, `10q-web-secrets.md`, `10r-web-gate-network.md` and the
boards on page `Runs` in `dev/web/templates/base`.

The `Gates` tab of `10r-web-gate-network.md` has no MCP view yet.
`Run detail — Overview · MCP` of `10o-web-mcp.md` shows what the Run holds
now; this view shows what the agent did with it. Draw these first and build
nothing until they are approved:

- `Run detail — Gates · MCP`:
  - the servers attached and rejected, and each policy change of
    `06f-mcp-live-attach.md` as a marker in time;
  - allowed and denied counts, and the budgets of `06d-mcp-rules.md` with
    what is spent;
  - the calls grouped by server and tool or resource, with count, denials
    and the slowest duration;
  - the table TIME, DECISION, SERVER, TOOL / RESOURCE, CATEGORY, RULE,
    DURATION with the filters All / Allowed / Denied / Secrets;
- `Run detail — Gates · MCP · secrets`: the calls to the built-in `secrets`
  server, each with the secret's name and the decision, and the note that a
  value read belongs to the agent.

A row opens its event in the audit popup of `10j-web-audit.md`. A server, a
secret, a policy, a rule and the Run open on click.

The view reads `GET /audit` with `run_id` and the events `mcp_attached`,
`mcp_rejected`, `mcp_policy_configured`, `mcp_policy_changed`,
`mcp_credentials_updated`, `mcp_call` and `secret_read`, and extends
`GET /runs/{run_id}/gates/{gate}` with the `mcp` grouping. Build what the
landed prompts write and show the rest once they land.

Arguments and results of a call are never logged, and the view never asks
for them. A secret shows by name only.

The UI is never an authorization boundary. Every action goes through API
authorization. Never display secrets.

Acceptance: an operator sees every tool and resource a Run called, which
secrets its agent read, what was denied and by which rule, and when the MCP
policy changed; `make smoke` opens the MCP view of a Run with a denied call.
