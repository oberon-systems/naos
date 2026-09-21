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
class EventRow:
    event: str
    detail: str
    at: str


@dataclass(frozen=True)
class RunnerDetailRow:
    id: str
    name: str
    status: str
    tone: Tone
    enrolled: str
    slots: str
    busy: str
    free: str
    percent: int
    lease: str
    lease_percent: int
    state: str
    heartbeat: str
    registered: str
    runs: list[RunRow]
    events: list[EventRow]


# What the audit row says beside its name, from the fields that event actually carries.
def _detail(event: Row) -> str:
    data = event["data"]
    for key in ("run_id", "lease_id", "names", "to", "reason"):
        if key in data and data[key]:
            value = data[key]
            return ", ".join(value) if isinstance(value, list) else str(value)
    return ""


def runner_detail(runner: Row, runs: list[Row], events: list[Row], now: int) -> RunnerDetailRow:
    held = {run["id"] for run in runner["runs"]}
    capacity = runner["capacity"]
    busy = len(runner["runs"])
    return RunnerDetailRow(
        id=runner["id"],
        name=runner["name"],
        status=runner["status"],
        tone=RUNNER_TONE[runner["status"]],
        enrolled=f"enrolled {format.ago(runner['created_at'], now)}",
        slots=format.slots(capacity, busy),
        busy=format.DASH if capacity is None else f"{busy} / {capacity} busy",
        free=format.DASH if capacity is None else f"{capacity - busy} free",
        percent=0 if not capacity else round(100 * busy / capacity),
        lease=_lease(runner, now),
        lease_percent=format.lease_percent(
            runner["lease_acquired_at"], runner["lease_expires_at"], now
        ),
        state=runner["status"].upper(),
        heartbeat=format.ago(runner["last_heartbeat_at"], now),
        registered=format.ago(runner["created_at"], now),
        runs=[row for row in run_rows([r for r in runs if r["id"] in held], [runner], now)],
        events=[
            EventRow(event=row["event"], detail=_detail(row), at=format.ago(row["at"], now))
            for row in events
        ],
    )


def runner_rows(runners: list[Row], now: int) -> list[RunnerRow]:
    return [
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
        )
        for runner in runners
    ]


@dataclass(frozen=True)
class Fact:
    label: str
    value: str
    mono: bool = False


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
            Fact("Host", format.DASH),
            Fact("Address", format.DASH),
            Fact("Heartbeat", format.ago(runner["last_heartbeat_at"], now)),
            Fact("Token", format.DASH),
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
