# 10 — Web UI

## Prompt

Build the HTMX dashboard after backend security boundaries are implemented.

Views:

- Runs list;
- Run detail;
- console;
- logs;
- network/shell/MCP/model events: the `Gates` tab of the Run detail, after
  `Logs & Audit`, switches between the gates the Run's policies name. The
  network view shows the policy in force and when the runner configured it,
  allowed and denied counts, the hosts the Run reached grouped by host,
  protocol and rule with the denied ones first, and the requests with the
  filters All, Allowed and Denied; a request opens its audit event, and a
  host or a reason the guest sent shows as plain text. The shell view, shown
  even for a Run without a shell policy, lists the granted capabilities and
  the guest mount roots, the calls grouped by capability and path with the
  denied ones first, and the calls with the same filters; a path and a reason
  show as plain text, and no command output or file content is ever shown.
  The model view lists the providers with their models, rate and credential
  name, the allowed and denied calls and the slowest one, the tokens spent
  against the budget and the requests of each provider against its rate per
  minute, the calls grouped by provider and model with the denied ones first,
  and the calls with the same filters; a refused call names its category, and
  no prompt, completion or key is ever shown.
  The MCP view shows the policy the Run holds, the allowed and denied calls
  and the secrets read, a timeline with the policy in force, each server's
  calls per minute and numbered markers for the gate events listed below it,
  the rule budgets with what each spent, the calls grouped by server, tool
  and resource with the denied ones first, and the calls with the filters
  All, Allowed, Denied and Secrets. A denied call names the rule that denied
  it or whose budget is spent. The Secrets filter lists every name the agent
  asked for, granted or not, notes that a value read belongs to the agent,
  and shows the calls to the `secrets` server with the name only; no
  argument, result or secret value is ever shown;
- filesystem diff, with the content of the picked file entry: the host and
  agent sides scrolled together, the lines that differ marked, a jump to the
  next difference and bytes that are not text shown as hex. Both sides are
  read in ranges as the operator scrolls, with no size cap, and rendered as
  inert text: no markup, links or terminal escapes are interpreted and every
  byte is kept. A directory and a rejected entry have no content;
- merge approval;
- profiles;
- policies, immutable: find by id or digest, read the resolved document and who uses it, create one of each kind, and delete one that no profile names and no Run took, after a confirm; a policy in use opens a refusal instead;
- models: the `model` policy kind with its providers, credential names and token budget (a new form starts at 2000000 input and 200000 output tokens, a provider starts as openai with its real URL, the next one added starts at the next API not yet in the form, picking another API among openai, anthropic and gemini changes the name and the URL unless one was typed, the credential is picked from the existing secrets and nothing else is accepted, a bad name is explained in words, and the models of that API are offered beside any typed name), picked in the new-run dialog and the profile form; the Run overview shows calls and tokens spent against the budget and the last refusals;
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
- images: the catalog, registering one, and deleting one from its popup after a confirm; an image a Run that has not ended still boots is refused with the Runs named.

Every form checks its required fields in the browser (`required`, `pattern`, `:user-invalid`): a field left empty turns red with a short message under it once the operator submits, nothing is red before that and nothing is sent. The API check stays and names the field. Buttons that only add or drop a row skip the check.

Run creation should make image, host directory, mount mode, network/shell/MCP/model policy, merge policy, timeout, and runtime settings explicit.

The UI is never the authorization boundary. Every action is authorized by the API. Dangerous actions require confirmation. Never display secrets.
