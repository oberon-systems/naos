# Prompt 06d — MCP Rules

Read `AGENTS.md`, `docs/08-mcp-gate.md`, `docs/01-security-model.md` and
`06c-mcp-registry.md`.

Today an `mcp` policy only lists the tools it grants. Give it rules the
broker enforces on every action, built-in servers included:

- a rule matches `server`, `tool` (exact or `*`) and optional argument
  constraints: equality, a prefix, a regular expression or a JSON schema
  fragment per argument;
- `effect` is `allow` or `deny`; a matching `deny` wins over any `allow`,
  and an action no rule allows is denied;
- a rule may set its own `max_calls_per_minute` and `max_calls` for the Run;
- `resources` keeps its URI prefixes, with the same allow and deny.

The broker checks the rules before a call leaves the host, and `tools/list`
shows only what some rule could allow. A denied call is a tool error with the
rule that denied it, and `mcp_call` carries the rule index; arguments stay
out of the audit as `docs/08-mcp-gate.md` requires.

The API validates rules at `POST /policies`: an unknown server, an unknown
argument of a built-in tool, an invalid pattern or a rule that can never
match is 422. Equivalent documents keep one digest.

Update `docs/08-mcp-gate.md` and `docs/03-api-design.md`.

Acceptance: allow and deny rules, argument constraints and per-rule budgets
hold for built-in and external servers; deny wins; an action no rule names is
denied; the broker tests cover each and `make smoke` sends one allowed and
one denied call per rule kind.
