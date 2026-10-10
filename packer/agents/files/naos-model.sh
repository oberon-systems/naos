# shellcheck shell=sh
# Agents reach their model through the gateway of this Run. The keys below are
# placeholders: the runner drops them and sets the provider's own.

export OPENAI_BASE_URL=http://127.0.0.1:4000/v1
export ANTHROPIC_BASE_URL=http://127.0.0.1:4000
export OPENAI_API_KEY=naos-placeholder
export ANTHROPIC_API_KEY=naos-placeholder

# Goose reads one provider from its own variables; naos-session names it and its model.
export GOOSE_PROVIDER__HOST=http://127.0.0.1:4000
export GOOSE_PROVIDER__API_KEY=naos-placeholder
export GOOSE_DISABLE_KEYRING=1
# shellcheck disable=SC1091  # written at boot by naos-session
[ -r /run/naos/agent.env ] && . /run/naos/agent.env
