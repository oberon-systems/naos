# 13 — Model Gateway

## Prompt

The VM has no network, and an agent needs its model before it can ask for
anything else. Give every Run a model gateway on its runner: the agent talks
plain HTTP to a local port, the runner talks HTTPS to the provider with the
provider's key, and the key never enters the VM. Any agent that takes a base
URL works, whichever vendor made it.

## Transport

The agent reaches the gateway over its own virtio-serial port:

```text
agent -> 127.0.0.1:4000 -> socat -> /run/naos/model -> virtio-serial naos.model -> model.sock -> runner
```

The guest side has no logic. `naos-model` runs `socat` with `fork` and
`max-children=1`, so the port carries one HTTP connection at a time and a
parallel call waits in the listen backlog. Every answer carries
`Connection: close`, which makes the client close and lets the next
connection in.

A virtio-serial port is one byte stream with no connection boundaries. The
runner learns them from the second QMP monitor, `events.sock`, which reports
`VSERPORT_CHANGE` when the guest opens or closes `naos-model`:

| Event | What the runner does |
|---|---|
| close, nothing written yet | drops a partial request and keeps the socket |
| close after an answer, or during one | aborts the provider call, reopens `model.sock` and carries over what the next connection already sent |
| bytes after an answer, before the close | drops them, the connection is over |

Reopening the socket is what keeps the unread tail of an aborted answer away
from the next connection: QEMU drops unread bytes when the runner hangs up,
and a guest write waits until the runner is back.

## Routes

| Request | Dialect | Answer |
|---|---|---|
| `POST /v1/chat/completions` | openai | the provider's answer |
| `GET /v1/models` | openai | the models of the openai providers, built from the policy |
| `POST /v1/messages` | anthropic | the provider's answer |
| anything else | - | 404 |

The request's `model` picks exactly one provider, and that provider's `api`
must match the path. Any other model gets 404 with `model_not_found`. A
request needs `Content-Length`; `Transfer-Encoding` or a missing length gets
411, a body over 32 MiB gets 413, a head over 64 KiB gets 431, and
`Expect: 100-continue` is answered. A query string such as `beta=true` is
passed on when it is at most 256 characters of letters, digits and `_=&.-`.

## Policy

The `model` policy is named by `spec.model.policy`, and the API resolves it
like the others ([03](03-api-design.md#policies)):

```json
{
  "providers": [
    {
      "name": "alpha",
      "api": "openai",
      "url": "https://api.example.com",
      "credential": "alpha-key",
      "models": ["alpha-mini"],
      "timeout_seconds": 600,
      "max_requests_per_minute": 60
    }
  ],
  "max_input_tokens": 2000000,
  "max_output_tokens": 200000
}
```

| Field | Rule |
|---|---|
| `name` | `^[a-z0-9][a-z0-9-]{0,31}$`, unique |
| `api` | `openai` or `anthropic` |
| `url` | the rules of [08](08-mcp-gate.md#external-servers); the request path is appended to it |
| `credential` | the name of a secret, required |
| `models` | 1 to 64 exact names; one model belongs to one provider |
| `timeout_seconds` | 1 to 900, default 600; it bounds the whole call, stream included |
| `max_requests_per_minute` | 1 to 600, default 60 |
| `max_input_tokens`, `max_output_tokens` | the budget of the whole Run, required |

Each provider gets its own network gate that allows https to its host and
nothing else ([06](06-network-gate.md)).

## Credentials

- The API issues each provider's `credential` with the Run's other
  credentials ([03](03-api-design.md#leases)). The secrets MCP never lists or
  returns them.
- The gateway forwards only `content-type`, `accept`, `anthropic-version` and
  `anthropic-beta`, so every auth header the agent sent is dropped. It sets
  `Authorization: Bearer` for openai and `x-api-key` for anthropic.
- A missing or expired credential refuses the call before anything leaves
  the host.
- Every byte of an answer passes a redactor that replaces the key with
  `<redacted>`, including a key split across two chunks.

## Budget and rate

Usage comes from the provider's answer: `usage` of a JSON answer, the final
chunk of an openai stream and `message_start` and `message_delta` of an
anthropic stream. Anthropic cache tokens count as input. The gateway sets
`stream_options.include_usage` on every openai stream, since without it the
stream reports no usage.

Each Run books its usage against its own budget, so one Run never spends
another's. A call is refused once the input or output budget is spent. A
call that starts under the budget runs to its end, so the budget can be
overshot by at most one call. The budget and the rate window live in the
runner's memory, and a restart of the runner starts them afresh.

## Failure behavior

Every refusal is in the error shape of the dialect of the path: openai
`{"error": {"message", "type", "code"}}`, anthropic
`{"type": "error", "error": {"type", "message"}}`.

| Outcome | Status |
|---|---|
| unknown path | 404 |
| model outside the policy, or on a provider of the other dialect | 404 |
| body that is not a JSON object, or without `model` | 400 |
| budget spent, rate reached | 429 |
| missing or expired credential | 502 |
| provider unreachable or refused by its network gate | 502 |
| provider slower than `timeout_seconds` | 504 |
| provider error status | passed on, redacted |
| stream that breaks midway | an `error` event in the dialect's stream shape, then the end of the body |

A failure is never retried and never sent to another provider.

## Audit

The gateway writes to the `audit` target ([11](11-observability.md)):

| Event | Fields |
|---|---|
| `model_policy_configured` | `run_id` |
| `model_attached` | `run_id` |
| `model_rejected` | `run_id`, `reason` |
| `model_call` | `run_id`, `provider`, `model`, `input_tokens`, `output_tokens`, `decision`, `duration_ms`, `category` |

`model_rejected` is a request the framing refused. `provider` is `naos` for
the model list and `unknown` before a model is routed. `model` is `none`
when the request named none, and `invalid` when the name is not a model name.
`decision` is `allow` or `deny`. `category` is `none`, `invalid`, `denied`,
`budget`, `rate`, `credential`, `timeout`, `provider` or `aborted`. Prompts,
completions and keys are never logged.

## Smoke build

The network gate refuses loopback, so `make smoke` builds the runner with the
cargo feature `smoke-stubs`. With it, the gateway resolves every provider
host to `127.0.0.1` and trusts the CA in `NAOS_AGENT_SMOKE_CA_FILE`. The
feature weakens the gate and is off in every other build.

## Acceptance

The tests in `packages/runner/src/libs/model/tests.rs` verify that:

- each dialect reaches its provider with the provider's key and no header of
  the agent's;
- an unknown path, a model outside the policy and a dialect mismatch never
  reach a provider;
- a spent budget and a reached rate are 429 in each dialect, and one Run
  never spends another's budget;
- a missing or expired credential never reaches the provider;
- a provider error is passed on once, a slow provider times out, and an
  echoed key is redacted, also across chunks;
- usage is read from both event streams;
- over the port, a request is answered, a close after it reopens the socket
  with the next request carried over, malformed requests are refused, and a
  close during a stream aborts it.

The API tests in `packages/api/tests/test_model.py`,
`test_runner_lifecycle.py`, `test_secrets.py` and `test_audit.py` cover the
policy, the credential issue and the audit schema.

The smoke test runs a stub provider of each dialect over https, calls both
through the gateway from the guest, and is refused a model outside the policy
and a call past the budget. It fails when a provider key shows up in a log,
the audit or the console log.

```bash
make test
make smoke
```
