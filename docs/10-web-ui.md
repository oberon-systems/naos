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
  host or a reason the guest sent shows as plain text. The shell view, shown
  even for a Run without a shell policy, lists the granted capabilities and
  the guest mount roots, the calls grouped by capability and path with the
  denied ones first, and the calls with the same filters; a path and a reason
  show as plain text, and no command output or file content is ever shown;
- filesystem diff;
- merge approval;
- profiles;
- policies, immutable: find by id or digest, read the resolved document and who uses it, create one of each kind;
- models: the `model` policy kind with its providers, credential names and token budget, picked in the new-run dialog and the profile form; the Run overview shows calls and tokens spent against the budget and the last refusals;
- MCP: the `MCP` page lists the built-in and external servers with their
  url, credential name, limits, the policies that name them and the Runs that
  hold them; a server opens a popup with its entry, its policies and Runs and
  its calls of the day. An external server is registered, edited, disabled
  after a confirm that names the Runs it leaves, and enabled again;
- `mcp` policies: the detail lists the rules by their index, the servers they
  name and the secrets granted to agents; the form edits the rules and the
  grants, each a secret by its exact name with an optional read budget, and
  whether the agent may list the granted names;
- the Run overview shows an MCP card: the policy the Run holds and the one it
  was created with, its calls and secret reads, the servers it holds now and
  the policy history. `Change MCP policy` edits what the Run holds, kept
  temporary or saved as a new policy, or picks a stored policy, and asks to
  confirm with the servers and secrets the agent gains or loses;
- secrets, write-only: see state, expiry, who names and which Runs hold each one, create, rotate, re-date and delete; a value is typed once and never rendered;
- images.

Run creation should make image, host directory, mount mode, network/shell/MCP/model policy, merge policy, timeout, and runtime settings explicit.

The UI is never the authorization boundary. Every action is authorized by the API. Dangerous actions require confirmation. Never display secrets.
