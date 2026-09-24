from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from naos_web import format
from naos_web.pages import (
    LIFECYCLE,
    MERGE_MEANING,
    OPEN_ACTION,
    ROW_ACTIONS,
    RUNNER_TONE,
    STATUS_TONE,
    STOPPABLE,
    RowAction,
    Summary,
    TileValue,
    Tone,
)

Row = dict[str, Any]


@dataclass(frozen=True)
class Action:
    label: str
    href: str
    post: bool
    confirm: str
    overlay: bool


@dataclass(frozen=True)
class RunRow:
    id: str
    seq: int
    status: str
    tone: Tone
    workspace: str
    image: str
    secondary: str
    runner: str
    runner_tone: Tone | None
    started: str
    duration: str
    action: Action


@dataclass(frozen=True)
class HeldRow:
    seq: int
    status: str


@dataclass(frozen=True)
class RunnerRow:
    id: str
    name: str
    status: str
    tone: Tone
    slots: str
    lease: str
    percent: int
    heartbeat: str
    held: list[HeldRow]
    load: str
    load_tone: Tone | None
    lease_bar: bool
    seen: str
    rotates: str
    revocable: bool


def _action(run: Row) -> Action:
    action: RowAction = ROW_ACTIONS.get(run["status"], OPEN_ACTION)
    return Action(
        label=action.label,
        href=action.href.format(id=run["id"]),
        post=action.post,
        confirm=action.confirm.format(seq=run["seq"]),
        overlay=action.href == OPEN_ACTION.href,
    )


# The merge summary and the failure reason both belong under the spec; a run never has both.
def _secondary(run: Row) -> str:
    if run["status_reason"]:
        return str(run["status_reason"])
    merge = run["merge"]
    if merge is None:
        return ""
    return f"{merge['changed']} files changed, {merge['conflicts']} conflicts"


def run_rows(runs: list[Row], runners: list[Row], now: int) -> list[RunRow]:
    tones = {runner["id"]: RUNNER_TONE[runner["status"]] for runner in runners}
    rows = []
    for run in runs:
        runner = run["runner"]
        image = run["spec"]["image"]
        rows.append(
            RunRow(
                id=run["id"],
                seq=run["seq"],
                status=run["status"],
                tone=STATUS_TONE[run["status"]],
                workspace=run["workspace"] or format.DASH,
                image=f"{image['id']} \u00b7 {image['digest'].removeprefix('sha256:')[:12]}",
                secondary=_secondary(run),
                runner=runner["name"] if runner else "unassigned",
                runner_tone=tones.get(runner["id"]) if runner else None,
                started=format.ago(run["created_at"], now),
                duration=format.duration(run["started_at"], run["finished_at"], now),
                action=_action(run),
            )
        )
    return rows


# A revoked runner says when it was revoked; a live or lapsed one says where its lease stands.
def _lease(runner: Row, now: int) -> str:
    if runner["status"] == "revoked":
        return f"revoked {format.ago(runner['revoked_at'], now)}"
    return format.lease_left(runner["lease_acquired_at"], runner["lease_expires_at"], now)


@dataclass(frozen=True)
class Fact:
    label: str
    value: str
    mono: bool = False


@dataclass(frozen=True)
class RunnerDetailRow:
    id: str
    name: str
    status: str
    tone: Tone
    meta: list[str]
    load: str
    load_tone: Tone | None
    free: str
    percent: int
    held: list[RunRow]
    placement: list[Fact]
    labels: list[str]
    lease_id: str
    lease_left: str
    lease_percent: int
    facts: list[Fact]
    revocable: bool
    drainable: bool
    runs: list[RunRow]


# Slots read by meaning: none at all is a fault, some free is fine, all taken is busy.
def _load(runner: Row) -> tuple[str, Tone | None]:
    busy, capacity = len(runner["runs"]), runner["capacity"]
    if capacity is None:
        return format.DASH, None
    if capacity == 0:
        return f"{busy}/0", "red"
    return f"{busy}/{capacity}", "blue" if busy >= capacity else "green"


def _lease_id(runner: Row) -> str:
    if runner["status"] == "revoked" or not runner.get("lease_id"):
        return format.DASH
    return str(runner["lease_id"])


def _lease_left(runner: Row, now: int) -> str:
    acquired, expires = runner["lease_acquired_at"], runner["lease_expires_at"]
    if runner["status"] == "live" and acquired is not None and expires is not None:
        return f"{format.left(expires, now)}s of {max(expires - acquired, 0)}s left"
    lapsed = runner.get("lease_lapsed_at")
    if runner["status"] == "stale" and lapsed is not None:
        return f"expired {format.ago(lapsed, now)}"
    return "no live lease"


def _rotates(runner: Row, now: int, span: Callable[[int], str]) -> str:
    if runner["status"] == "revoked":
        return format.DASH
    left = format.left(runner["token_rotates_at"], now)
    return span(left) if left else "next heartbeat"


def _token(runner: Row, now: int) -> str:
    if runner["status"] == "revoked":
        return "refused"
    left = format.left(runner["token_rotates_at"], now)
    return f"rotates in {format.fine(left)}" if left else "rotates on the next heartbeat"


def _previous(runner: Row, now: int) -> str:
    expires = runner.get("prev_token_expires_at")
    if expires is None or runner["status"] == "revoked":
        return format.DASH
    return f"valid {format.fine(format.left(expires, now))} more"


STATE_NOTE = {
    "live": "heartbeat under 30s",
    "stale": "lease expired, fenced",
    "revoked": "token refused",
}


def _runner_state(runner: Row, now: int) -> str:
    status = runner["status"]
    if status == "live" and runner.get("drained_at") is not None:
        return "LIVE \u00b7 draining"
    if status == "live":
        seen = runner["last_heartbeat_at"]
        if seen is not None and now - seen >= 30:
            return f"LIVE \u00b7 heartbeat {format.ago(seen, now)}"
    return f"{status.upper()} \u00b7 {STATE_NOTE[status]}"


def _heartbeat(runner: Row, now: int) -> str:
    seen = format.ago(runner["last_heartbeat_at"], now)
    every = runner.get("heartbeat_seconds")
    return f"{seen} \u00b7 every {every}s" if every and runner["last_heartbeat_at"] else seen


def _agent(placement: Row) -> str | None:
    return f"naos-runner v{placement['version']}" if placement.get("version") else None


def _placement(placement: Row) -> list[Fact]:
    return [
        Fact("Host", placement.get("host") or format.DASH),
        Fact("Address", placement.get("address") or format.DASH, mono=True),
        Fact("Zone", placement.get("zone") or format.DASH),
        Fact("Platform", placement.get("platform") or format.DASH),
        Fact("Agent", _agent(placement) or format.DASH),
    ]


# Runs on the current lease come first, the rest follow newest first.
def _ordered(runner: Row, runs: list[Row]) -> list[Row]:
    held = {run["id"] for run in runner["runs"]}
    return sorted(runs, key=lambda run: (run["id"] not in held, -run["seq"]))


def runner_detail(runner: Row, runs: list[Row], now: int) -> RunnerDetailRow:
    placement: Row = runner.get("placement") or {}
    load, load_tone = _load(runner)
    busy, capacity = len(runner["runs"]), runner["capacity"]
    held = {run["id"] for run in runner["runs"]}
    ordered = run_rows(_ordered(runner, runs), [runner], now)
    meta = [
        f"enrolled {format.ago(runner['created_at'], now)}",
        _agent(placement),
        placement.get("zone"),
        f"{load} slots" if capacity is not None else None,
    ]
    revoked = runner["status"] == "revoked"
    return RunnerDetailRow(
        id=runner["id"],
        name=runner["name"],
        status=runner["status"],
        tone=RUNNER_TONE[runner["status"]],
        meta=[part for part in meta if part],
        load=load,
        load_tone=load_tone,
        free=format.DASH if capacity is None else f"{max(capacity - busy, 0)} free",
        percent=0 if not capacity else round(100 * busy / capacity),
        held=[row for row in ordered if row.id in held],
        placement=_placement(placement),
        labels=list(placement.get("labels") or []),
        lease_id=_lease_id(runner),
        lease_left=_lease_left(runner, now),
        lease_percent=format.lease_percent(
            runner["lease_acquired_at"], runner["lease_expires_at"], now
        ),
        facts=[
            Fact("State", _runner_state(runner, now)),
            Fact("Heartbeat", _heartbeat(runner, now)),
            Fact("Token", _token(runner, now)),
            Fact("Previous token", _previous(runner, now)),
            Fact("Registered", f"{format.ago(runner['created_at'], now)} \u00b7 enrollment token"),
        ],
        revocable=not revoked,
        drainable=not revoked and runner.get("drained_at") is None,
        runs=ordered,
    )


def runner_rows(runners: list[Row], now: int) -> list[RunnerRow]:
    rows = []
    for runner in runners:
        load, load_tone = _load(runner)
        revoked = runner["status"] == "revoked"
        rows.append(
            RunnerRow(
                id=runner["id"],
                name=runner["name"],
                status=runner["status"],
                tone=RUNNER_TONE[runner["status"]],
                slots=format.slots(runner["capacity"], len(runner["runs"])),
                lease=_lease(runner, now),
                percent=format.lease_percent(
                    runner["lease_acquired_at"], runner["lease_expires_at"], now
                ),
                heartbeat=format.heartbeat_ago(runner["last_heartbeat_at"], now),
                held=[HeldRow(seq=held["seq"], status=held["status"]) for held in runner["runs"]],
                load=load,
                load_tone=load_tone,
                lease_bar=not revoked,
                seen=format.DASH if revoked else format.ago(runner["last_heartbeat_at"], now),
                rotates=_rotates(runner, now, format.coarse),
                revocable=not revoked,
            )
        )
    return rows


def pick(runners: list[Row], state: str, query: str) -> list[Row]:
    needle = query.strip().lower()
    return [
        runner
        for runner in runners
        if (state == "all" or runner["status"] == state)
        and (not needle or needle in runner["name"].lower() or needle in runner["id"].lower())
    ]


def fleet(runners: list[Row]) -> Summary:
    counts = {status: 0 for status in STATE_NOTE}
    for runner in runners:
        counts[runner["status"]] += 1
    live = [runner for runner in runners if runner["status"] == "live"]
    slots = sum(runner["capacity"] or 0 for runner in live)
    busy = sum(len(runner["runs"]) for runner in live)
    return Summary(
        subtitle=(
            f"{len(runners)} enrolled \u00b7 {counts['live']} live \u00b7 "
            f"{busy} of {slots} slots busy"
        ),
        values={
            **{
                status: TileValue(str(count), STATE_NOTE[status])
                for status, count in counts.items()
            },
            "slots_busy": TileValue(f"{busy} / {slots}", "across live runners"),
        },
    )


@dataclass(frozen=True)
class Step:
    name: str
    mark: str
    at: str


@dataclass(frozen=True)
class RunRunnerRow:
    id: str
    name: str
    tone: Tone
    slots: str
    percent: int
    lease: str
    facts: list[Fact]


@dataclass(frozen=True)
class RunDetailRow:
    id: str
    seq: int
    status: str
    tone: Tone
    workspace: str
    image: str
    runner: str
    runner_tone: Tone | None
    started: str
    elapsed: str
    attempt: str
    stoppable: bool
    profile_id: str | None
    run: list[Fact]
    policy: list[Fact]
    holder: RunRunnerRow | None
    steps: list[Step]


def _size(mib: int) -> str:
    return f"{mib // 1024} GiB" if mib % 1024 == 0 else f"{mib} MiB"


def _image(ref: Row, images: list[Row]) -> str:
    found = next(
        (row for row in images if row["id"] == ref["id"] and row["digest"] == ref["digest"]), None
    )
    tail = f"v{found['version']}" if found else ref["digest"].removeprefix("sha256:")[:12]
    return f"{ref['id']} \u00b7 {tail}"


def _state(run: Row) -> str:
    if run["status_reason"]:
        return f"{run['status']} \u00b7 {run['status_reason']}"
    if run["runner"]:
        return f"{run['status']} \u00b7 claimed by {run['runner']['name']}"
    return str(run["status"])


def _egress(policy: Row | None) -> str:
    if policy is None:
        return "no policy"
    hosts = len(policy["document"].get("allow", []))
    return f"allowlist \u00b7 {hosts} host{'s' if hosts != 1 else ''}"


# Only the credential names are counted; a value never reaches the web.
def _secrets(policy: Row | None) -> str:
    servers = policy["document"].get("servers", []) if policy else []
    bound = len({server["credential"] for server in servers if server.get("credential")})
    return f"{bound} bound \u00b7 never logged" if bound else "none bound"


def _fencing(run: Row, runner: Row | None) -> str:
    if not run.get("lease_id"):
        return "off \u00b7 no lease held"
    if runner is None or runner["lease_acquired_at"] is None or runner["lease_expires_at"] is None:
        return "on"
    return f"on \u00b7 {runner['lease_expires_at'] - runner['lease_acquired_at']}s ttl"


def _reached(events: list[Row]) -> dict[str, int]:
    reached: dict[str, int] = {}
    for event in events:
        if event["event"] == "run_created":
            reached["PENDING"] = event["at"]
        elif event["event"] == "run_transition":
            reached[str(event["data"]["to"])] = event["at"]
    return reached


# FAILED and CANCELLED leave the path, so the timeline ends on them after the last step reached.
def _steps(run: Row, events: list[Row], now: int) -> list[Step]:
    reached = _reached(events)
    status = run["status"]
    current = LIFECYCLE.index(status) if status in LIFECYCLE else -1
    steps = []
    for index, name in enumerate(LIFECYCLE):
        if name == status:
            steps.append(Step(name, "current", "current"))
        elif name in reached and (current < 0 or index < current):
            steps.append(Step(name, "done", format.ago(reached[name], now)))
        else:
            steps.append(Step(name, "future", format.DASH))
    if current < 0:
        last = max((i for i, s in enumerate(steps) if s.mark == "done"), default=-1)
        steps = [*steps[: last + 1], Step(status, "exit", format.ago(run["finished_at"], now))]
    return steps


def _holder(runner: Row, now: int) -> RunRunnerRow:
    return RunRunnerRow(
        id=runner["id"],
        name=runner["name"],
        tone=RUNNER_TONE[runner["status"]],
        slots=format.slots(runner["capacity"], len(runner["runs"])),
        percent=format.lease_percent(runner["lease_acquired_at"], runner["lease_expires_at"], now),
        lease=f"{_lease(runner, now)} \u00b7 renewed on every heartbeat",
        facts=[
            Fact("Host", (runner.get("placement") or {}).get("host") or format.DASH),
            Fact("Address", (runner.get("placement") or {}).get("address") or format.DASH),
            Fact("Heartbeat", format.ago(runner["last_heartbeat_at"], now)),
            Fact("Token", _token(runner, now)),
        ],
    )


def run_detail(
    run: Row,
    events: list[Row],
    runner: Row | None,
    policies: dict[str, Row],
    images: list[Row],
    profile: Row | None,
    now: int,
) -> RunDetailRow:
    spec = run["spec"]
    runtime = spec["runtime"]
    started = format.ago(run["started_at"], now)
    elapsed = format.duration(run["started_at"], run["finished_at"], now)
    merge = spec["merge"]["policy"]
    size = f"{runtime['cpu']} vCPU \u00b7 {_size(runtime['memory_mib'])}"
    return RunDetailRow(
        id=run["id"],
        seq=run["seq"],
        status=run["status"],
        tone=STATUS_TONE[run["status"]],
        workspace=run["workspace"] or format.DASH,
        image=spec["image"]["id"],
        runner=run["runner"]["name"] if run["runner"] else "unassigned",
        runner_tone=RUNNER_TONE[runner["status"]] if runner and run["runner"] else None,
        started=f"started {started}" if run["started_at"] else "not started",
        elapsed=elapsed,
        attempt=f"attempt {format.DASH}",
        stoppable=run["status"] in STOPPABLE,
        profile_id=run.get("profile_id"),
        run=[
            Fact("Run id", run["id"], mono=True),
            Fact("State", _state(run)),
            Fact("Spec", f"{run['workspace'] or format.DASH} \u00b7 {spec['image']['id']}"),
            Fact("Image", _image(spec["image"], images), mono=True),
            Fact("Profile", f"{profile['name']} \u00b7 {size}" if profile else size),
            Fact(
                "Started",
                f"{started} \u00b7 {elapsed} elapsed" if run["started_at"] else format.DASH,
            ),
            Fact("Attempt", format.DASH),
        ],
        policy=[
            Fact("Merge policy", f"{merge} \u00b7 {MERGE_MEANING[merge]}"),
            Fact("Approvals", format.DASH),
            Fact("Timeouts", f"run {format.coarse(spec['timeout'])} \u00b7 idle {format.DASH}"),
            Fact("Network egress", _egress(policies.get("network"))),
            Fact("Secrets", _secrets(policies.get("mcp"))),
            Fact("Artifacts", format.DASH),
            Fact("Lease fencing", _fencing(run, runner)),
        ],
        holder=_holder(runner, now) if runner else None,
        steps=_steps(run, events, now),
    )
