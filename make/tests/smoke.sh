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

run_status() {
    curl -fsS "${auth[@]}" "$api/api/v1/runs/$run" | field status
}

booted() {
    local status
    status="$(run_status)"
    if [ "$status" = FAILED ] || grep -qs 'naos-probe fail' "$TEMP_DIR"/runs/*/boot.log; then
        echo "run $run failed to boot" >&2
        exit 1
    fi
    grep -qs naos-ready "$TEMP_DIR"/runs/*/boot.log && grep -qs 'naos-probe ok' "$TEMP_DIR"/runs/*/boot.log
}

collected() {
    local status
    status="$(run_status)"
    if [ "$status" = FAILED ]; then
        grep -e '"event":"run_failed"' -e 'reconcile action failed' "$TEMP_DIR/agent.log" >&2 || true
        fail "run $run failed while its workspace was collected"
    fi
    [ "$status" = WAITING_MERGE ] && [ "$(events workspace_collected)" -ge 1 ]
}

merge_state() {
    curl -fsS "${auth[@]}" "$api/api/v1/runs/$run/merge"
}

# Selects every mergeable path of the collected diff, with the given resolutions.
decide() {
    merge_state | "$VENV/bin/python" -c '
import json, sys
entries = json.load(sys.stdin)["entries"]
paths = sorted({entry["path"] for entry in entries if entry["change"] != "rejected"})
print(json.dumps({"paths": paths, "resolutions": json.loads(sys.argv[1])}))
' "$1" | curl -fsS "${auth[@]}" "$api/api/v1/runs/$run/merge" -o /dev/null -d @-
}

conflicted() {
    [ "$(events merge_conflict)" -ge 1 ] && merge_state | "$VENV/bin/python" -c '
import json, sys
state = json.load(sys.stdin)
paths = [conflict["path"] for conflict in state["conflicts"] or []]
sys.exit(0 if state["decision"] is None and paths == ["notes.txt"] else 1)
'
}

completed() {
    local status
    status="$(run_status)"
    if [ "$status" = FAILED ]; then
        grep -e '"event":"run_failed"' -e 'reconcile action failed' "$TEMP_DIR/agent.log" >&2 || true
        fail "run $run failed while it merged"
    fi
    [ "$status" = COMPLETED ] && [ "$(events changes_archived)" -ge 1 ]
}

events() {
    grep -c "event\":\"$1\"" "$TEMP_DIR/agent.log" || true
}

first_line() {
    grep -n -m1 "event\":\"$1\"" "$TEMP_DIR/agent.log" | cut -d: -f1
}

agent_gone() {
    ! kill -0 -- "-$agent" 2>/dev/null
}

reattached() {
    [ "$(events mcp_attached)" -ge 2 ] && [ "$(events mcp_credentials_updated)" -ge 2 ]
}

workspace_tree() {
    (
        cd "$TEMP_DIR/workspaces/alpha"
        find . -type f -print0 | sort -z | xargs -0 sha256sum
        find . | sort
    ) | sha256sum
}

fail() {
    echo "$1" >&2
    echo "--- last lines of the guest console ---" >&2
    tail -40 "$TEMP_DIR/console.log" >&2
    exit 1
}

# A second tmux window gets a shell next to the agent, and the lines are typed into it.
guest() {
    {
        sleep 2
        printf '\002c'
        sleep 3
        for line in "$@"; do
            printf '%s\r' "$line"
            sleep 1
        done
        sleep 25
    } | NAOS_AGENT_IMAGE_DIR="$TEMP_DIR/vms" NAOS_AGENT_VM_DIR="$TEMP_DIR/runs" \
        cargo run -q -p naos-runner -- console "$run" >>"$TEMP_DIR/console.log" 2>&1
}

# Every gate call the guest makes is one audit line of the runner, which took the decision.
mcp_calls() {
    grep '"event":"mcp_call"' "$TEMP_DIR/agent.log" |
        grep -c "\"server\":\"$1\",\"tool\":\"$2\",.*\"decision\":\"$3\"" || true
}

check_gates() {
    grep -q NAOS-SMOKE-DONE "$TEMP_DIR/console.log" || fail "the guest commands did not finish"
    for tool in read_file list_dir grep http_request; do
        grep -q "\"name\":\"$tool\"" "$TEMP_DIR/console.log" ||
            fail "$tool is missing from the tools/list reply"
    done
    # The shell gate serves the host workspace, so it still reads the text the guest overwrote.
    grep -q '"text":"alpha' "$TEMP_DIR/console.log" ||
        fail "the shell gate did not read the host workspace"
    grep -q 'duplicate request id' "$TEMP_DIR/console.log" ||
        fail "a reused request id was accepted"
    for call in "naos read_file allow" "naos http_request allow" "naos read_file deny" \
        "naos git_status deny" "naos http_request deny" "alpha search deny"; do
        # shellcheck disable=SC2086  # the three fields are one argument each
        [ "$(mcp_calls $call)" -ge 1 ] || fail "no mcp_call for: $call"
    done
    for event in shell_allowed shell_denied network_allowed network_denied; do
        [ "$(events "$event")" -ge 1 ] || fail "$event is missing from the agent log"
    done
}

check_diff() {
    local diff
    diff="$(echo "$TEMP_DIR"/runs/*/diff.json)"
    [ "$(stat -c %a "$diff")" = 600 ] || fail "$diff is not 0600"
    [ -e "$(dirname "$diff")/upper.img" ] || fail "the upper disk is gone after collection"
    "$VENV/bin/python" - "$diff" <<'PY' || fail "the workspace diff is wrong"
import json
import sys

entries = json.load(open(sys.argv[1]))["entries"]
found = {(entry["path"], entry["change"], entry.get("from")) for entry in entries}
expected = {
    ("added.txt", "created", None),
    ("notes.txt", "modified", None),
    ("old.txt", "deleted", None),
    ("renamed.txt", "renamed", "moved.txt"),
    ("link", "rejected", None),
    ("rel", "created", None),
    ("pipe", "rejected", None),
    ("dir/gone.txt", "deleted", None),
    ("dir/keep.txt", "deleted", None),
    ("dir/new.txt", "created", None),
}
missing = sorted(expected - found)
probes = sorted(path for path, _, _ in found if path.startswith(".naos-probe"))
if missing or probes:
    sys.exit(f"missing {missing}, probe leftovers {probes}, in {entries}")
PY
}

check_merge() {
    local workspace="$TEMP_DIR/workspaces/alpha" kept
    kept="$(echo "$TEMP_DIR"/runs/archive/*/merge)"
    [ -e "$(dirname "$kept")/upper.img" ] || fail "the archive lost the upper disk"
    [ "$(cat "$workspace/notes.txt")" = beta ] || fail "notes.txt did not take the agent's version"
    [ "$(cat "$workspace/added.txt")" = gamma ] || fail "added.txt was not merged"
    [ "$(cat "$workspace/renamed.txt")" = "moved content" ] || fail "renamed.txt was not merged"
    [ "$(cat "$workspace/dir/new.txt")" = new ] || fail "dir/new.txt was not merged"
    [ "$(readlink "$workspace/rel")" = notes.txt ] || fail "the relative symlink was not merged"
    for gone in old.txt moved.txt dir/keep.txt dir/gone.txt link pipe; do
        if [ -e "$workspace/$gone" ] || [ -L "$workspace/$gone" ]; then
            fail "$gone should not be in the workspace"
        fi
    done
    [ "$(cat "$kept/backup/notes.txt")" = local ] || fail "the host edit of notes.txt was not kept"
    [ "$(cat "$kept/backup/old.txt")" = old ] || fail "the deleted old.txt was not kept"
    [ "$(cat "$kept/backup/dir/keep.txt")" = keep ] || fail "the deleted dir/keep.txt was not kept"
    if compgen -G "$workspace/.naos-merge-*" >/dev/null; then
        fail "the merge left its staging directory in the workspace"
    fi
}

# The runner posts its events after each cycle, so the timeline fills in shortly after the log.
audited() {
    curl -fsS "${auth[@]}" "$api/api/v1/runs/$run/events?limit=10000" |
        "$VENV/bin/python" -c '
import json, sys
rows = json.load(sys.stdin)
seen = {row["event"] for row in rows}
seen |= {("to", row["data"]["to"]) for row in rows if row["event"] == "run_transition"}
seen |= {("mcp_call", row["data"]["decision"]) for row in rows if row["event"] == "mcp_call"}
statuses = ("STARTING", "STARTED", "STOPPING", "COLLECTING", "WAITING_MERGE", "COMPLETED")
expected = {
    "run_created", "vm_created", "workspace_shared", "network_allowed", "network_denied",
    "shell_allowed", "shell_denied", ("mcp_call", "allow"), ("mcp_call", "deny"),
    "workspace_collected", "diff_reported", "merge_decided", "merge_conflict", "merge_applied",
    "changes_archived", *(("to", status) for status in statuses),
}
sys.exit(0 if expected <= seen else f"not in the timeline yet: {sorted(map(str, expected - seen))}")
'
}

check_secrets() {
    local after=0 page spool="$TEMP_DIR/state/audit.jsonl"
    : >"$TEMP_DIR/audit.json"
    while page="$(curl -fsS "${auth[@]}" "$api/api/v1/audit?limit=1000&after=$after")" &&
        [ "$page" != "[]" ]; do
        printf '%s\n' "$page" >>"$TEMP_DIR/audit.json"
        after="$(printf '%s' "$page" | "$VENV/bin/python" -c 'import json, sys; print(json.load(sys.stdin)[-1]["seq"])')"
    done
    [ "$(stat -c %a "$spool")" = 600 ] || fail "$spool is not 0600"
    for value in "$operator" "$(cat "$TEMP_DIR/enrollment")" "$secret" \
        "$(field token <"$TEMP_DIR/state/credentials.json")"; do
        if grep -qF -- "$value" "$TEMP_DIR/audit.json" "$spool"; then
            fail "a credential reached the audit trail"
        fi
    done
}

share_gone() {
    ! pgrep -f -- "--socket-path=$TEMP_DIR/runs" >/dev/null
}

start_agent() {
    NAOS_AGENT_API_URL="$api" \
        NAOS_AGENT_NAME=alpha \
        NAOS_AGENT_STATE_DIR="$TEMP_DIR/state" \
        NAOS_AGENT_ENROLLMENT_TOKEN_FILE="$TEMP_DIR/enrollment" \
        NAOS_AGENT_IMAGE_DIR="$TEMP_DIR/vms" \
        NAOS_AGENT_VM_DIR="$TEMP_DIR/runs" \
        setsid cargo run -q -p naos-runner >>"$TEMP_DIR/agent.log" 2>&1 &
    agent=$!
}

mkdir -m 700 "$TEMP_DIR" "$TEMP_DIR/state"
mkdir -p "$TEMP_DIR/workspaces/alpha"
printf 'alpha\n' >"$TEMP_DIR/workspaces/alpha/notes.txt"
printf 'old\n' >"$TEMP_DIR/workspaces/alpha/old.txt"
printf 'moved content\n' >"$TEMP_DIR/workspaces/alpha/moved.txt"
mkdir "$TEMP_DIR/workspaces/alpha/dir"
printf 'keep\n' >"$TEMP_DIR/workspaces/alpha/dir/keep.txt"
printf 'gone\n' >"$TEMP_DIR/workspaces/alpha/dir/gone.txt"
tree_before="$(workspace_tree)"
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
touch "$TEMP_DIR/api.log" "$TEMP_DIR/agent.log" "$TEMP_DIR/console.log"
tail -f "$TEMP_DIR/api.log" "$TEMP_DIR/agent.log" &
logs=$!

echo "starting api..."
NAOS_OPERATOR_TOKEN_SHA256="$(printf '%s' "$operator" | sha256sum | cut -d' ' -f1)" \
NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256="$(sha256sum "$TEMP_DIR/enrollment" | cut -d' ' -f1)" \
NAOS_DATABASE_URL="sqlite:///$TEMP_DIR/naos.db" \
NAOS_ALLOWED_MOUNT_ROOTS="[\"$TEMP_DIR/workspaces\"]" \
    setsid "$MAKE" -C "$ROOT" run-api >"$TEMP_DIR/api.log" 2>&1 &
server=$!
wait_for 30 curl -fs -o /dev/null "$api/healthz"

echo "registering the image and creating a run..."
curl -fsS "${auth[@]}" "$api/api/v1/images" -o /dev/null -d @- <<EOF
{"id": "naos-agents", "version": "$version", "digest": "$digest", "url": "$release/$image"}
EOF
network_policy="$(
    curl -fsS "${auth[@]}" "$api/api/v1/policies" -d @- <<EOF | field id
{"kind": "network", "document": {"allow": [{"protocol": "https", "host": "www.google.com"}]}}
EOF
)"
shell_policy="$(
    curl -fsS "${auth[@]}" "$api/api/v1/policies" -d @- <<EOF | field id
{"kind": "shell", "document": {"allow": ["read_file", "list_dir", "grep"]}}
EOF
)"
mount_policy="$(
    curl -fsS "${auth[@]}" "$api/api/v1/policies" -d @- <<EOF | field id
{"kind": "mount", "document": {"workspace": {"host_path": "$TEMP_DIR/workspaces/alpha", "mode": "rw"}}}
EOF
)"
secret="$(token)"
curl -fsS "${auth[@]}" "$api/api/v1/secrets" -o /dev/null -d @- <<EOF
{"name": "alpha-token", "value": "$secret"}
EOF
mcp_policy="$(
    curl -fsS "${auth[@]}" "$api/api/v1/policies" -d @- <<EOF | field id
{"kind": "mcp", "document": {"servers": [{"name": "alpha", "url": "https://example.com/mcp", "tools": ["search"], "resources": [], "credential": "alpha-token"}]}}
EOF
)"
run="$(
    curl -fsS "${auth[@]}" -H "Idempotency-Key: smoke" "$api/api/v1/runs" -d @- <<EOF | field id
{"image": {"id": "naos-agents", "digest": "$digest"}, "runtime": {"cpu": 2, "memory_mib": 2048, "disk_gib": 8}, "mounts": {"policy": "$mount_policy"}, "network": {"policy": "$network_policy"}, "shell": {"policy": "$shell_policy"}, "mcp": {"policy": "$mcp_policy"}, "timeout": 3600}
EOF
)"

echo "starting agent..."
start_agent

wait_for 900 booted
for event in network_policy_configured shell_policy_configured mcp_policy_configured mcp_attached workspace_shared; do
    [ "$(events "$event")" -ge 1 ] || {
        echo "$event is missing from the agent log" >&2
        exit 1
    }
done
credentials="$(first_line mcp_credentials_updated)"
if [ -z "$credentials" ] || [ "$credentials" -gt "$(first_line vm_created)" ]; then
    echo "the vm started before its mcp gate held the credentials" >&2
    exit 1
fi

if [ "$(workspace_tree)" != "$tree_before" ]; then
    echo "the guest changed the host workspace" >&2
    exit 1
fi

echo "restarting agent with the vm running..."
kill -- "-$agent"
wait_for 30 agent_gone
start_agent
wait_for 120 reattached
if [ "$(run_status)" != STARTED ] || [ "$(events vm_created)" -ne 1 ]; then
    echo "the restarted agent did not keep the running vm" >&2
    exit 1
fi
echo "editing the workspace and calling the gates from the console..."
guest \
    "cd /naos/alpha" \
    "printf 'beta\n' > notes.txt" \
    "printf 'gamma\n' > added.txt" \
    "rm old.txt" \
    "mv moved.txt renamed.txt" \
    "ln -s /etc/passwd link" \
    "ln -s notes.txt rel" \
    "mkfifo pipe" \
    "rm -rf dir && mkdir dir && printf 'new\n' > dir/new.txt" \
    "cat > /tmp/rpc <<'JSON'" \
    '{"jsonrpc":"2.0","id":11,"method":"tools/list"}' \
    '{"jsonrpc":"2.0","id":12,"method":"tools/call","params":{"name":"read_file","arguments":{"path":"/naos/alpha/notes.txt"}}}' \
    '{"jsonrpc":"2.0","id":13,"method":"tools/call","params":{"name":"read_file","arguments":{"path":"/etc/passwd"}}}' \
    '{"jsonrpc":"2.0","id":14,"method":"tools/call","params":{"name":"git_status","arguments":{"path":"/naos/alpha"}}}' \
    '{"jsonrpc":"2.0","id":15,"method":"tools/call","params":{"name":"http_request","arguments":{"method":"GET","url":"https://www.google.com/robots.txt"}}}' \
    '{"jsonrpc":"2.0","id":16,"method":"tools/call","params":{"name":"http_request","arguments":{"method":"GET","url":"https://www.wikipedia.org/"}}}' \
    '{"jsonrpc":"2.0","id":17,"method":"tools/call","params":{"name":"alpha__search","arguments":{"query":"naos"}}}' \
    '{"jsonrpc":"2.0","id":11,"method":"ping"}' \
    "JSON" \
    "{ cat /tmp/rpc; sleep 20; } | naos-mcp" \
    "echo NAOS-SMOKE-DONE"
check_gates
echo "run $run booted, stopping it..."
curl -fsS "${auth[@]}" -X POST "$api/api/v1/runs/$run/stop" -o /dev/null
wait_for 120 collected
wait_for 10 share_gone
if [ "$(workspace_tree)" != "$tree_before" ]; then
    echo "the guest changed the host workspace" >&2
    exit 1
fi
check_diff
echo "editing the host workspace and merging, which conflicts..."
printf 'local\n' >"$TEMP_DIR/workspaces/alpha/notes.txt"
decide '{}'
wait_for 60 conflicted
[ ! -e "$TEMP_DIR/workspaces/alpha/added.txt" ] || fail "a conflicted merge wrote to the workspace"
echo "taking the agent's version of notes.txt..."
decide '{"notes.txt": "take"}'
wait_for 120 completed
check_merge
echo "checking the audit timeline of the run..."
wait_for 60 audited
check_secrets
echo "smoke test passed"
