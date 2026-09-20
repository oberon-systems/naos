#!/usr/bin/env bash
set -euo pipefail

: "${ROOT:?}" "${LOCAL:?}" "${MAKE:=make}"

compose="$ROOT/docker"
env_file="$compose/.env"
runner="$ROOT/target/release/naos-runner"
python="$ROOT/.venv/bin/python"

fail() {
    echo "$*" >&2
    exit 1
}

token() {
    "$python" -c "import secrets; print(secrets.token_urlsafe(32), end='')"
}

digest() {
    sha256sum "$1" | cut -d' ' -f1
}

keep_token() {
    if [ ! -f "$LOCAL/$1" ]; then
        (
            umask 077
            token >"$LOCAL/$1"
        )
    fi
}

wait_for() {
    local seconds="$1"
    shift
    until "$@"; do
        seconds=$((seconds - 1))
        if [ "$seconds" -le 0 ]; then return 1; fi
        sleep 1
    done
}

check_hash() {
    grep -qx "$1=$(digest "$LOCAL/$2")" "$env_file" ||
        fail "$env_file holds another $1: put its token in $LOCAL/$2, or run make clean"
}

write_env() {
    if [ -f "$env_file" ]; then
        check_hash NAOS_OPERATOR_TOKEN_SHA256 operator
        check_hash NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256 enrollment
        return
    fi
    sed -e "s|^NAOS_TAG=.*|NAOS_TAG=local|" \
        -e "s|^NAOS_DB_PASSWORD=.*|NAOS_DB_PASSWORD=$(token)|" \
        -e "s|^NAOS_OPERATOR_TOKEN_SHA256=.*|NAOS_OPERATOR_TOKEN_SHA256=$(digest "$LOCAL/operator")|" \
        -e "s|^NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256=.*|NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256=$(digest "$LOCAL/enrollment")|" \
        "$env_file.example" >"$env_file"
    chmod 600 "$env_file"
    echo "wrote $env_file"
}

# The stack has one source of settings, so the urls here are the ones compose published.
settings() {
    [ -f "$env_file" ] || fail "no $env_file yet: run make kickstart"
    set -a
    # shellcheck disable=SC1090
    . "$env_file"
    set +a
    api="http://${NAOS_BIND:-127.0.0.1}:${NAOS_API_PORT:-8000}"
    web="http://${NAOS_BIND:-127.0.0.1}:${NAOS_WEB_PORT:-8001}"
    name="${NAOS_AGENT_NAME:-alpha}"
}

agent_pid() {
    [ -f "$LOCAL/agent.pid" ] || return 1
    local pid
    pid="$(cat "$LOCAL/agent.pid")"
    kill -0 "$pid" 2>/dev/null || return 1
    echo "$pid"
}

agent_env() {
    export NAOS_AGENT_API_URL="$api"
    export NAOS_AGENT_NAME="$name"
    export NAOS_AGENT_STATE_DIR="$LOCAL/agent"
    export NAOS_AGENT_ENROLLMENT_TOKEN_FILE="$LOCAL/enrollment"
    [ -x "$runner" ] || cargo build --release -p naos-runner --manifest-path "$ROOT/Cargo.toml"
}

kickstart() {
    [ -x "$python" ] || fail "no virtualenv in $ROOT: run make install there first"
    [ -d "$LOCAL" ] || mkdir -m 700 "$LOCAL"
    keep_token operator
    keep_token enrollment
    write_env
    settings
    "$MAKE" -C "$compose" images
    "$MAKE" -C "$compose" up
    wait_for 60 curl -fs -o /dev/null "$api/healthz" || fail "the api did not answer at $api/healthz"
    if pid="$(agent_pid)"; then
        echo "the runner is already up, pid $pid"
    else
        agent_env
        setsid "$runner" >>"$LOCAL/agent.log" 2>&1 &
        echo $! >"$LOCAL/agent.pid"
        wait_for 30 test -f "$LOCAL/agent/credentials.json" ||
            fail "the runner did not enroll, see $LOCAL/agent.log"
    fi
    cat <<REPORT

api   $api
web   $web
agent $name, log in $LOCAL/agent.log

curl -fsS -H "Authorization: Bearer \$(cat $LOCAL/operator)" $api/api/v1/audit
REPORT
}

foreground() {
    settings
    agent_env
    exec "$runner"
}

down() {
    if pid="$(agent_pid)"; then
        kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    fi
    rm -f "$LOCAL/agent.pid"
    if [ -f "$env_file" ]; then "$MAKE" -C "$compose" down; fi
}

case "${1:-kickstart}" in
kickstart) kickstart ;;
runner) foreground ;;
down) down ;;
*) fail "usage: kickstart.sh [kickstart|runner|down]" ;;
esac
