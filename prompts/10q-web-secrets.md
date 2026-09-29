# Prompt 10q — Secrets

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/03-api-design.md`,
`docs/01-security-model.md`, `06c-mcp-registry.md`, `06e-mcp-secrets.md`,
`06h-model-gateway.md`, `10a-web-shell.md` and the boards in
`dev/web/templates/base`.

No board shows secrets yet. Draw these first and build nothing until they
are approved:

- `Secrets — List`: name, expiry and its state (valid, expiring, expired),
  what uses the secret (registry servers, model policies, `mcp` policies that
  grant it to agents) and the active Runs holding it; search and filters;
- `Secrets — Detail`: a popup with the metadata, the usage and the secret's
  events (created, rotated, expiry changed, issued to a Run, read by an
  agent);
- `Secrets — New`, `Secrets — Rotate` and `Secrets — Expiry`: dialogs;
- `Confirm — Delete secret`.

The API has `POST /secrets` and `GET /secrets/{name}` only. Add what the
boards need, never an endpoint that returns a value:

- `GET /secrets` with `q`, `state` and `used`, each row with its usage;
- `POST /secrets/{name}/rotate` takes a new value, keeps the name, and the
  next issue to a runner carries it;
- `PATCH /secrets/{name}` changes `expires_at` only;
- `DELETE /secrets/{name}` answers 409 while a policy, a registry server or
  an active Run names it;
- every write is audited with actor `operator` and never carries the value.

A value is typed once into a password field, posted, and never rendered back:
not in the page, a redirect, an error message or a log. A form that failed
comes back empty. The page never holds a value in a query string.

The value field carries an eye switch: a toggle that stays on until it is
switched off, not a press-and-hold. On, the field shows what was typed; off,
it is masked again. It starts off in every dialog, and it only changes the
field in the browser: nothing about it is sent, stored or rendered by the
server.

Every secret, policy, registry server and Run on these screens opens it on
click.

The UI is never an authorization boundary. Every action goes through API
authorization. Dangerous actions require confirmation. Never display secrets.

Acceptance: an operator creates, rotates, re-dates and deletes a secret and
sees who uses it and which Runs read it; a delete of a secret in use is
refused; no response of the web or the API carries a value; `make smoke`
creates and rotates a secret through the web.
