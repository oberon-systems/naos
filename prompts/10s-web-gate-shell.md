# Prompt 10s — Shell Gate Events

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`,
`docs/07-shell-gate.md`, `docs/11-observability.md`,
`10r-web-gate-network.md` and the boards on page `Runs` in
`dev/web/templates/base`.

The `Gates` tab of `10r-web-gate-network.md` has no shell view yet. Draw
these first and build nothing until they are approved:

- `Run detail — Gates · Shell`:
  - the policy in force, its capabilities and the mount root they are
    confined to;
  - allowed and denied counts;
  - the calls grouped by capability with their paths and counts, the denied
    ones first;
  - the table TIME, DECISION, CAPABILITY, PATH, REASON with the filters
    All / Allowed / Denied;
- `Run detail — Gates · Shell · empty`: a Run whose shell policy grants
  nothing.

A row opens its event in the audit popup of `10j-web-audit.md`. The policy
and the Run open on click.

The view reads `GET /audit` with `run_id` and the events
`shell_policy_configured`, `shell_allowed` and `shell_denied`, and extends
`GET /runs/{run_id}/gates/{gate}` with the `shell` grouping.

A path and a reason come from the guest's request: render them as inert text.
The gate never logs a command's output or a file's content, and the view
never asks for either.

The UI is never an authorization boundary. Every action goes through API
authorization. Never display secrets.

Acceptance: an operator sees every capability a Run used, on which paths, and
why a call was denied; `make smoke` opens the shell view of a Run with a
denied call.
