# Prompt 10p — Models

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`,
`06h-model-gateway.md`, `10k-web-policies.md`, `10c-web-new-run.md` and the
boards in `dev/web/templates/base`.

No board shows the model gateway yet. Draw these first and build nothing
until they are approved:

- `Policies — model`: the `model` kind in the policies list, its popup and
  its form: providers with api, url, credential name, models, limits and
  token budgets;
- `New Run` with the model policy next to the other policies;
- `Run detail — Overview · Model`: the providers and models the Run may use,
  requests and tokens spent against the budget, and the last refusals.

Every provider credential, policy and Run mentioned on these screens opens it
on click; a credential shows by name only.

The UI is never an authorization boundary. Every action goes through API
authorization. Never display secrets.

Acceptance: every shipped screen matches an approved board; an operator
creates a model policy, starts a Run with it and follows its token budget;
no page carries a provider key.
