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
    if [ -n "${stub:-}" ]; then kill "$stub" 2>/dev/null || true; fi
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

# Selects every mergeable path with the given resolutions, the way the operator does: through
# the web's confirm, then posting the form it renders.
decide() {
    merge_state | "$VENV/bin/python" -c '
import html, json, re, sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen
web, run = sys.argv[2], sys.argv[3]
entries = json.load(sys.stdin)["entries"]
paths = sorted({entry["path"] for entry in entries if entry["change"] != "rejected"})
chosen = [(kind, path) for path, kind in json.loads(sys.argv[1]).items()]
query = urlencode([("touched", "1")] + [("path", path) for path in paths] + chosen)
page = urlopen(f"{web}/runs/{run}/merge?{query}").read().decode()
if "the audit keeps who decided" not in page:
    sys.exit("the merge confirm is missing")
fields = re.findall(r"<input type=\"hidden\" name=\"([^\"]+)\" value=\"([^\"]*)\">", page)
body = urlencode([(name, html.unescape(value)) for name, value in fields]).encode()
answer = urlopen(Request(f"{web}/runs/{run}/merge", data=body)).read().decode()
if "role=\"alert\"" in answer:
    sys.exit("the web refused the merge decision")
' "$1" "$web" "$run"
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
    # The console carries terminal escapes that would repaint the reason printed above.
    tail -40 "$TEMP_DIR/console.log" | sed 's/\x1b\[[0-9;?]*[A-Za-z]//g' | tr -d '\r\033' >&2
    echo "smoke failed: $1" >&2
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
        cargo run -q -p naos-runner --features smoke-stubs -- console "$run" >>"${console_log:-$TEMP_DIR/console.log}" 2>&1
}

# Every gate call the guest makes is one audit line of the runner, which took the decision.
mcp_calls() {
    grep '"event":"mcp_call"' "$TEMP_DIR/agent.log" |
        grep -c "\"server\":\"$1\",\"tool\":\"$2\",.*\"decision\":\"$3\"" || true
}

model_calls() {
    grep '"event":"model_call"' "$TEMP_DIR/agent.log" |
        grep -c "\"provider\":\"$1\",.*\"decision\":\"$2\".*\"category\":\"$3\"" || true
}

# Both dialects answer through the gateway, a foreign model and a spent budget do not, and no key
# the runner set ever reaches the guest, a log or the audit.
check_models() {
    grep -q 'MODEL-ENV http://127.0.0.1:4000/v1 http://127.0.0.1:4000' "$TEMP_DIR/console.log" ||
        fail "the guest environment does not point at the model gateway"
    grep -q 'echo-<redacted>' "$TEMP_DIR/console.log" || fail "the echoed key was not redacted"
    for call in "alpha allow none" "beta allow none" "unknown deny denied" "alpha deny budget"; do
        # shellcheck disable=SC2086  # the three fields are one argument each
        [ "$(model_calls $call)" -ge 1 ] || fail "no model_call for: $call"
    done
    [ "$(events model_attached)" -ge 1 ] || fail "model_attached is missing from the agent log"
    for value in "$openai_key" "$anthropic_key"; do
        if grep -qF -- "$value" "$TEMP_DIR/console.log" "$TEMP_DIR/agent.log"; then
            fail "a provider key reached the guest or the runner log"
        fi
    done
}

model_card() {
    curl -fsS "$web/runs/$run" >"$TEMP_DIR/run-model.html"
    for text in "$model_policy" "of 10 · 100%" "token budget spent" "model outside the policy" "openai-key"; do
        grep -qF -- "$text" "$TEMP_DIR/run-model.html" || return 1
    done
}

# The Run overview follows the budget from the audit and names each key by its secret only.
check_model_card() {
    wait_for 60 model_card
    for value in "$openai_key" "$anthropic_key" "$operator"; do
        ! grep -qF -- "$value" "$TEMP_DIR/run-model.html" || fail "the model card carries a credential"
    done
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
placement = row["placement"]
if not placement["version"] or not placement["platform"] or not placement["address"]:
    sys.exit(f"the runner reported no placement: {placement}")
if not row["heartbeat_seconds"]:
    sys.exit("the runner reported no heartbeat interval")
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

# The fleet as the runners page shows it, then the runner popup with its placement and the run.
check_runners_page() {
    local runner
    runner="$(curl -fsS "${auth[@]}" "$api/api/v1/runners" | "$VENV/bin/python" -c '
import json, sys
print(json.load(sys.stdin)[0]["id"])
')"
    curl -fsS "$web/runners" | "$VENV/bin/python" -c '
import sys
body = sys.stdin.read()
wanted = [">alpha</span>", "dot dot--lg tone-green", "runners-table__load", "runners-table__rotates"]
missing = [text for text in wanted if text not in body]
if missing:
    sys.exit(f"the runners page is missing {missing}")
if "Bearer" in body or "token_hash" in body:
    sys.exit("the runners page carries a credential")
'
    for tab in "" /runs /audit; do
        curl -fsS "$web/runners/$runner$tab" | "$VENV/bin/python" -c '
import sys
body, tab = sys.stdin.read(), sys.argv[1]
wanted = {"": ["naos-runner v", "rotates in", "#1</span>"], "/runs": ["#1</div>"], "/audit": ["lease_acquired"]}[tab]
missing = [text for text in wanted if text not in body]
if missing:
    sys.exit(f"the runner popup {tab or "overview"} is missing {missing}")
if "Bearer" in body or "token_hash" in body:
    sys.exit("the runner popup carries a credential")
' "$tab"
    done
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
    for call in "shell read_file allow" "network http_request allow" "shell read_file deny" \
        "shell git_status deny" "network http_request deny" "alpha search deny" "delta search deny" \
        "shell list_dir allow" "shell list_dir deny" "shell grep allow" "shell grep deny"; do
        # shellcheck disable=SC2086  # the three fields are one argument each
        [ "$(mcp_calls $call)" -ge 1 ] || fail "no mcp_call for: $call"
    done
    # One denied call per rule kind: a whole server by prefix, equality, a regex, a schema, a budget.
    [ "$(grep -o 'denied by rule [0-9]*' "$TEMP_DIR/console.log" | sort -u | wc -l)" -eq 3 ] ||
        fail "the deny rules did not each refuse their call"
    # Counted in the runner log: the console is a terminal, and a burst of replies can lose a line.
    [ "$(grep '"event":"mcp_call"' "$TEMP_DIR/agent.log" |
        grep -c '"decision":"deny".*"category":"denied","rule":"none"')" -ge 4 ] ||
        fail "a call no rule allows was not refused"
    grep -q 'no rule allows this call' "$TEMP_DIR/console.log" ||
        fail "a refused call did not say that no rule allows it"
    # The granted secret reaches the guest and nothing else of naos; the server credential never does.
    grep -qF -- "$agent_secret" "$TEMP_DIR/console.log" || fail "the granted secret did not come back"
    grep -qF -- "$secret" "$TEMP_DIR/console.log" && fail "a server credential reached the guest"
    grep -q 'agent-key' "$TEMP_DIR/console.log" || fail "secrets__list did not name the granted secret"
    grep -qF -- "$agent_secret" "$TEMP_DIR/agent.log" && fail "a secret value reached the runner log"
    # The guest printed the value on its own console, so only that log may hold it.
    grep -rqF --exclude=console.log -- "$agent_secret" "$TEMP_DIR/state" "$TEMP_DIR/runs" &&
        fail "a secret value reached the runner's state"
    for read in '"name":"agent-key","decision":"allow"' '"name":"alpha-token","decision":"deny"'; do
        grep '"event":"secret_read"' "$TEMP_DIR/agent.log" | grep -qF -- "$read" ||
            fail "no secret_read for: $read"
    done
    grep -q 'budget of rule [0-9]* is spent' "$TEMP_DIR/console.log" ||
        fail "a spent rule budget was not refused"
    grep '"event":"mcp_call"' "$TEMP_DIR/agent.log" | grep -q '"decision":"allow".*"rule":"[0-9]' ||
        fail "an allowed mcp_call does not name its rule"
    for event in shell_allowed shell_denied network_allowed network_denied; do
        [ "$(events "$event")" -ge 1 ] || fail "$event is missing from the agent log"
    done
}

# The runner holds a changed policy once it logs one more <kind>_policy_configured.
policy_applied() {
    [ "$(events "$1_policy_configured")" -gt "$configured" ]
}

change_policy() {
    configured="$(events "$1_policy_configured")"
    curl -fsS "${auth[@]}" "$api/api/v1/runs/$run/policies" -o /dev/null -d "$2"
    wait_for 60 policy_applied "$1"
}

# The server added to the started Run was routed to, and is unknown once it was taken away.
check_live_policy() {
    grep -q NAOS-SMOKE-DETACHED "$TEMP_DIR/console.log" || fail "the guest did not call the removed server"
    [ "$(mcp_calls unknown unknown deny)" -ge 1 ] ||
        fail "a server taken away from the run still answered"
    curl -fsS "${auth[@]}" "$api/api/v1/runs/$run" | "$VENV/bin/python" -c '
import json, sys

rows = json.load(sys.stdin)["policy_history"]
held = [(row["kind"], row["previous_id"], row["policy_id"]) for row in rows]
mcp, shell = sys.argv[1:]
kept = [("mcp", mcp, None), ("mcp", None, mcp), ("shell", shell, None), ("shell", None, shell)]
if held != kept:
    sys.exit(f"the run did not keep the policies it held: {held}")
' "$mcp_policy" "$shell_policy"
    curl -fsS "${auth[@]}" "$api/api/v1/audit?event=policy_changed&run_id=$run" | "$VENV/bin/python" -c '
import json, sys

if len(json.load(sys.stdin)) != 4:
    sys.exit("the policy changes of the run are not audited")
'
}

# A capability taken away from the started Run is denied, and answers again once it is given back.
check_live_shell() {
    grep -q NAOS-SMOKE-REGRANTED "$TEMP_DIR/console.log" || fail "the guest did not call the gate again"
    grep '"event":"shell_denied"' "$TEMP_DIR/agent.log" | grep '"capability":"list_dir"' |
        grep -q 'capability not granted' || fail "a capability taken away from the run still answered"
    [ "$(mcp_calls shell list_dir allow)" -gt "$listed" ] ||
        fail "a capability given back to the run did not answer"
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

# The Changes tab of the waiting run: every collected entry, told apart, and no credential.
check_changes_tab() {
    curl -fsS "$web/runs/$run/changes" | "$VENV/bin/python" -c '
import sys
body = sys.stdin.read()
wanted = [
    "run__tab run__tab--active", "Waiting for your decision", "renamed.txt", "moved.txt",
    ">created</span>", ">modified</span>", ">deleted</span>", ">renamed</span>", ">rejected</span>",
    "SELECTED ENTRY", "REJECTED",
]
missing = [text for text in wanted if text not in body]
if missing:
    sys.exit(f"the changes tab is missing {missing}")
if "Bearer" in body or "Authorization" in body:
    sys.exit("the changes tab carries the operator credential")
'
}

# The Changes tab after a decision: the conflict waiting for its resolution, then the report.
check_merge_tab() {
    curl -fsS "$web/runs/$run/changes" | "$VENV/bin/python" -c '
import sys
body = sys.stdin.read()
wanted = {
    "conflicts": ["1 conflict · nothing was written", "CONFLICT · 1 OF 1", "Send decision again"],
    "merged": ["Changes · merged", "REPORT", "WHERE THINGS WENT", "Decided by operator"],
}[sys.argv[1]]
missing = [text for text in wanted if text not in body]
if missing:
    sys.exit(f"the changes tab is missing {missing}")
' "$1"
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
seen |= {("model_call", row["data"]["decision"]) for row in rows if row["event"] == "model_call"}
statuses = ("STARTING", "STARTED", "STOPPING", "COLLECTING", "WAITING_MERGE", "COMPLETED")
expected = {
    "run_created", "run_assigned", "vm_created", "workspace_shared", "network_allowed", "network_denied",
    "shell_allowed", "shell_denied", ("mcp_call", "allow"), ("mcp_call", "deny"),
    ("model_call", "allow"), ("model_call", "deny"),
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
    for value in "$operator" "$(cat "$TEMP_DIR/enrollment")" "$secret" "$agent_secret" "$openai_key" \
        "$anthropic_key" \
        "$(field token <"$TEMP_DIR/state/credentials.json")"; do
        if grep -qF -- "$value" "$TEMP_DIR/audit.json" "$spool"; then
            fail "a credential reached the audit trail"
        fi
    done
}

# The audit page counts the gate denials; a denial's popup names its policy and run timeline.
check_audit_page() {
    local denied
    denied="$(curl -fsS "${auth[@]}" "$api/api/v1/audit?event=network_denied&run_id=$run" |
        "$VENV/bin/python" -c '
import json, sys
row = json.load(sys.stdin)[0]
if row["received_at"] < row["at"]:
    sys.exit("a runner event reached the api before it was written")
print(row["id"])
')"
    curl -fsS "${auth[@]}" "$api/api/v1/audit/summary" | "$VENV/bin/python" -c '
import json, sys
summary = json.load(sys.stdin)
if min(summary["denials"].values()) < 1 or summary["spool_lag"] is None:
    sys.exit(f"the audit summary misses the smoke run: {summary}")
'
    for path in "/audit?q=network_denied" "/audit/$denied" "/audit/$denied/logs"; do
        curl -fsS "$web$path" >"$TEMP_DIR/audit.html"
        "$VENV/bin/python" - "$TEMP_DIR/audit.html" "$path" "$secret" "$operator" <<'PY' || fail "the audit page is wrong"
import re
import sys
page, path, *secrets = sys.argv[1:]
body = re.sub(r">\s+|\s+<", lambda m: m.group().strip(), open(page).read())
if path.endswith("/logs"):
    wanted = ["network_denied", "timeline of run #"]
elif "?" in path:
    wanted = ["network_denied", "tile__number"]
else:
    wanted = ["network_denied", "<dt>Gate</dt>", "<dd>network</dd>", 'hx-get="/runs/', 'hx-get="/runners/']
missing = [text for text in wanted if text not in body]
if missing:
    sys.exit(f"{path} is missing {missing}")
if any(value in body for value in secrets) or "Bearer" in body:
    sys.exit(f"{path} carries a credential")
PY
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
        --data-urlencode "profile=$smoke_profile" \
        --data-urlencode "mode=update" \
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
        --data-urlencode "model=$model_policy" \
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
if spec["model"]["policy"] != sys.argv[1]:
    sys.exit(f"the run does not carry the model policy picked in the dialog: {spec}")
print(runs[0]["id"])
' "$1"
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

# A policy is found by its digest, reads who uses it, and an equivalent document keeps its id.
check_policies_page() {
    local digest
    digest="$(curl -fsS "${auth[@]}" "$api/api/v1/policies/$network_policy" |
        "$VENV/bin/python" -c '
import json, sys
row = json.load(sys.stdin)
if sys.argv[1] not in row["profiles"] or row["runs_open"] < 1:
    sys.exit(f"the network policy misses its profile or run: {row}")
print(row["digest"])
' "$profile")"
    curl -fsS "${auth[@]}" "$api/api/v1/runs?policy=$network_policy" | "$VENV/bin/python" -c '
import json, sys
if [row["id"] for row in json.load(sys.stdin)] != [sys.argv[1]]:
    sys.exit("the runs of the network policy are wrong")
' "$run"
    curl -fsS "$web/policies?q=${digest:0:12}" >"$TEMP_DIR/policies-list.html"
    curl -fsS "$web/policies/$network_policy" >"$TEMP_DIR/policies-document.html"
    curl -fsS "$web/policies/$network_policy/used" >"$TEMP_DIR/policies-used.html"
    curl -fsS "$web/policies/$mcp_policy" >"$TEMP_DIR/policies-mcp.html"
    curl -fsS "$web/policies?kind=model" >"$TEMP_DIR/policies-kind.html"
    curl -fsS "$web/policies/$model_policy" >"$TEMP_DIR/policies-model.html"
    curl -fsS -X POST "$web/policies/new" --data-urlencode "kind=shell" \
        --data-urlencode "cap.read_file=on" --data-urlencode "cap.list_dir=on" \
        --data-urlencode "cap.grep=on" >"$TEMP_DIR/policies-exists.html"
    "$VENV/bin/python" - "$TEMP_DIR" "$network_policy" "$shell_policy" "$run" "$model_policy" \
        "$secret" "$operator" "$openai_key" "$anthropic_key" <<'PY' || fail "the policies page is wrong"
import sys
temp, network, shell, run, model, *secrets = sys.argv[1:]
wanted = {
    "list": [network, "tile__number"],
    "kind": [model, "2 providers"],
    "model": ["PROVIDERS · 2", "BUDGET · PER RUN", "SECRETS · 2", "anthropic-key"],
    "document": ["CANONICAL DOCUMENT", "www.google.com", "New from this"],
    "used": [run, "Open profile"],
    "mcp": ["SECRETS · 1", "alpha-token"],
    "exists": ["Policy already exists", shell],
}
for name, texts in wanted.items():
    body = open(f"{temp}/policies-{name}.html").read()
    missing = [text for text in texts if text not in body]
    if missing:
        sys.exit(f"policies {name} is missing {missing}")
    if any(value in body for value in secrets) or "Bearer" in body:
        sys.exit(f"policies {name} carries a credential")
PY
}

# The web posts a value and answers with a redirect that names the secret only.
secret_post() {
    printf '%s' "$2" | curl -fsS -o /dev/null -w '%{redirect_url}' -X POST "$web/secrets/$1" \
        --data-urlencode "value@-" "${@:3}"
}

# A secret is created, rotated and deleted through the web; one in use is refused, none is shown.
check_secrets_page() {
    local first second
    first="$(token)"
    second="$(token)"
    [ "$(secret_post _new "$first" --data-urlencode "name=beta-token" --data-urlencode "term=1h")" = \
        "$web/secrets/beta-token" ] || fail "the web did not create the secret"
    [ "$(secret_post beta-token/rotate "$second")" = "$web/secrets/beta-token" ] ||
        fail "the web did not rotate the secret"
    curl -fsS "${auth[@]}" "$api/api/v1/secrets/beta-token" | "$VENV/bin/python" -c '
import json, sys
row = json.load(sys.stdin)
if row["rotated_at"] is None or row["expires_at"] is None or "value" in row:
    sys.exit(f"the secret the web made is wrong: {row}")
'
    curl -fsS "$web/secrets" >"$TEMP_DIR/secrets-list.html"
    curl -fsS "$web/secrets/beta-token/events" >"$TEMP_DIR/secrets-events.html"
    curl -fsS "$web/secrets/alpha-token/used" >"$TEMP_DIR/secrets-used.html"
    curl -fsS -X POST "$web/secrets/alpha-token/delete" >"$TEMP_DIR/secrets-refused.html"
    curl -fsS "${auth[@]}" "$api/api/v1/audit?secret=beta-token" >"$TEMP_DIR/secrets-audit.html"
    [ "$(curl -fsS -o /dev/null -w '%{redirect_url}' -X POST "$web/secrets/beta-token/delete")" = \
        "$web/secrets" ] || fail "the web did not delete the unused secret"
    "$VENV/bin/python" - "$TEMP_DIR" "$run" "$first" "$second" "$secret" "$operator" <<'PY' || fail "the secrets page is wrong"
import sys
temp, run, *secrets = sys.argv[1:]
wanted = {
    "list": ["beta-token", "alpha-token", "tile__number"],
    "events": ["secret_created", "secret_rotated"],
    "used": ["mcp registry", "server alpha", run],
    "refused": ["alpha-token cannot be deleted", "the api answered 409"],
    "audit": ["secret_created", "secret_rotated"],
}
for name, texts in wanted.items():
    body = open(f"{temp}/secrets-{name}.html").read()
    missing = [text for text in texts if text not in body]
    if missing:
        sys.exit(f"secrets {name} is missing {missing}")
    if any(value in body for value in secrets) or "Bearer" in body:
        sys.exit(f"secrets {name} carries a credential")
PY
}

# A Run of its own through the api: no mounts, and one mcp policy that grants one secret and
# names the registry server the first Run names too. Prints the id of the Run.
side_run() {
    local policy
    curl -fsS "${auth[@]}" "$api/api/v1/secrets" -o /dev/null -d "{\"name\": \"$1\", \"value\": \"$2\"}"
    policy="$(
        curl -fsS "${auth[@]}" "$api/api/v1/policies" -d @- <<EOF | field id
{"kind": "mcp", "document": {"rules": [
  {"server": "alpha", "tool": "search", "effect": "allow"},
  {"server": "secrets", "tool": "list", "effect": "allow"},
  {"server": "secrets", "tool": "get", "effect": "allow", "arguments": {"name": {"equals": "$1"}}}
]}}
EOF
    )"
    curl -fsS "${auth[@]}" -H "Idempotency-Key: $1" "$api/api/v1/runs" -d @- <<EOF | field id
{"image": {"id": "naos-agents", "digest": "$digest"}, "timeout": 3600,
 "runtime": {"cpu": 2, "memory_mib": 2048, "disk_gib": 8}, "mcp": {"policy": "$policy"}}
EOF
}

vm_dir_of() {
    local meta
    meta="$(grep -ls "\"run_id\":\"$1\"" "$TEMP_DIR"/runs/*/vm.json | head -n1 || true)"
    [ -z "$meta" ] || dirname "$meta"
}

side_booted() {
    local dir status
    status="$(run="$1" run_status)"
    [ "$status" != FAILED ] || fail "run $1 failed to boot"
    dir="$(vm_dir_of "$1")"
    [ "$status" = STARTED ] && [ -n "$dir" ] && grep -qs naos-ready "$dir/boot.log" &&
        grep -qs 'naos-probe ok' "$dir/boot.log"
}

# One secrets__get as a guest sends it; a third argument forges that Run's id around the call.
get_secret() {
    local forged=""
    [ -z "${3:-}" ] || forged=",\"run_id\":\"$3\",\"_meta\":{\"run_id\":\"$3\"}"
    printf '{"jsonrpc":"2.0","id":%s,"run_id":"%s","method":"tools/call","params":{"name":"secrets__get","arguments":{"name":"%s"}%s}}' \
        "$1" "${3:-none}" "$2" "$forged"
}

# What a Run asks its own broker beside another Run: its secret, the other's, the other's again
# with that Run's id forged into the request, and the server both Runs name.
side_guest() {
    local own="$1" name="$2" foreign="$3" other="$4" id="$5"
    run="$own" console_log="$TEMP_DIR/side-$own.log" guest \
        "cat > /tmp/rpc <<'JSON'" \
        "{\"jsonrpc\":\"2.0\",\"id\":$((id + 1)),\"method\":\"tools/call\",\"params\":{\"name\":\"secrets__list\",\"arguments\":{}}}" \
        "$(get_secret $((id + 2)) "$name")" \
        "$(get_secret $((id + 3)) "$foreign")" \
        "$(get_secret $((id + 4)) "$foreign" "$other")" \
        "{\"jsonrpc\":\"2.0\",\"id\":$((id + 5)),\"method\":\"tools/call\",\"params\":{\"name\":\"alpha__search\",\"arguments\":{}}}" \
        "JSON" \
        "{ cat /tmp/rpc; sleep 20; } | naos-mcp" \
        "echo NAOS-SMOKE-SIDE-$id"
}

# The secret reads the runner decided for one Run, by name and decision.
secret_reads() {
    grep '"event":"secret_read"' "$TEMP_DIR/agent.log" | grep "\"run_id\":\"$1\"" |
        grep -cF -- "\"name\":\"$2\",\"decision\":\"$3\"" || true
}

# The runner let go of a Run once the gate of that Run logs that it holds no credential.
released() {
    grep '"event":"mcp_credentials_updated"' "$TEMP_DIR/agent.log" | grep "\"run_id\":\"$1\"" |
        grep -q '"names":""'
}

attached_again() {
    [ "$(events mcp_attached)" -ge "$1" ]
}

# One of the two Runs after its guest typed `rounds` times: its own secret came back, the other's
# never did, and every read was decided under its own Run id.
check_side() {
    local run_id="$1" name="$2" foreign="$3" value="$4" other_value="$5" rounds="$6"
    local log="$TEMP_DIR/side-$run_id.log"
    grep -qF -- "$value" "$log" || fail "run $run_id did not read its own secret"
    if grep -qF -- "$other_value" "$log"; then
        fail "run $run_id read the secret of the other run"
    fi
    [ "$(secret_reads "$run_id" "$name" allow)" -ge "$rounds" ] ||
        fail "run $run_id has no allowed secret_read of $name"
    [ "$(secret_reads "$run_id" "$foreign" deny)" -ge $((rounds * 2)) ] ||
        fail "run $run_id was not refused $foreign, the forged request included"
    [ "$(secret_reads "$run_id" "$foreign" allow)" -eq 0 ] ||
        fail "run $run_id was allowed the secret of the other run"
    [ "$(grep '"event":"mcp_call"' "$TEMP_DIR/agent.log" | grep "\"run_id\":\"$run_id\"" |
        grep -c '"server":"alpha","tool":"search"')" -ge "$rounds" ] ||
        fail "run $run_id did not reach the server both runs name"
}

# Two Runs on the one runner at the same time, before and after a restart of the runner, and
# then one of them stopping while the other keeps what it holds.
check_isolation() {
    local first second first_value second_value typing attached id run_id value rounds=0
    first_value="$(token)"
    second_value="$(token)"
    first="$(side_run side-a-key "$first_value")"
    second="$(side_run side-b-key "$second_value")"
    wait_for 300 side_booted "$first"
    wait_for 300 side_booted "$second"
    for id in 40 50; do
        rounds=$((rounds + 1))
        side_guest "$first" side-a-key side-b-key "$second" "$id" &
        typing=$!
        side_guest "$second" side-b-key side-a-key "$first" "$id"
        wait "$typing"
        for run_id in "$first" "$second"; do
            grep -q "NAOS-SMOKE-SIDE-$id" "$TEMP_DIR/side-$run_id.log" ||
                fail "the guest of run $run_id did not finish"
        done
        check_side "$first" side-a-key side-b-key "$first_value" "$second_value" "$rounds"
        check_side "$second" side-b-key side-a-key "$second_value" "$first_value" "$rounds"
        if [ "$id" = 40 ]; then
            echo "restarting the agent under both runs..."
            attached="$(events mcp_attached)"
            kill -- "-$agent"
            wait_for 30 agent_gone
            start_agent
            wait_for 60 enrolled
            wait_for 120 attached_again $((attached + 2))
        fi
    done
    for value in "$first_value" "$second_value"; do
        if grep -rqF --exclude=console.log -- "$value" "$TEMP_DIR/agent.log" "$TEMP_DIR/state" "$TEMP_DIR/runs"; then
            fail "a secret value of a side run reached the runner log or its state"
        fi
    done
    echo "stopping one of the two runs, the other keeps going..."
    web_post "$first" stop >/dev/null
    wait_for 120 released "$first"
    if released "$second"; then
        fail "stopping one run let go of the gate of the other"
    fi
    run="$second" console_log="$TEMP_DIR/side-$second.log" guest \
        "cat > /tmp/rpc <<'JSON'" \
        "$(get_secret 61 side-b-key)" \
        "JSON" \
        "{ cat /tmp/rpc; sleep 5; } | naos-mcp" \
        "echo NAOS-SMOKE-KEPT"
    grep -q NAOS-SMOKE-KEPT "$TEMP_DIR/side-$second.log" ||
        fail "the guest of the kept run did not finish"
    [ "$(secret_reads "$second" side-b-key allow)" -ge 3 ] ||
        fail "the run that kept going lost its secret"
    web_post "$second" stop >/dev/null
    wait_for 120 released "$second"
}

share_gone() {
    ! pgrep -f -- "--socket-path=$TEMP_DIR/runs" >/dev/null
}

start_agent() {
    NAOS_AGENT_API_URL="$api" \
        NAOS_AGENT_NAME=alpha \
        NAOS_AGENT_CAPACITY=3 \
        NAOS_AGENT_STATE_DIR="$TEMP_DIR/state" \
        NAOS_AGENT_ENROLLMENT_TOKEN_FILE="$TEMP_DIR/enrollment" \
        NAOS_AGENT_IMAGE_DIR="$TEMP_DIR/vms" \
        NAOS_AGENT_VM_DIR="$TEMP_DIR/runs" \
        NAOS_AGENT_SMOKE_CA_FILE="$TEMP_DIR/ca.crt" \
        setsid cargo run -q -p naos-runner --features smoke-stubs >>"$TEMP_DIR/agent.log" 2>&1 &
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
if [ ! -r /dev/vhost-vsock ] || [ ! -w /dev/vhost-vsock ]; then
    echo "the model gateway needs /dev/vhost-vsock, see docs/host/vhost-vsock.md" >&2
    exit 1
fi

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

# The stubs answer on loopback under a CA of their own; only the smoke-stubs runner reaches them.
echo "starting the stub model providers..."
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=naos-smoke-ca \
    -keyout "$TEMP_DIR/ca.key" -out "$TEMP_DIR/ca.crt" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj /CN=naos-smoke-stub \
    -keyout "$TEMP_DIR/stub.key" -out "$TEMP_DIR/stub.csr" 2>/dev/null
printf '%s\n' 'subjectAltName=DNS:openai.example.com,DNS:anthropic.example.com' \
    'basicConstraints=CA:FALSE' 'extendedKeyUsage=serverAuth' >"$TEMP_DIR/stub.ext"
openssl x509 -req -in "$TEMP_DIR/stub.csr" -CA "$TEMP_DIR/ca.crt" -CAkey "$TEMP_DIR/ca.key" \
    -CAcreateserial -days 1 -extfile "$TEMP_DIR/stub.ext" -out "$TEMP_DIR/stub.crt" 2>/dev/null
openai_key="$(token)"
anthropic_key="$(token)"
stub_port="$("$VENV/bin/python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
STUB_OPENAI_KEY="$openai_key" STUB_ANTHROPIC_KEY="$anthropic_key" \
    "$VENV/bin/python" "$ROOT/make/tests/stub_provider.py" "$stub_port" \
    "$TEMP_DIR/stub.crt" "$TEMP_DIR/stub.key" >"$TEMP_DIR/stub.log" 2>&1 &
stub=$!

echo "starting api..."
NAOS_OPERATOR_TOKEN_SHA256="$(printf '%s' "$operator" | sha256sum | cut -d' ' -f1)" \
NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256="$(sha256sum "$TEMP_DIR/enrollment" | cut -d' ' -f1)" \
NAOS_DATABASE_URL="sqlite:///$TEMP_DIR/naos.db" \
NAOS_DATABASE_AUTO_MIGRATE=true \
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
agent_secret="$(token)"
curl -fsS "${auth[@]}" "$api/api/v1/secrets" -o /dev/null -d @- <<EOF
{"name": "agent-key", "value": "$agent_secret"}
EOF
curl -fsS "${auth[@]}" "$api/api/v1/mcp-servers" -o /dev/null -d @- <<EOF
{"name": "alpha", "url": "https://example.com/mcp", "credential": "alpha-token"}
EOF
curl -fsS "${auth[@]}" "$api/api/v1/mcp-servers" -o /dev/null \
    -d '{"name": "delta", "url": "https://example.com/delta"}'
mcp_rules="$(
    cat <<'EOF'
  {"server": "shell", "tool": "*", "effect": "allow"},
  {"server": "shell", "tool": "*", "effect": "deny",
   "arguments": {"path": {"prefix": "/naos/alpha/dir"}}},
  {"server": "shell", "tool": "grep", "effect": "deny",
   "arguments": {"pattern": {"equals": "gamma"}}},
  {"server": "shell", "tool": "read_file", "effect": "deny",
   "arguments": {"path": {"regex": ".*/added[.]txt"}}},
  {"server": "network", "tool": "http_request", "effect": "allow",
   "arguments": {"url": {"prefix": "https://www."}, "method": {"schema": {"enum": ["GET"]}}}},
  {"server": "alpha", "tool": "search", "effect": "allow", "max_calls": 1},
  {"server": "secrets", "tool": "list", "effect": "allow"},
  {"server": "secrets", "tool": "get", "effect": "allow",
   "arguments": {"name": {"equals": "agent-key"}}}
EOF
)"
mcp_policy="$(
    curl -fsS "${auth[@]}" "$api/api/v1/policies" \
        -d "{\"kind\": \"mcp\", \"document\": {\"rules\": [$mcp_rules]}}" | field id
)"
# A policy names registered servers only, and a rule that can never match is refused.
for rule in '{"server": "beta", "tool": "search", "effect": "allow"}' \
    '{"server": "shell", "tool": "rm", "effect": "allow"}' \
    '{"server": "shell", "tool": "read_file", "effect": "allow", "arguments": {"url": {"prefix": "a"}}}' \
    '{"server": "alpha", "tool": "search", "effect": "allow", "arguments": {"q": {"regex": "(?=a)b"}}}' \
    '{"server": "secrets", "tool": "*", "effect": "allow"}' \
    '{"server": "secrets", "tool": "get", "effect": "allow", "arguments": {"name": {"prefix": "a"}}}'; do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "${auth[@]}" "$api/api/v1/policies" \
        -d "{\"kind\": \"mcp\", \"document\": {\"rules\": [$rule]}}")" = 422 ] ||
        fail "an mcp rule that cannot hold was accepted: $rule"
done
for name in openai anthropic; do
    key_var="${name}_key"
    curl -fsS "${auth[@]}" "$api/api/v1/secrets" -o /dev/null -d @- <<EOF
{"name": "$name-key", "value": "${!key_var}"}
EOF
done
# The policy form creates it. Two calls of 5 output tokens each spend the budget, so the third
# is refused.
model_policy="$(
    curl -fsS -o /dev/null -w '%{redirect_url}' -X POST "$web/policies/new" \
        --data-urlencode "kind=model" \
        --data-urlencode "max_input=1000" \
        --data-urlencode "max_output=10" \
        --data-urlencode "providers.0.name=alpha" \
        --data-urlencode "providers.0.api=openai" \
        --data-urlencode "providers.0.url=https://openai.example.com:$stub_port" \
        --data-urlencode "providers.0.models=alpha-mini" \
        --data-urlencode "providers.0.credential=openai-key" \
        --data-urlencode "providers.0.timeout=600" \
        --data-urlencode "providers.0.requests=60" \
        --data-urlencode "providers.1.name=beta" \
        --data-urlencode "providers.1.api=anthropic" \
        --data-urlencode "providers.1.url=https://anthropic.example.com:$stub_port" \
        --data-urlencode "providers.1.models=beta-large" \
        --data-urlencode "providers.1.credential=anthropic-key" \
        --data-urlencode "providers.1.timeout=600" \
        --data-urlencode "providers.1.requests=60"
)"
model_policy="${model_policy##*/}"
[ "${model_policy#modelpol_}" != "$model_policy" ] || fail "the web did not create the model policy"
# The profile names no model policy; the dialog picks it and saves it into the profile.
smoke_profile="$(
    curl -fsS "${auth[@]}" "$api/api/v1/profiles" -d @- <<EOF | field id
{"name": "smoke", "spec": {
  "runtime": {"cpu": 2, "memory_mib": 2048, "disk_gib": 8}, "timeout": 3600, "merge": {"policy": "ask"},
  "mounts": {"policy": "$mount_policy"}, "network": {"policy": "$network_policy"},
  "shell": {"policy": "$shell_policy"}, "mcp": {"policy": "$mcp_policy"}
}}
EOF
)"
echo "creating the run from the new run dialog..."
new_run
new_run
run="$(curl -fsS "${auth[@]}" "$api/api/v1/runs" | only_run "$model_policy")"
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
check_runners_page
check_profile_locked
check_policies_page
echo "creating, rotating and deleting a secret through the web..."
check_secrets_page
check_run_detail STARTED
check_confirms
echo "editing the mcp policy of the started run: one more server..."
delta_rule='{"server": "delta", "tool": "search", "effect": "allow"}'
change_policy mcp "{\"kind\": \"mcp\", \"document\": {\"rules\": [$mcp_rules, $delta_rule]}}"
echo "editing the workspace and calling the gates from the console..."
# shellcheck disable=SC2016  # the guest shell expands these, not this one
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
    '{"jsonrpc":"2.0","id":18,"method":"tools/call","params":{"name":"alpha__search","arguments":{"query":"naos"}}}' \
    '{"jsonrpc":"2.0","id":19,"method":"tools/call","params":{"name":"alpha__delete","arguments":{}}}' \
    '{"jsonrpc":"2.0","id":20,"method":"tools/call","params":{"name":"list_dir","arguments":{"path":"/naos/alpha/dir"}}}' \
    '{"jsonrpc":"2.0","id":21,"method":"tools/call","params":{"name":"list_dir","arguments":{"path":"/naos/alpha"}}}' \
    '{"jsonrpc":"2.0","id":22,"method":"tools/call","params":{"name":"grep","arguments":{"path":"/naos/alpha","pattern":"gamma"}}}' \
    '{"jsonrpc":"2.0","id":23,"method":"tools/call","params":{"name":"grep","arguments":{"path":"/naos/alpha","pattern":"alpha"}}}' \
    '{"jsonrpc":"2.0","id":24,"method":"tools/call","params":{"name":"read_file","arguments":{"path":"/naos/alpha/added.txt"}}}' \
    '{"jsonrpc":"2.0","id":25,"method":"tools/call","params":{"name":"http_request","arguments":{"method":"HEAD","url":"https://www.google.com/robots.txt"}}}' \
    '{"jsonrpc":"2.0","id":26,"method":"tools/call","params":{"name":"http_request","arguments":{"method":"GET","url":"https://example.com/"}}}' \
    '{"jsonrpc":"2.0","id":27,"method":"tools/call","params":{"name":"secrets__list","arguments":{}}}' \
    '{"jsonrpc":"2.0","id":28,"method":"tools/call","params":{"name":"secrets__get","arguments":{"name":"agent-key"}}}' \
    '{"jsonrpc":"2.0","id":29,"method":"tools/call","params":{"name":"secrets__get","arguments":{"name":"alpha-token"}}}' \
    '{"jsonrpc":"2.0","id":30,"method":"tools/call","params":{"name":"delta__search","arguments":{}}}' \
    '{"jsonrpc":"2.0","id":11,"method":"ping"}' \
    "JSON" \
    "{ cat /tmp/rpc; sleep 20; } | naos-mcp" \
    'echo "MODEL-ENV $OPENAI_BASE_URL $ANTHROPIC_BASE_URL"' \
    "cat > /tmp/chat <<'JSON'" \
    '{"model":"alpha-mini","messages":[{"role":"user","content":"hi"}]}' \
    "JSON" \
    "cat > /tmp/messages <<'JSON'" \
    '{"model":"beta-large","max_tokens":16,"stream":true,"messages":[{"role":"user","content":"hi"}]}' \
    "JSON" \
    "cat > /tmp/foreign <<'JSON'" \
    '{"model":"gamma","messages":[]}' \
    "JSON" \
    'wget -q -O - --header "content-type: application/json" --header "authorization: Bearer $OPENAI_API_KEY" --post-file /tmp/chat "$OPENAI_BASE_URL/chat/completions"; echo' \
    'wget -q -O - --header "content-type: application/json" --header "x-api-key: $ANTHROPIC_API_KEY" --header "anthropic-version: 2023-06-01" --post-file /tmp/messages "$ANTHROPIC_BASE_URL/v1/messages"; echo' \
    'wget -O - --header "content-type: application/json" --post-file /tmp/foreign "$OPENAI_BASE_URL/chat/completions"; echo' \
    'wget -O - --header "content-type: application/json" --post-file /tmp/chat "$OPENAI_BASE_URL/chat/completions"; echo' \
    "echo NAOS-SMOKE-DONE"
check_gates
check_models
check_model_card
echo "giving the started run its stored mcp policy back: the server is gone..."
change_policy mcp "{\"kind\": \"mcp\", \"policy_id\": \"$mcp_policy\"}"
echo "editing the shell policy of the started run: list_dir is taken away..."
change_policy shell '{"kind": "shell", "document": {"allow": ["read_file", "grep"]}}'
guest \
    "cat > /tmp/rpc <<'JSON'" \
    '{"jsonrpc":"2.0","id":31,"method":"tools/call","params":{"name":"delta__search","arguments":{}}}' \
    '{"jsonrpc":"2.0","id":32,"method":"tools/call","params":{"name":"list_dir","arguments":{"path":"/naos/alpha"}}}' \
    "JSON" \
    "{ cat /tmp/rpc; sleep 5; } | naos-mcp" \
    "echo NAOS-SMOKE-DETACHED"
echo "giving the started run its stored shell policy back..."
listed="$(mcp_calls shell list_dir allow)"
change_policy shell "{\"kind\": \"shell\", \"policy_id\": \"$shell_policy\"}"
guest \
    "cat > /tmp/rpc <<'JSON'" \
    '{"jsonrpc":"2.0","id":33,"method":"tools/call","params":{"name":"list_dir","arguments":{"path":"/naos/alpha"}}}' \
    "JSON" \
    "{ cat /tmp/rpc; sleep 5; } | naos-mcp" \
    "echo NAOS-SMOKE-REGRANTED"
check_live_policy
check_live_shell
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
echo "reading the diff on the changes tab..."
check_changes_tab
echo "editing the host workspace and merging, which conflicts..."
printf 'local\n' >"$TEMP_DIR/workspaces/alpha/notes.txt"
decide '{}'
wait_for 60 conflicted
check_merge_tab conflicts
[ ! -e "$TEMP_DIR/workspaces/alpha/added.txt" ] || fail "a conflicted merge wrote to the workspace"
echo "taking the agent's version of notes.txt..."
decide '{"notes.txt": "take"}'
wait_for 120 completed
check_merge_tab merged
check_run_view COMPLETED finished
check_summary COMPLETED 0
check_merge
echo "checking the audit timeline of the run..."
wait_for 60 audited
check_secrets
echo "checking the logs & audit tab of the run..."
check_run_logs
echo "checking the audit page..."
check_audit_page
echo "rerunning the run from its overlay, then stopping the copy..."
rerun_key="$(curl -fsS "$web/runs/$run/rerun" | sed -n 's/.*name="key" value="\([0-9a-f]*\)".*/\1/p')"
copy="$(web_post "$run" rerun "$rerun_key")"
[ "$(web_post "$run" rerun "$rerun_key")" = "$copy" ] || fail "one rerun key made two runs"
case "$copy" in "$web/runs/run_"*) ;; *) fail "rerun did not open a new run" ;; esac
web_post "${copy##*/}" stop >/dev/null
echo "starting two runs side by side, each with its own mcp policy and secret..."
check_isolation
echo "smoke test passed"
