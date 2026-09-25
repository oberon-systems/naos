from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import quote

Tone = Literal["blue", "green", "amber", "red", "grey", "faint"]


@dataclass(frozen=True)
class Tile:
    key: str
    label: str
    tone: Tone
    qualifier: str = ""


@dataclass(frozen=True)
class Action:
    label: str
    href: str
    overlay: bool = False


@dataclass(frozen=True)
class Chip:
    label: str
    tone: Tone = "grey"


@dataclass(frozen=True)
class ListPage:
    key: str
    title: str
    href: str
    tiles: tuple[Tile, ...] = ()
    action: Action | None = None
    chip: Chip | None = None
    header: bool = True


NAV: tuple[ListPage, ...] = (
    ListPage(
        key="runs",
        title="Runs",
        href="/runs",
        tiles=(
            Tile("active", "Active runs", "blue"),
            Tile("queued", "Queued", "grey"),
            Tile("waiting_merge", "Waiting merge", "amber"),
            Tile("failed", "Failed", "red", qualifier="24h"),
        ),
        header=False,
    ),
    ListPage(
        key="runners",
        title="Runners",
        href="/runners",
        tiles=(
            Tile("live", "Live", "green"),
            Tile("stale", "Stale", "amber"),
            Tile("revoked", "Revoked", "red"),
            Tile("slots_busy", "Slots busy", "blue"),
        ),
        action=Action("Enroll runner", "/runners/enroll"),
        chip=Chip("enrollment open", "green"),
    ),
    ListPage(
        key="images",
        title="Images",
        href="/images",
        tiles=(
            Tile("registered", "Registered", "blue"),
            Tile("in_use", "In use", "green"),
            Tile("unused", "Unused", "faint"),
            Tile("catalog_size", "Catalog size", "grey"),
        ),
        action=Action("Register image", "/images/register", overlay=True),
        chip=Chip("catalog is append-only", "grey"),
    ),
    ListPage(
        key="profiles",
        title="Profiles",
        href="/profiles",
        tiles=(
            Tile("profiles", "Profiles", "blue"),
            Tile("policies", "Policies", "green"),
            Tile("secrets", "Secrets", "amber"),
            Tile("runs_24h", "Runs 24h", "grey"),
        ),
        action=Action("New profile", "/profiles/new", overlay=True),
        chip=Chip("a Run copies, never references", "grey"),
    ),
    ListPage(
        key="audit",
        title="Audit",
        href="/audit",
        tiles=(
            Tile("events_24h", "Events 24h", "blue"),
            Tile("gate_denials", "Gate denials", "red"),
            Tile("refused", "Refused", "green"),
            Tile("spool_lag", "Spool lag", "amber"),
        ),
        action=Action("Export", "/audit/export"),
        chip=Chip("live tail", "green"),
    ),
)

PAGES: dict[str, ListPage] = {page.key: page for page in NAV}


@dataclass
class TileValue:
    number: str = ""
    note: str = ""


@dataclass
class Summary:
    subtitle: str = ""
    values: dict[str, TileValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Filter:
    key: str
    label: str

    @property
    def href(self) -> str:
        return "/runs" if self.key == "all" else f"/runs?state={self.key}"


RUN_FILTERS: tuple[Filter, ...] = (
    Filter("all", "All"),
    Filter("active", "Active"),
    Filter("queued", "Queued"),
    Filter("waiting_merge", "Waiting merge"),
    Filter("failed", "Failed"),
)

STATUS_TONE: dict[str, Tone] = {
    "PENDING": "grey",
    "STARTING": "blue",
    "STARTED": "blue",
    "STOPPING": "blue",
    "COLLECTING": "blue",
    "WAITING_MERGE": "amber",
    "COMPLETED": "green",
    "FAILED": "red",
    "CANCELLED": "grey",
}

RUNNER_TONE: dict[str, Tone] = {"live": "green", "stale": "amber", "revoked": "red"}


@dataclass(frozen=True)
class SearchFilter:
    key: str
    label: str
    path: str

    def href(self, query: str) -> str:
        params = [] if self.key == "all" else [f"state={self.key}"]
        params += [f"q={quote(query)}"] if query else []
        return self.path + ("?" + "&".join(params) if params else "")


RUNNER_FILTERS: tuple[SearchFilter, ...] = (
    SearchFilter("all", "All", "/runners"),
    SearchFilter("live", "Live", "/runners"),
    SearchFilter("stale", "Stale", "/runners"),
    SearchFilter("revoked", "Revoked", "/runners"),
)
RUNNER_COLUMNS = ("RUNNER", "SLOTS", "LEASE", "HEARTBEAT", "ROTATES IN", "")
RUNNER_NOTE = (
    "Tokens are never shown here \u2014 the API keeps only their SHA-256, "
    "and a heartbeat past half the lifetime returns a replacement."
)
LEASE_NOTE = "renewed on every heartbeat \u00b7 expiry fences the runner's VMs"

IMAGE_FILTERS: tuple[SearchFilter, ...] = (
    SearchFilter("all", "All", "/images"),
    SearchFilter("in_use", "In use", "/images"),
    SearchFilter("unused", "Unused", "/images"),
)
IMAGE_COLUMNS = ("IMAGE", "VERSION", "DIGEST", "SOURCE", "REGISTERED", "USAGE")
IMAGE_NOTE = (
    "An image row is never rewritten or deleted \u2014 a new build is a new version, "
    "and a Run pins the digest it booted."
)
SOURCE_NOTE = (
    "The runner downloads the file itself and trusts this digest, not the host that served "
    "it. https only, no credentials, and the API never contacts the url."
)

PROFILE_FILTERS: tuple[SearchFilter, ...] = (
    SearchFilter("all", "All", "/profiles"),
    SearchFilter("used", "Used", "/profiles"),
    SearchFilter("unused", "Unused", "/profiles"),
)
PROFILE_COLUMNS = ("PROFILE", "RUNTIME", "POLICIES", "MERGE", "TIMEOUT", "RUNS", "")
PROFILE_NOTE = (
    "A profile is only a starting point: creating a Run copies these values into a spec "
    "that never changes again, so editing a profile leaves every started Run alone."
)
COPY_NOTE = (
    "Creating a Run copies these values into its own spec, which never changes again "
    "\u2014 an edit here never reaches a Run that has started."
)
POLICIES_NOTE = (
    "Documents as the API resolved them. A secret appears by name only; "
    "its value never leaves the API."
)
OPEN_NOTE = (
    "Edit is refused while a run from this profile is open. "
    "Every run ever launched from it is in Runs."
)
FORM_NOTE = (
    "A Run copies these values when it is created. An edit later never reaches a Run "
    "that has started."
)
GATES_NOTE = (
    "Every gate starts closed: none allows nothing until you pick a policy. "
    "An MCP policy names its secrets; their values never reach this form."
)


@dataclass(frozen=True)
class RowAction:
    label: str
    href: str
    post: bool = False
    confirm: str = ""


# Cancel is the one action that changes a Run, so it asks first; the api authorizes it either way.
ROW_ACTIONS: dict[str, RowAction] = {
    "PENDING": RowAction("Cancel", "/runs/{id}/cancel", post=True, confirm="Cancel run #{seq}?"),
    "WAITING_MERGE": RowAction("Review", "/runs/{id}"),
    "COMPLETED": RowAction("Diff", "/runs/{id}"),
    "FAILED": RowAction("Logs", "/runs/{id}/logs"),
}
OPEN_ACTION = RowAction("Open", "/runs/{id}")

RUN_COLUMNS = ("RUN", "STATUS", "SPEC", "RUNNER", "STARTED", "DURATION", "")

# The lifecycle of AGENTS.md, which the api enforces and this footer only states.
LIFECYCLE: tuple[str, ...] = (
    "PENDING",
    "STARTING",
    "STARTED",
    "STOPPING",
    "COLLECTING",
    "WAITING_MERGE",
    "COMPLETED",
)
EXITS: tuple[tuple[str, str], ...] = (
    ("any state before COMPLETED", "FAILED"),
    ("PENDING", "CANCELLED"),
)
FENCING = "an expired lease fences its runs"

FINISHED = frozenset({"COMPLETED", "FAILED", "CANCELLED"})

# The states POST /runs/{id}/stop moves; the api answers the rest with the Run unchanged.
STOPPABLE = frozenset({"PENDING", "STARTING", "STARTED"})
MERGE_MEANING: dict[str, str] = {
    "ask": "manual approval",
    "always": "automatic unless sensitive",
    "never": "workspace untouched",
}
