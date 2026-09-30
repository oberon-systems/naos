# Prompt 06h — Model Gateway

Read `AGENTS.md`, `docs/01-security-model.md`, `docs/03-api-design.md`,
`docs/04-runner-design.md`, `docs/05-vm-and-qemu.md`,
`docs/06-network-gate.md`, `docs/08-mcp-gate.md`, `06c-mcp-registry.md` and
`06e-mcp-secrets.md`.

The VM has no network, and an agent needs its model before it can ask for
anything else. Give every Run a model gateway on its runner: the agent talks
plain HTTP to a local port, the runner talks HTTPS to the provider with the
provider's key, and the key never enters the VM. Any agent that takes a base
URL works, whichever vendor made it.

1. Policy — a new policy kind `model`, named by `spec.model.policy`:
   `providers`, each with `name`, `api` (`openai` or `anthropic`), an https
   `url` with the rules of `docs/08-mcp-gate.md`, `credential` (a secret
   name), the exact `models` it may serve, `timeout_seconds`,
   `max_requests_per_minute` and the Run's `max_input_tokens` and
   `max_output_tokens`. A model name routes to exactly one provider.
2. API — resolves the policy like the others and issues the provider
   credentials with the Run's other credentials. They are never listed by
   `secrets__list` nor returned by `secrets__get`.
3. Runner — a gate per Run behind its own vsock device (a virtio-serial port
   has no connection boundaries, see `docs/13-model-gateway.md`):
   - `POST /v1/chat/completions` and `GET /v1/models` for `openai` providers,
     `POST /v1/messages` for `anthropic` ones; any other path is 404;
   - it routes by the request's model, refuses one the policy does not
     name, drops every auth header the agent sent, sets the provider's own
     (`Authorization: Bearer` or `x-api-key`) and redacts the key in answers;
   - streamed answers pass through as they arrive; usage from the answer
     counts against the Run's budget, and a spent budget or rate is 429 in
     the dialect's error shape;
   - request bodies are bounded, and a gate or provider failure is an error,
     never a retry to another provider.
4. Guest — the image ships a unit like `naos-mcp`: `socat` from
   `127.0.0.1:4000` to the runner's vsock port. The Run's environment carries
   `OPENAI_BASE_URL=http://127.0.0.1:4000/v1`,
   `ANTHROPIC_BASE_URL=http://127.0.0.1:4000` and a placeholder key for
   both. Bump the image as the packer rules require.

Every call writes `model_call` with the Run, provider, model, input and
output tokens, duration, decision and category. Prompts, completions and
keys are never logged.

Write `docs/13-model-gateway.md` and update `docs/03-api-design.md`,
`docs/05-vm-and-qemu.md` and `docs/01-security-model.md`.

Acceptance: an agent in the VM reaches an allowed model of each dialect with
a placeholder key; a model outside the policy, a spent budget, an expired
credential and a failing provider are refused; the provider key is never in
the VM, a log or the audit; one Run never spends another's budget; `make
smoke` runs a stub provider of each dialect and calls both through the
gateway.
