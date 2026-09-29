from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import urlencode

from naos_web import format
from naos_web.client import Row
from naos_web.pages import Tone

Filter = Literal[
    "all",
    "created",
    "modified",
    "deleted",
    "renamed",
    "rejected",
    "sensitive",
    "applied",
    "exported",
    "backed_up",
    "left_out",
]
State = Literal["waiting", "sent", "conflicts", "merged"]
Resolution = Literal["skip", "take", "export"]
Choice = Literal["apply", "skip", "take", "export"]
Select = Literal["all", "none"]
Action = Literal["merge", "again", "applying", "none"]

FILTERS: tuple[tuple[Filter, str], ...] = (
    ("all", "All"),
    ("created", "Created"),
    ("modified", "Modified"),
    ("deleted", "Deleted"),
    ("renamed", "Renamed"),
    ("rejected", "Rejected"),
    ("sensitive", "Sensitive"),
)
MERGED_FILTERS: tuple[tuple[Filter, str], ...] = (
    ("all", "All"),
    ("applied", "Applied"),
    ("exported", "Exported"),
    ("backed_up", "Backed up"),
    ("left_out", "Left out"),
)
RESOLUTIONS: dict[str, Resolution] = {"skip": "skip", "take": "take", "export": "export"}
CHOICES: dict[str, Choice] = {"apply": "apply", "skip": "skip", "take": "take", "export": "export"}
SELECTS: dict[str, Select] = {"all": "all", "none": "none"}
ON_MERGE: tuple[tuple[Choice, str], ...] = (
    ("apply", "Apply"),
    ("skip", "Skip"),
    ("export", "Export"),
)
ON_CONFLICT: tuple[tuple[Choice, str], ...] = (
    ("skip", "Skip"),
    ("take", "Take"),
    ("export", "Export"),
)
CHANGE_TONE: dict[str, Tone] = {
    "created": "green",
    "modified": "blue",
    "deleted": "red",
    "renamed": "violet",
    "rejected": "grey",
}
LIST_NOTE = (
    "Sorted by path bytes, as collected. File contents stay on the upper disk; "
    "a change is judged by size, mode and sha256."
)
ENTRY_NOTE = (
    "The base is the host file at collection time; the merge applies only while the host "
    "still matches it. Export leaves the host alone and writes the agent's version to "
    "merge/export/."
)
SENSITIVE_NOTE = (
    "These run on the host or in CI once merged. They are never selected for you, even under "
    "merge policy always \u2014 tick each one yourself."
)
REJECTED_NOTE = "A rejected object never reaches the host and cannot be selected."
CONFLICT_NOTES = (
    "Skip \u2014 the host keeps its version; the agent's change is dropped.",
    "Take \u2014 the agent's version is applied; the host's current one moves to merge/backup/.",
    "Export \u2014 the host is left alone; the agent's version is written to merge/export/.",
)
REPORT_NOTE = (
    "As the runner reported it. The decision and the report are in the run's Logs & Audit."
)
WHERE_NOTE = (
    "Both stay in the runner's VM archive with the upper disk and diff.json. Nothing removes "
    "the archive yet; an operator deletes it once no longer needed."
)
SENT_NOTE = "another decision is refused with 409 while this one is pending"
CLEARED_NOTE = "the previous decision was cleared by the conflict"
KIB = 1024


@dataclass(frozen=True)
class Banner:
    tone: Tone
    title: str
    text: str


@dataclass(frozen=True)
class EntryRow:
    index: int
    path: str
    change: str
    tone: Tone
    source: str | None
    detail: str
    sensitive: bool
    rejected: bool
    selected: bool = False
    conflict: str | None = None
    resolution: Choice | None = None
    outcome: str | None = None
    outcome_tone: Tone = "grey"


@dataclass(frozen=True)
class Fact:
    label: str
    value: str
    mono: bool = False


@dataclass(frozen=True)
class Picked:
    index: int
    path: str
    change: str
    tone: Tone
    facts: list[Fact]


@dataclass(frozen=True)
class Conflict:
    index: int
    path: str
    reason: str
    position: int
    total: int
    facts: list[Fact]
    resolution: Choice | None


@dataclass(frozen=True)
class Asked:
    paths: tuple[str, ...] = ()
    resolutions: tuple[tuple[str, Resolution], ...] = ()
    touched: bool = False
    toggle: str | None = None
    select: Select | None = None
    resolve: str | None = None
    to: Choice | None = None


@dataclass(frozen=True)
class Selection:
    paths: frozenset[str]
    resolutions: dict[str, Resolution]

    def fields(self) -> list[tuple[str, str]]:
        picked = [("path", path) for path in sorted(self.paths)]
        return picked + [(kind, path) for path, kind in sorted(self.resolutions.items())]

    def query(self) -> str:
        return urlencode([("touched", "1"), *self.fields()])


@dataclass(frozen=True)
class Bar:
    title: str
    text: str
    action: Action
    count: int
    ready: bool


@dataclass(frozen=True)
class Where:
    runner: str
    runner_id: str | None
    archive: str


@dataclass(frozen=True)
class Changes:
    base: str
    state: State
    label: str
    banner: Banner
    filters: tuple[tuple[Filter, str], ...]
    counts: dict[Filter, int]
    shown: Filter
    focus: int | None
    rows: list[EntryRow]
    picked: Picked | None
    conflict: Conflict | None
    sensitive: list[EntryRow]
    sensitive_left: int
    rejected: list[EntryRow]
    selection: Selection
    bar: Bar
    report: list[Fact]
    where: Where | None
    error: str | None

    @property
    def live(self) -> bool:
        return self.state in ("waiting", "conflicts")

    # A filter link drops the entry, a row link keeps the filter, an action keeps both.
    def link(self, change: Filter | None = None, entry: int | None = None, **act: str) -> str:
        shown = self.shown if change is None else change
        index = None if change is not None else (self.focus if entry is None else entry)
        query: list[tuple[str, str]] = [("change", shown)] if shown != "all" else []
        if index is not None:
            query.append(("entry", str(index)))
        query.extend(act.items())
        return self.base + ("?" + urlencode(query) if query else "")


def size(value: int) -> str:
    return f"{value} B" if value < KIB else f"{value / KIB:.1f} KiB"


def _mode(value: int) -> str:
    return f"{value:04o}"


def _digest(value: str) -> str:
    return f"{value[:4]}\u2026{value[-4:]}"


def _detail(entry: Row) -> str:
    if entry["change"] == "rejected":
        return str(entry.get("reason") or "rejected")
    if entry["kind"] == "symlink" and entry.get("target") is not None:
        return f"\u2192 {entry['target']}"
    if entry["kind"] == "dir":
        return "dir"
    if entry.get("size") is not None:
        return size(entry["size"])
    return ""


def _row(index: int, entry: Row) -> EntryRow:
    return EntryRow(
        index=index,
        path=entry["path"],
        change=entry["change"],
        tone=CHANGE_TONE[entry["change"]],
        source=entry.get("from"),
        detail=_detail(entry),
        sensitive=bool(entry.get("sensitive")),
        rejected=entry["change"] == "rejected",
    )


def _matches(row: EntryRow, shown: Filter) -> bool:
    if shown == "all":
        return True
    if shown == "sensitive":
        return row.sensitive
    if shown in ("applied", "exported"):
        return row.outcome is not None and row.outcome.startswith(shown)
    if shown == "backed_up":
        return row.outcome is not None and row.outcome.endswith("backed up")
    if shown == "left_out":
        return row.outcome in ("left out", "rejected")
    return row.change == shown


def _picked(index: int, entry: Row) -> Picked:
    facts = [Fact("Kind", entry["kind"])]
    if entry.get("size") is not None:
        facts.append(Fact("Size", size(entry["size"])))
    if entry.get("mode") is not None:
        base = entry.get("base_mode")
        facts.append(
            Fact(
                "Mode",
                _mode(entry["mode"]) + ("" if base is None else f" \u00b7 base {_mode(base)}"),
                mono=True,
            )
        )
    if entry.get("sha256") is not None:
        facts.append(Fact("sha256", _digest(entry["sha256"]), mono=True))
    if entry.get("base_sha256") is not None:
        facts.append(Fact("Base sha256", _digest(entry["base_sha256"]), mono=True))
    if entry.get("target") is not None:
        facts.append(Fact("Target", entry["target"], mono=True))
    if entry.get("base_target") is not None:
        facts.append(Fact("Base target", entry["base_target"], mono=True))
    return Picked(
        index=index,
        path=entry["path"],
        change=entry["change"],
        tone=CHANGE_TONE[entry["change"]],
        facts=facts,
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def banner(merge: Row, policy: str, workspace: str, runner: str, now: int) -> Banner:
    report, conflicts, decision = merge["report"], merge["conflicts"], merge["decision"]
    if report is not None:
        return Banner(
            "green",
            f"Merged into {workspace} \u00b7 {len(report['applied'])} applied \u00b7 "
            f"{len(report['exported'])} exported \u00b7 {len(report['backed_up'])} backed up",
            "Every replaced or removed host entry was moved to the backup, never deleted. "
            "The run completed when the runner reported the merge.",
        )
    if conflicts:
        return Banner(
            "red",
            f"{_plural(len(conflicts), 'conflict')} \u00b7 nothing was written",
            "The runner compared every selected path with the host before writing and stopped: "
            "the host changed since collection. Resolve each conflict and send the decision "
            "again.",
        )
    if decision is not None:
        return Banner(
            "blue",
            f"Decision sent \u00b7 runner {runner} is applying it",
            f"You selected {_plural(len(decision['paths']), 'path')} "
            f"{format.ago(merge['updated_at'], now)}. The selection is locked until the runner "
            "reports: either the merge applies and the run completes, or conflicts come back "
            "here with nothing written.",
        )
    return Banner(
        "amber",
        f"Waiting for your decision \u00b7 merge policy {policy}",
        f"The workspace {workspace} is untouched until you merge. Nothing on the host is "
        "deleted: a replaced or removed entry moves to merge/backup/.",
    )


def state(merge: Row) -> State:
    if merge["report"] is not None:
        return "merged"
    if merge["conflicts"]:
        return "conflicts"
    return "sent" if merge["decision"] is not None else "waiting"


def asked(pairs: Iterable[tuple[str, str]]) -> Asked:
    paths: list[str] = []
    resolutions: list[tuple[str, Resolution]] = []
    one: dict[str, str] = {}
    for key, value in pairs:
        if key == "path":
            paths.append(value)
        elif key in RESOLUTIONS:
            resolutions.append((value, RESOLUTIONS[key]))
        else:
            one[key] = value
    return Asked(
        paths=tuple(paths),
        resolutions=tuple(resolutions),
        touched=one.get("touched") == "1",
        toggle=one.get("toggle"),
        select=SELECTS.get(one.get("select", "")),
        resolve=one.get("resolve"),
        to=CHOICES.get(one.get("to", "")),
    )


@dataclass(frozen=True)
class _Graph:
    live: frozenset[str]
    sensitive: frozenset[str]
    needs: dict[str, frozenset[str]]
    needed_by: dict[str, set[str]]


# The same rules the api checks a selection with, so a tick never builds one it refuses.
def _graph(entries: list[Row]) -> _Graph:
    live = [entry for entry in entries if entry["change"] != "rejected"]
    created_dirs = {
        entry["path"] for entry in live if entry["change"] == "created" and entry["kind"] == "dir"
    }
    removed = [
        (entry["from"] if entry["change"] == "renamed" else entry["path"], entry["path"])
        for entry in live
        if entry["change"] in ("deleted", "renamed")
    ]
    needs: dict[str, frozenset[str]] = {}
    needed_by: dict[str, set[str]] = {}
    for entry in live:
        path = entry["path"]
        wanted: set[str] = set()
        parent = path.rpartition("/")[0]
        if parent in created_dirs:
            wanted.add(parent)
        if entry["change"] == "deleted" and entry["kind"] == "dir":
            prefix = f"{path}/"
            wanted.update(owner for host, owner in removed if host.startswith(prefix))
        needs[path] = frozenset(wanted)
        for other in wanted:
            needed_by.setdefault(other, set()).add(path)
    return _Graph(
        live=frozenset(needs),
        sensitive=frozenset(entry["path"] for entry in live if entry.get("sensitive")),
        needs=needs,
        needed_by=needed_by,
    )


def _with(graph: _Graph, chosen: set[str], path: str) -> set[str]:
    added: set[str] = set()
    stack = [path]
    while stack:
        current = stack.pop()
        if current in chosen or current in added:
            continue
        added.add(current)
        stack.extend(graph.needs[current])
    if (added - {path}) & graph.sensitive:
        return chosen
    return chosen | added


def _without(graph: _Graph, chosen: set[str], path: str) -> set[str]:
    dropped: set[str] = set()
    stack = [path]
    while stack:
        current = stack.pop()
        if current not in dropped:
            dropped.add(current)
            stack.extend(graph.needed_by.get(current, ()))
    return chosen - dropped


def _closed(graph: _Graph, candidates: set[str]) -> set[str]:
    chosen = set(candidates)
    while broken := {path for path in chosen if not graph.needs[path] <= chosen}:
        chosen -= broken
    return chosen


# A sent decision locks the selection; before that the form carries it, or the default does.
def selection(merge: Row, ask: Asked) -> Selection:
    decision = merge["decision"]
    if decision is not None:
        return Selection(frozenset(decision["paths"]), dict(decision.get("resolutions") or {}))
    graph = _graph(merge["entries"])
    unasked = set(graph.live - graph.sensitive)
    chosen = {path for path in ask.paths if path in graph.live} if ask.touched else set()
    if not ask.touched or ask.select == "all":
        chosen = _closed(graph, chosen | unasked)
    elif ask.select == "none":
        chosen = set()
    toggle = ask.toggle
    if toggle is not None and toggle in graph.live:
        chosen = (
            _without(graph, chosen, toggle) if toggle in chosen else _with(graph, chosen, toggle)
        )
    resolutions = {path: kind for path, kind in ask.resolutions if path in chosen}
    if ask.resolve is not None and ask.resolve in chosen and ask.to is not None:
        if ask.to == "apply":
            resolutions.pop(ask.resolve, None)
        else:
            resolutions[ask.resolve] = ask.to
    return Selection(frozenset(chosen), resolutions)


def resolved(resolutions: dict[str, Resolution]) -> str:
    kinds = list(resolutions.values())
    return " · ".join(
        f"{kind} {kinds.count(kind)}" for kind in RESOLUTIONS.values() if kinds.count(kind)
    )


def _outcome(entry: Row, report: Row) -> tuple[str, Tone]:
    path = entry["path"]
    if entry["change"] == "rejected":
        return "rejected", "grey"
    if path in report["exported"]:
        return "exported", "violet"
    if path in report["skipped"]:
        return "skipped", "grey"
    if path in report["applied"]:
        backed = {path, entry.get("from")} & set(report["backed_up"])
        return ("applied · backed up" if backed else "applied"), "green"
    return "left out", "grey"


def _conflict(entry: Row, at: EntryRow, position: int, total: int, sel: Selection) -> Conflict:
    facts: list[Fact] = []
    if entry.get("base_sha256") is not None:
        facts.append(Fact("Base sha256", _digest(entry["base_sha256"]), mono=True))
    if entry.get("sha256") is not None:
        facts.append(Fact("Agent sha256", _digest(entry["sha256"]), mono=True))
    return Conflict(
        index=at.index,
        path=entry["path"],
        reason=at.conflict or "",
        position=position,
        total=total,
        facts=facts,
        resolution=sel.resolutions.get(entry["path"]),
    )


def _report(rows: list[EntryRow], report: Row) -> list[Fact]:
    left = [row for row in rows if row.outcome in ("left out", "rejected")]
    sensitive = sum(1 for row in left if row.sensitive)
    rejected = sum(1 for row in left if row.rejected)
    parts = [
        f"{count} {word}"
        for count, word in (
            (sensitive, "sensitive"),
            (rejected, "rejected"),
            (len(left) - sensitive - rejected, "not selected"),
        )
        if count
    ]
    return [
        Fact("Applied", str(len(report["applied"]))),
        Fact("Exported", str(len(report["exported"]))),
        Fact("Skipped", str(len(report["skipped"]))),
        Fact("Backed up", str(len(report["backed_up"]))),
        Fact("Left out", " · ".join([str(len(left)), ", ".join(parts)]) if parts else "0"),
    ]


@dataclass(frozen=True)
class Context:
    run_id: str
    workspace: str
    runner: str
    runner_id: str | None
    vm_id: str | None
    decided: Row | None
    now: int


def _bar(merge: Row, kind: State, rows: list[EntryRow], sel: Selection, ctx: Context) -> Bar:
    count = len(sel.paths)
    picked = _plural(count, "path")
    summary = resolved(sel.resolutions)
    if kind == "merged":
        decided = ctx.decided or {"actor": "operator", "at": None}
        when = "" if decided["at"] is None else f" {format.ago(decided['at'], ctx.now)}"
        actor = decided["actor"]
        return Bar(
            " · ".join([f"Decided by {actor}{when}", picked] + ([summary] if summary else [])),
            f"the runner reported the merge {format.ago(merge['updated_at'], ctx.now)}; "
            "the run completed",
            "none",
            count,
            False,
        )
    if kind == "sent":
        return Bar(
            f"Decision sent · {picked} · {summary or 'no resolutions'}",
            SENT_NOTE,
            "applying",
            count,
            False,
        )
    if kind == "conflicts":
        paths = [row.path for row in rows if row.conflict is not None]
        done = sum(1 for path in paths if path not in sel.paths or path in sel.resolutions)
        return Bar(
            f"{picked} selected · {done} of {_plural(len(paths), 'conflict')} resolved",
            " · ".join(([summary] if summary else []) + [CLEARED_NOTE]),
            "again",
            count,
            done == len(paths),
        )
    live = sum(1 for row in rows if not row.rejected)
    left = sum(1 for row in rows if row.sensitive and not row.selected)
    rejected = sum(1 for row in rows if row.rejected)
    parts = ([f"{left} sensitive left out"] if left else []) + (
        [f"{rejected} rejected"] if rejected else []
    )
    return Bar(
        f"{count} of {_plural(live, 'path')} selected",
        " · ".join([*parts, f"decision applies to {ctx.workspace}"]),
        "merge",
        count,
        count > 0,
    )


def _label(kind: State, merge: Row) -> str:
    if kind == "merged":
        return "Changes · merged"
    if kind == "conflicts":
        return f"Changes · {_plural(len(merge['conflicts']), 'conflict')}"
    return f"Changes · {len(merge['entries'])}"


# A rejected entry cannot be picked, so asking for one falls back to the first live entry.
def changes(
    merge: Row,
    shown: Filter,
    picked: int | None,
    banner_row: Banner,
    ctx: Context,
    ask: Asked | None = None,
    error: str | None = None,
) -> Changes:
    kind = state(merge)
    entries: list[Row] = merge["entries"]
    sel = selection(merge, ask or Asked())
    reasons = {row["path"]: row["reason"] for row in merge["conflicts"] or []}
    report = merge["report"]
    rows = []
    for index, entry in enumerate(entries):
        row = _row(index, entry)
        outcome: tuple[str | None, Tone] = (None, "grey")
        if report is not None:
            outcome = _outcome(entry, report)
        rows.append(
            replace(
                row,
                selected=row.path in sel.paths,
                conflict=reasons.get(row.path) if kind == "conflicts" else None,
                resolution=sel.resolutions.get(row.path),
                outcome=outcome[0],
                outcome_tone=outcome[1],
            )
        )
    filters = MERGED_FILTERS if kind == "merged" else FILTERS
    shown = shown if any(key == shown for key, _ in filters) else "all"
    live = [row.index for row in rows if not row.rejected]
    first = picked if picked in live else (live[0] if live else None)
    conflicting = [row for row in rows if row.conflict is not None]
    at = next((row for row in conflicting if row.index == picked), None) or (
        conflicting[0] if conflicting else None
    )
    conflict = (
        _conflict(entries[at.index], at, conflicting.index(at) + 1, len(conflicting), sel)
        if at is not None
        else None
    )
    sensitive = [row for row in rows if row.sensitive]
    return Changes(
        base=f"/runs/{ctx.run_id}/changes",
        state=kind,
        label=_label(kind, merge),
        banner=banner_row,
        filters=filters,
        counts={key: sum(1 for row in rows if _matches(row, key)) for key, _ in filters},
        shown=shown,
        focus=conflict.index if conflict is not None else first,
        rows=[row for row in rows if _matches(row, shown)],
        picked=(
            _picked(first, entries[first])
            if first is not None and kind in ("waiting", "sent")
            else None
        ),
        conflict=conflict,
        sensitive=sensitive,
        sensitive_left=sum(1 for row in sensitive if not row.selected),
        rejected=[row for row in rows if row.rejected],
        selection=sel,
        bar=_bar(merge, kind, rows, sel, ctx),
        report=_report(rows, report) if report is not None else [],
        where=(
            Where(ctx.runner, ctx.runner_id, f"archive/{ctx.vm_id or '<vm id>'}/")
            if kind == "merged"
            else None
        ),
        error=error,
    )
