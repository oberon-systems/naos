# Prompt 10r — Network Gate Events

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`,
`docs/06-network-gate.md`, `docs/11-observability.md`,
`10d-web-run-detail.md`, `10f-web-run-logs.md`, `10j-web-audit.md` and the
boards on page `Runs` in `dev/web/templates/base`.

`docs/10-web-ui.md` names network, shell and MCP events as views of their
own, and no board shows them yet. This prompt adds the `Gates` tab to the run
detail popup with its network view; `10s`, `10t` and `10u` add the other
gates to the same tab.

Draw these first and build nothing until they are approved:

- `Run detail — Gates · Network`: the `Gates` tab next to `Logs & Audit`, a
  switch between the gates the Run's policies name, and for the network gate:
  - the policy in force and when the runner configured it;
  - allowed and denied counts;
  - the hosts the Run reached, grouped by host with protocol, rule and count,
    the denied ones first;
  - the table TIME, DECISION, PROTOCOL, HOST, RULE, REASON with the filters
    All / Allowed / Denied;
- `Run detail — Gates · Network · empty`: a Run whose network policy allows
  nothing, and a Run that has not started yet.

A row opens its event in the audit popup of `10j-web-audit.md`. The policy,
a rule and the Run open on click.

The tab reads `GET /audit` with `run_id` and the events
`network_policy_configured`, `network_allowed` and `network_denied`. Add
`GET /runs/{run_id}/gates/{gate}` for the grouped hosts and counts, computed
from the audit, never a copy of it; the next gate prompts extend it.

A host and a reason come from the guest's request: render them as inert text.
Events are typed: render the known fields and mark an unknown one as refused.

The UI is never an authorization boundary. Every action goes through API
authorization. Never display secrets.

Acceptance: an operator sees every host a Run tried to reach, what the policy
allowed and why the rest was denied, and opens the rule that decided;
`make smoke` opens the tab of a Run with a denied request.
