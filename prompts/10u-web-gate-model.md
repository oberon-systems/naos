# Prompt 10u — Model Gate Events

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`,
`docs/13-model-gateway.md`, `docs/11-observability.md`,
`06h-model-gateway.md`, `10p-web-models.md`, `10r-web-gate-network.md` and
the boards on page `Runs` in `dev/web/templates/base`.

The `Gates` tab of `10r-web-gate-network.md` has no model view yet.
`Run detail — Overview · Model` of `10p-web-models.md` shows the budget and
the last refusals; this view lists every call. Draw it first and build
nothing until it is approved:

- `Run detail — Gates · Model`:
  - the providers and models the policy allows;
  - input and output tokens spent against the budget, and requests against
    the rate, over the Run's time;
  - the calls grouped by provider and model, with count, tokens, denials and
    the slowest duration;
  - the table TIME, DECISION, PROVIDER, MODEL, INPUT, OUTPUT, DURATION,
    CATEGORY with the filters All / Allowed / Denied.

A row opens its event in the audit popup of `10j-web-audit.md`. A provider
credential, the policy and the Run open on click.

The view reads `GET /audit` with `run_id` and the event `model_call`, and
extends `GET /runs/{run_id}/gates/{gate}` with the `model` grouping.

Prompts, completions and keys are never logged, and the view never asks for
them. A credential shows by name only.

The UI is never an authorization boundary. Every action goes through API
authorization. Never display secrets.

Acceptance: an operator sees every model call of a Run, its tokens against
the budget and why a call was refused; `make smoke` opens the model view of a
Run that called the stub provider.
