# Prompt 06g — Run Isolation

Read `AGENTS.md`, `docs/01-security-model.md`, `docs/02-threat-model.md`,
`docs/04-runner-design.md`, `docs/05-vm-and-qemu.md`, `docs/08-mcp-gate.md`
and `06c` to `06f`.

Runs on one runner share the host, never their MCP servers or secrets. Make
the separation explicit and prove it:

- a broker belongs to one VM and learns its Run from the socket it was given,
  never from anything the guest sends;
- credentials and secrets are held per Run, keyed by the Run's id inside the
  runner, and dropped when the Run leaves STARTED, stops or loses its lease;
- an external server's session and `Mcp-Session-Id` belong to one Run; two
  Runs naming the same registry server open two sessions;
- budgets and rule counters live with the Run;
- nothing of a Run's secrets or sessions reaches the VM directory, the
  archive or another Run's desired state.

Add tests at each boundary: two Runs on one runner with different policies
and secrets, a guest that forges a Run id in its requests, a Run that ends
while the other keeps going, a runner restart, and a lease handed over.

Document in `docs/01-security-model.md` what is isolated, where it is
enforced and what fails closed.

Acceptance: one Run never lists, calls or reads another's servers, secrets,
sessions or budgets on the same runner, before or after a restart; the tests
cover every case above, and `make smoke` runs two Runs side by side and checks
it.
