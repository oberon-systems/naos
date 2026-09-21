from dataclasses import dataclass
from typing import Any

from naos_web import format
from naos_web.pages import (
    OPEN_ACTION,
    ROW_ACTIONS,
    RUNNER_TONE,
    STATUS_TONE,
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
