# 10 — Web UI

## Prompt

Build the HTMX dashboard after backend security boundaries are implemented.

Views:

- Runs list;
- Run detail;
- console;
- logs;
- network/shell/MCP events: the `Gates` tab of the Run detail, after
  `Logs & Audit`, switches between the gates the Run's policies name. The
  network view shows the policy in force and when the runner configured it,
  allowed and denied counts, the hosts the Run reached grouped by host,
  protocol and rule with the denied ones first, and the requests with the
  filters All, Allowed and Denied; a request opens its audit event, and a
  host or a reason the guest sent shows as plain text;
- filesystem diff;
- merge approval;
- profiles;
- policies, immutable: find by id or digest, read the resolved document and who uses it, create one of each kind;
- models: the `model` policy kind with its providers, credential names and token budget, picked in the new-run dialog and the profile form; the Run overview shows calls and tokens spent against the budget and the last refusals;
- secrets, write-only: see state, expiry, who names and which Runs hold each one, create, rotate, re-date and delete; a value is typed once and never rendered;
- images.

Run creation should make image, host directory, mount mode, network/shell/MCP/model policy, merge policy, timeout, and runtime settings explicit.

The UI is never the authorization boundary. Every action is authorized by the API. Dangerous actions require confirmation. Never display secrets.
