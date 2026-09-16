#!/usr/bin/env bash
set -euo pipefail

api=http://127.0.0.1:8000
version="$(sed -n 's/^  version: //p' "$ROOT/packer/.cz.yaml")"
repo="$(git -C "$ROOT" remote get-url origin | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')"
release="https://github.com/$repo/releases/download/image-$version"
image="naos-agents-$version.qcow2"

cleanup() {
    for group in "${agent:-}" "${server:-}"; do
        if [ -n "$group" ]; then kill -- "-$group" 2>/dev/null || true; fi
    done
    if [ -n "${logs:-}" ]; then kill "$logs" 2>/dev/null || true; fi
    pkill -f -- "$TEMP_DIR/runs" 2>/dev/null || true
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

wait_for() {
    local seconds="$1"
    shift
    until "$@"; do
        seconds=$((seconds - 1))
        if [ "$seconds" -le 0 ]; then
            echo "timed out waiting for $*" >&2
            exit 1
        fi
        sleep 1
    done
}

token() {
    "$VENV/bin/python" -c "import secrets; print(secrets.token_urlsafe(32), end='')"
}

field() {
    "$VENV/bin/python" -c "import json, sys; print(json.load(sys.stdin)['$1'])"
}

task_status() {
    curl -fsS "${auth[@]}" "$api/api/v1/tasks/$task" | field status
}

booted() {
    local status
    status="$(task_status)"
    if [ "$status" = FAILED ] || grep -qs 'naos-probe fail' "$TEMP_DIR"/runs/*/boot.log; then
        echo "task $task failed to boot" >&2
        exit 1
    fi
    grep -qs naos-ready "$TEMP_DIR"/runs/*/boot.log && grep -qs 'naos-probe ok' "$TEMP_DIR"/runs/*/boot.log
}

collected() {
    [ "$(task_status)" = COLLECTING ]
}

mkdir -m 700 "$TEMP_DIR" "$TEMP_DIR/state"
echo "smoke test of $release/$image, working dir: $TEMP_DIR"

digest="$(curl -fsSL "$release/SHA256SUMS" | awk -v name="$image" '$2 == name { print "sha256:" $1 }')"
if [ -z "$digest" ]; then
    echo "$image is not listed in $release/SHA256SUMS" >&2
    exit 1
fi

operator="$(token)"
token >"$TEMP_DIR/enrollment"
chmod 600 "$TEMP_DIR/enrollment"
auth=(-H "Authorization: Bearer $operator" -H "Content-Type: application/json")
touch "$TEMP_DIR/api.log" "$TEMP_DIR/agent.log"
tail -f "$TEMP_DIR/api.log" "$TEMP_DIR/agent.log" &
logs=$!

echo "starting api..."
NAOS_OPERATOR_TOKEN_SHA256="$(printf '%s' "$operator" | sha256sum | cut -d' ' -f1)" \
NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256="$(sha256sum "$TEMP_DIR/enrollment" | cut -d' ' -f1)" \
NAOS_DATABASE_URL="sqlite:///$TEMP_DIR/naos.db" \
    setsid "$MAKE" -C "$ROOT" run-api >"$TEMP_DIR/api.log" 2>&1 &
server=$!
wait_for 30 curl -fsS -o /dev/null "$api/healthz"

echo "registering the image and creating a task..."
curl -fsS "${auth[@]}" "$api/api/v1/images" -o /dev/null -d @- <<EOF
{"id": "naos-agents", "version": "$version", "digest": "$digest", "url": "$release/$image"}
EOF
network_policy="$(
    curl -fsS "${auth[@]}" "$api/api/v1/policies" -d @- <<EOF | field id
{"kind": "network", "document": {"allow": [{"protocol": "https", "host": "example.com"}]}}
EOF
)"
task="$(
    curl -fsS "${auth[@]}" -H "Idempotency-Key: smoke" "$api/api/v1/tasks" -d @- <<EOF | field id
{"image": {"id": "naos-agents", "digest": "$digest"}, "runtime": {"cpu": 2, "memory_mib": 2048, "disk_gib": 8}, "network": {"policy": "$network_policy"}, "timeout": 3600}
EOF
)"

echo "starting agent..."
NAOS_AGENT_API_URL="$api" \
    NAOS_AGENT_NAME=alpha \
    NAOS_AGENT_STATE_DIR="$TEMP_DIR/state" \
    NAOS_AGENT_ENROLLMENT_TOKEN_FILE="$TEMP_DIR/enrollment" \
    NAOS_AGENT_IMAGE_DIR="$TEMP_DIR/vms" \
    NAOS_AGENT_VM_DIR="$TEMP_DIR/runs" \
    setsid cargo run -q -p naos-agent >"$TEMP_DIR/agent.log" 2>&1 &
agent=$!

wait_for 900 booted
grep -qs 'event":"network_policy_configured"' "$TEMP_DIR/agent.log" || {
    echo "network policy was not configured" >&2
    exit 1
}
echo "task $task booted, stopping it..."
curl -fsS "${auth[@]}" -X POST "$api/api/v1/tasks/$task/stop" -o /dev/null
wait_for 120 collected
echo "smoke test passed"
