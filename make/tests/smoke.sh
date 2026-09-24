#!/usr/bin/env bash
set -euo pipefail

api=http://127.0.0.1:8080
web=http://127.0.0.1:8000
version="$(sed -n 's/^  version: //p' "$ROOT/packer/.cz.yaml")"
repo="$(git -C "$ROOT" remote get-url origin | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')"
release="https://github.com/$repo/releases/download/image-$version"
image="naos-agents-$version.qcow2"

cleanup() {
    for group in "${agent:-}" "${server:-}" "${ui:-}"; do
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

enrolled() {
    curl -fsS "${auth[@]}" "$api/api/v1/runners" | "$VENV/bin/python" -c '
import json, sys
rows = json.load(sys.stdin)
sys.exit(0 if [(row["name"], row["status"]) for row in rows] == [("alpha", "live")] else 1)
'
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

# The read model the operator screens render, rather than the raw row.
check_run_view() {
    curl -fsS "${auth[@]}" "$api/api/v1/runs/$run" | "$VENV/bin/python" -c '
import json, sys
status, finished = sys.argv[1], sys.argv[2] == "finished"
row = json.load(sys.stdin)
seen = {"seq": row["seq"], "status": row["status"], "workspace": row["workspace"]}
wanted = {"seq": 1, "status": status, "workspace": "alpha"}
if seen != wanted:
    sys.exit(f"the run reads {seen}, expected {wanted}")
if (row["runner"] or {}).get("name") != "alpha":
    sys.exit(f"the run names the runner {row["runner"]}, expected alpha")
if row["started_at"] is None:
    sys.exit("the run carries no started_at")
if (row["finished_at"] is not None) != finished:
    sys.exit(f"finished_at is {row["finished_at"]} while the run is {sys.argv[2]}")
' "$1" "$2"
}

check_merge_summary() {
    curl -fsS "${auth[@]}" "$api/api/v1/runs/$run" | "$VENV/bin/python" -c '
import json, sys
merge = json.load(sys.stdin)["merge"]
if not merge or merge["changed"] < 1:
    sys.exit(f"the waiting run summarises its merge as {merge}")
'
}

listed() {
    curl -fsS "${auth[@]}" "$api/api/v1/runs?state=$1" | "$VENV/bin/python" -c '
import json, sys
sys.exit(0 if sys.argv[1] in [row["id"] for row in json.load(sys.stdin)] else 1)
' "$run"
}

check_states() {
    listed "$1" || fail "the run is missing from state=$1"
    for state in active queued waiting_merge failed; do
        if [ "$state" != "$1" ] && listed "$state"; then
            fail "the run shows up under state=$state as well as state=$1"
        fi
    done
}

check_summary() {
    curl -fsS "${auth[@]}" "$api/api/v1/runs/summary" | "$VENV/bin/python" -c '
import json, sys
status, opened = sys.argv[1], int(sys.argv[2])
body = json.load(sys.stdin)
if body["counts"].get(status) != 1:
    sys.exit(f"the summary counts {body["counts"]}, expected one {status}")
if body["open"] != opened:
    sys.exit(f"the summary says {body["open"]} open, expected {opened}")
' "$1" "$2"
}

# capacity outlives a lease, so it is stored rather than counted per request.
check_runner_slots() {
    curl -fsS "${auth[@]}" "$api/api/v1/runners" | "$VENV/bin/python" -c '
import json, sys
(row,) = json.load(sys.stdin)
if row["capacity"] is None:
    sys.exit("the runner reports no capacity")
held = [held["seq"] for held in row["runs"]]
if held != [1]:
    sys.exit(f"the runner holds {held}, expected the smoke run")
if row["lease_acquired_at"] is None or row["lease_expires_at"] is None:
    sys.exit("the live runner names no lease window")
'
}

page() {
    curl -fsS "$web/runs${1:+?state=$1}"
}

# The page is read as an operator reads it: the run number, the state it is in and
# the runner it landed on, rendered rather than fetched.
check_page() {
    page | "$VENV/bin/python" -c '
import sys
body = sys.stdin.read()
wanted = ["<div>#1</div>", f">{sys.argv[1]}</span>", ">alpha</span>", ">1 live</span>"]
missing = [text for text in wanted if text not in body]
if missing:
    sys.exit(f"the runs page is missing {missing}")
if "Bearer" in body or "Authorization" in body:
    sys.exit("the runs page carries the operator credential")
' "$1"
}

# Exactly one filter holds the run, and it is the one its state belongs to.
check_page_filters() {
    local found=""
    for state in active queued waiting_merge failed; do
        if page "$state" | grep -q "<div>#1</div>"; then found="$found $state"; fi
    done
    [ "$found" = " $1" ] || fail "the page lists the run under '\''$found'\'', expected $1"
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
    "run_created", "run_assigned", "vm_created", "workspace_shared", "network_allowed", "network_denied",
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

# The Logs & Audit tab renders every event the run carries, refuses none, and exports them all.
check_run_logs() {
    curl -fsS "${auth[@]}" "$api/api/v1/runs/$run/events?limit=10000" >"$TEMP_DIR/events.json"
    curl -fsS "$web/runs/$run/logs" >"$TEMP_DIR/logs.html"
    curl -fsS "$web/runs/$run/logs?kind=errors" >"$TEMP_DIR/errors.html"
    curl -fsS "$web/runs/$run/logs/export" >"$TEMP_DIR/export.json"
    "$VENV/bin/python" - "$TEMP_DIR" "$secret" "$operator" <<'PY' || fail "the logs tab is wrong"
import json, re, sys
from pathlib import Path

temp, secrets = Path(sys.argv[1]), sys.argv[2:]
logs = (temp / "logs.html").read_text()
errors = (temp / "errors.html").read_text()
exported = json.loads((temp / "export.json").read_text())
timeline = json.loads((temp / "events.json").read_text())

def events(body):
    table = body[body.index("<tbody>") : body.index("</tbody>")]
    return re.findall(r'class="logs__event">([^<]*)<', table)

# The runner may post one more event between the reads, so the timeline is a prefix.
if exported[: len(timeline)] != timeline:
    sys.exit("the export is not the timeline of the run")
shown = len(events(logs))
if shown < len(timeline) or f"{shown} entries" not in logs:
    sys.exit("the tab does not show every event of the run")
if "refused:" in logs:
    sys.exit("the tab refused an event the api wrote")
wanted = {"run_created", "run_assigned", "run_transition", "network_denied", "merge_conflict"}
if missing := wanted - set(events(logs)):
    sys.exit(f"the tab is missing {sorted(missing)}")
if "network_denied" not in events(errors) or "run_created" in events(errors):
    sys.exit("the errors filter does not keep only the errors")
for value in secrets:
    if value in logs or value in errors:
        sys.exit("the logs tab carries a credential")
PY
}

# The overlay opened as a plain page: the run, its bound secret counted and never shown.
check_run_detail() {
    curl -fsS "$web/runs/$run" | "$VENV/bin/python" -c '
import sys
body = sys.stdin.read()
wanted = ["Run #1</h2>", f">{sys.argv[1]}</span>", "1 bound", "Lease fencing", "Stop run</a>"]
missing = [text for text in wanted if text not in body]
if missing:
    sys.exit(f"the run overlay is missing {missing}")
for value in sys.argv[2:]:
    if value in body:
        sys.exit("the run overlay carries a credential")
' "$1" "$secret" "$operator" "Bearer"
}

# Both actions ask in a popup of their own before anything is posted.
check_confirms() {
    curl -fsS "$web/runs/$run/stop" | grep -qF "Are you sure you want to stop $run?" ||
        fail "the stop confirm does not ask"
    curl -fsS "$web/runs/$run/rerun" | grep -qF "A new run will be created with these options:" ||
        fail "the rerun confirm does not list what the new run gets"
}

# Stop and rerun go through the web as a browser without htmx posts them.
web_post() {
    local data=()
    [ -z "${3:-}" ] || data=(--data-urlencode "key=$3")
    curl -fsS -o /dev/null -w '%{redirect_url}' -X POST "$web/runs/$1/$2" "${data[@]}"
}

# The dialog is driven as a browser without htmx drives it: open it, take its key,
# post the last step. The same form posted twice must land on one Run.
new_run() {
    [ -n "${dialog_key:-}" ] ||
        dialog_key="$(curl -fsS "$web/runs/new" | sed -n 's/.*name="key" value="\([0-9a-f]*\)".*/\1/p')"
    [ -n "$dialog_key" ] || fail "the new run dialog carries no key"
    local location
    location="$(curl -fsS -o /dev/null -w '%{redirect_url}' "$web/runs/new" \
        --data-urlencode "key=$dialog_key" \
        --data-urlencode "profile=" \
        --data-urlencode "mode=new" \
        --data-urlencode "name=smoke" \
        --data-urlencode "cpu=2" \
        --data-urlencode "memory_mib=2048" \
        --data-urlencode "disk_gib=8" \
        --data-urlencode "timeout=3600" \
        --data-urlencode "merge=ask" \
        --data-urlencode "mounts=$mount_policy" \
        --data-urlencode "network=$network_policy" \
        --data-urlencode "shell=$shell_policy" \
        --data-urlencode "mcp=$mcp_policy" \
        --data-urlencode "image=auto" \
        --data-urlencode "runner=auto")"
    [ "$location" = "$web/runs" ] || fail "the new run dialog did not create the run"
}

only_run() {
    "$VENV/bin/python" -c '
import json, sys
runs = json.load(sys.stdin)
if len(runs) != 1 or runs[0]["status"] != "PENDING":
    sys.exit(f"the dialog should have created one PENDING run, got {runs}")
spec = runs[0]["spec"]
if spec["runtime"] != {"cpu": 2, "memory_mib": 2048, "disk_gib": 8} or spec["runner"] is not None:
    sys.exit(f"the run does not carry the spec of the dialog: {spec}")
print(runs[0]["id"])
'
}

only_profile() {
    "$VENV/bin/python" -c '
import json, sys
profiles = json.load(sys.stdin)
if len(profiles) != 1 or profiles[0]["active_runs"] != 1:
    sys.exit(f"the dialog should have saved one profile with its run, got {profiles}")
print(profiles[0]["id"])
'
}

# The api, not only the form, refuses to change a profile while its run is active.
check_profile_locked() {
    local code
    code="$(curl -sS "${auth[@]}" -o /dev/null -w '%{http_code}' -X PUT \
        "$api/api/v1/profiles/$profile" \
        -d '{"spec": {"runtime": {"cpu": 4, "memory_mib": 2048, "disk_gib": 8}, "timeout": 3600}}')"
    [ "$code" = 409 ] || fail "a profile with an active run was updated ($code)"
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
touch "$TEMP_DIR/api.log" "$TEMP_DIR/web.log" "$TEMP_DIR/agent.log" "$TEMP_DIR/console.log"
tail -f "$TEMP_DIR/api.log" "$TEMP_DIR/web.log" "$TEMP_DIR/agent.log" &
logs=$!

echo "starting api..."
NAOS_OPERATOR_TOKEN_SHA256="$(printf '%s' "$operator" | sha256sum | cut -d' ' -f1)" \
NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256="$(sha256sum "$TEMP_DIR/enrollment" | cut -d' ' -f1)" \
NAOS_DATABASE_URL="sqlite:///$TEMP_DIR/naos.db" \
NAOS_ALLOWED_MOUNT_ROOTS="[\"$TEMP_DIR/workspaces\"]" \
    setsid "$MAKE" -C "$ROOT" run-api >"$TEMP_DIR/api.log" 2>&1 &
server=$!
wait_for 30 curl -fs -o /dev/null "$api/healthz"

echo "starting web..."
printf '%s' "$operator" >"$TEMP_DIR/operator"
chmod 600 "$TEMP_DIR/operator"
NAOS_WEB_API_URL="$api" \
    NAOS_WEB_OPERATOR_TOKEN_FILE="$TEMP_DIR/operator" \
    setsid "$MAKE" -C "$ROOT" run-web >"$TEMP_DIR/web.log" 2>&1 &
ui=$!
wait_for 30 curl -fs -o /dev/null "$web/healthz"

# The guest's console output travels runner -> api -> web without a port on the runner.
console_shipped() {
    curl -fsS "${auth[@]}" "$api/api/v1/runs/$run/console" | grep -q NAOS-SMOKE-DONE
}

check_terminal() {
    curl -fsS "$web/runs/$run/terminal" | grep -qF "data-stream=\"/runs/$run/terminal/ws\"" ||
        fail "the terminal tab does not attach"
    curl -fsS "$web/runs/$run/terminal/log" | grep -q NAOS-SMOKE-DONE ||
        fail "the terminal log misses the console output"
    NAOS_TOKEN="$operator" "$VENV/bin/python" - "$api" "$run" <<'PY' || fail "the api attach did not stream the console"
import os
import sys

import anyio
import httpx
from httpx_ws import aconnect_ws


async def main() -> None:
    headers = {"Authorization": f"Bearer {os.environ['NAOS_TOKEN']}"}
    async with httpx.AsyncClient(headers=headers) as client:
        async with aconnect_ws(f"{sys.argv[1]}/api/v1/runs/{sys.argv[2]}/attach", client) as ws:
            seen = b""
            while b"NAOS-SMOKE-DONE" not in seen:
                seen += await ws.receive_bytes(timeout=10)


anyio.run(main)
PY
    curl -fsS "${auth[@]}" "$api/api/v1/runs/$run/events?limit=10000" | "$VENV/bin/python" -c '
import json, sys
rows = [row for row in json.load(sys.stdin) if row["event"] == "console_attached" and row["source"] == "api"]
if not rows or {row["actor"] for row in rows} != {"operator"}:
    sys.exit("the api attach left no operator audit entry")
'
}

# A key pressed in the browser reaches the guest: the page's socket claims the
# keyboard, types a command, and the guest's own echo comes back down it.
check_terminal_input() {
    "$VENV/bin/python" - "$web" "$run" <<'INPUT' || fail "the browser could not type into the run"
import json
import re
import sys

import anyio
import httpx
from httpx_ws import aconnect_ws

# The guest prints what the typed line does not hold, so its own echo never counts.
TYPED = b"echo NAOS-$((6*7))\r"
PRINTED = b"NAOS-42"
# tmux wraps and redraws in the middle of a word, so the screen is read as text
ESCAPES = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b.|[\x00-\x09\x0b-\x1f\x7f]")


def payload(frame: object) -> bytes:
    data = getattr(frame, "data", None)
    return data if isinstance(data, bytes) else b""


def said(frame: object) -> str:
    data = getattr(frame, "data", None)
    return data if isinstance(data, str) else ""


async def main() -> None:
    web, run = sys.argv[1], sys.argv[2]
    async with httpx.AsyncClient() as client:
        async with aconnect_ws(f"{web}/runs/{run}/terminal/ws", client) as ws:
            # the socket takes the keyboard the way a browser does when it opens
            driving = False
            with anyio.move_on_after(20):
                while not driving:
                    await ws.send_text(
                        json.dumps({"cols": 120, "rows": 30, "view": "window", "take": True})
                    )
                    with anyio.move_on_after(5):
                        while not (text := said(await ws.receive())):
                            pass
                        driving = bool(json.loads(text).get("driving"))
                    if not driving:
                        await anyio.sleep(1)
            if not driving:
                sys.exit("another terminal of the run holds the keyboard; close it while smoke runs")
            await ws.send_bytes(TYPED)
            seen = b""
            with anyio.fail_after(40):
                while PRINTED not in ESCAPES.sub(b"", seen):
                    seen += payload(await ws.receive(timeout=40))


anyio.run(main)
INPUT
    curl -fsS "${auth[@]}" "$api/api/v1/runs/$run/events?limit=10000" | "$VENV/bin/python" -c '
import json, sys
rows = [row for row in json.load(sys.stdin) if row["event"] == "console_typing"]
if not rows or {row["actor"] for row in rows} != {"operator"}:
    sys.exit("typing left no operator audit entry")
if any(set(row["data"]) - {"view"} for row in rows):
    sys.exit("the audit carries more than that the keyboard was taken")
'
}

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
echo "creating the run from the new run dialog..."
new_run
new_run
run="$(curl -fsS "${auth[@]}" "$api/api/v1/runs" | only_run)"
profile="$(curl -fsS "${auth[@]}" "$api/api/v1/profiles?q=smoke" | only_profile)"

echo "starting agent..."
start_agent
wait_for 60 enrolled

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
wait_for 60 enrolled
wait_for 120 reattached
if [ "$(run_status)" != STARTED ] || [ "$(events vm_created)" -ne 1 ]; then
    echo "the restarted agent did not keep the running vm" >&2
    exit 1
fi
echo "checking what the operator screens read..."
check_run_view STARTED running
check_states active
check_runner_slots
check_summary STARTED 1
check_page STARTED
check_page_filters active
check_profile_locked
check_run_detail STARTED
check_confirms
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
echo "reading the console through the api and the terminal tab..."
wait_for 30 console_shipped
check_terminal
check_terminal_input
echo "run $run booted, stopping it from the run overlay..."
[ "$(web_post "$run" stop)" = "$web/runs/$run" ] || fail "stop did not return to the run"
wait_for 120 collected
check_states waiting_merge
check_merge_summary
check_page WAITING_MERGE
check_page_filters waiting_merge
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
check_run_view COMPLETED finished
check_summary COMPLETED 0
check_merge
echo "checking the audit timeline of the run..."
wait_for 60 audited
check_secrets
echo "checking the logs & audit tab of the run..."
check_run_logs
echo "rerunning the run from its overlay, then stopping the copy..."
rerun_key="$(curl -fsS "$web/runs/$run/rerun" | sed -n 's/.*name="key" value="\([0-9a-f]*\)".*/\1/p')"
copy="$(web_post "$run" rerun "$rerun_key")"
[ "$(web_post "$run" rerun "$rerun_key")" = "$copy" ] || fail "one rerun key made two runs"
case "$copy" in "$web/runs/run_"*) ;; *) fail "rerun did not open a new run" ;; esac
web_post "${copy##*/}" stop >/dev/null
echo "smoke test passed"
