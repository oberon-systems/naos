from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import quote

Tone = Literal["blue", "green", "amber", "red", "grey", "faint", "violet"]


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
        key="policies",
        title="Policies",
        href="/policies",
        tiles=(
            Tile("mount", "Mount", "blue"),
            Tile("network", "Network", "green"),
            Tile("shell", "Shell", "amber"),
            Tile("mcp", "MCP", "grey"),
            Tile("model", "Model", "violet"),
        ),
        action=Action("New policy", "/policies/new", overlay=True),
        chip=Chip("immutable \u00b7 a new document is a new id", "blue"),
    ),
    ListPage(
        key="secrets",
        title="Secrets",
        href="/secrets",
        tiles=(
            Tile("secrets", "Secrets", "blue"),
            Tile("in_use", "In use", "green"),
            Tile("expiring", "Expiring", "amber"),
            Tile("held", "Held by Runs", "grey"),
        ),
        action=Action("New secret", "/secrets/_new", overlay=True),
        chip=Chip("values are never shown", "blue"),
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
    param: str = "state"

    def href(self, query: str) -> str:
        params = [] if self.key == "all" else [f"{self.param}={self.key}"]
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
POLICY_FILTERS: tuple[SearchFilter, ...] = (
    SearchFilter("all", "All", "/policies", "kind"),
    SearchFilter("mount", "Mount", "/policies", "kind"),
    SearchFilter("network", "Network", "/policies", "kind"),
    SearchFilter("shell", "Shell", "/policies", "kind"),
    SearchFilter("mcp", "MCP", "/policies", "kind"),
    SearchFilter("model", "Model", "/policies", "kind"),
)
POLICY_COLUMNS = ("POLICY", "KIND", "DOCUMENT", "USED BY", "CREATED", "")
POLICY_NOTE = (
    "A policy never changes: the API stores the resolved document once, an equivalent "
    "document returns the same id, and a Run keeps a snapshot of what it started with."
)
SECRET_FILTERS: tuple[SearchFilter, ...] = (
    SearchFilter("all", "All", "/secrets"),
    SearchFilter("valid", "Valid", "/secrets"),
    SearchFilter("expiring", "Expiring", "/secrets"),
    SearchFilter("expired", "Expired", "/secrets"),
    SearchFilter("used", "Used", "/secrets"),
    SearchFilter("unused", "Unused", "/secrets"),
)
SECRET_COLUMNS = ("SECRET", "STATE", "EXPIRES", "USED BY", "ROTATED", "HELD BY", "")
TYPED_ONCE_NOTE = (
    "A value is typed once and never shown again. Rotate keeps the name; the next issue to a "
    "runner carries the new value. Delete is refused while a server, a policy or an open Run "
    "names the secret."
)
WRITE_ONLY_NOTE = (
    "The value is write-only: typed once, sent to the API and never shown again \u2014 not "
    "here, not in a log, not in the audit. Rotate replaces it and keeps the name."
)
NAMED_NOTE = (
    "A credential is put in place by naos and never listed to agents. A row opens its policy."
)
HELD_NOTE = (
    "A Run holds the value from issue until it leaves STARTED. Delete is refused meanwhile; "
    "every Run it was ever issued to is in Used by."
)
ISSUED_NOTE = (
    "A Run is listed once the secret was issued to it. It holds the value until it leaves "
    "STARTED; a finished Run stays listed."
)
SENT_ONCE_NOTE = (
    "The value is sent once and never shown again: not in this form, a redirect, an error or a log."
)
ROTATE_NOTE = (
    "The name stays. The next issue to a runner carries the new value; a Run already holding "
    "the old one keeps it until its credential TTL ends."
)
EXPIRY_NOTE = "Changes expires_at only. The value is not touched and nothing is sent to a runner."
NEVER_NOTE = (
    "The secret stays valid until someone rotates it, sets a term or deletes it. A Run still "
    "gets it only for the credential TTL. No keeps the previous choice."
)
ERASED_NOTE = (
    "The value is erased from the API and cannot be brought back. Its audit events stay; "
    "none of them ever carried the value."
)
IDENTITY_NOTE = (
    "A policy is never edited or deleted. A different document is a new policy with its own id."
)
SECRETS_NOTE = "Names only. Credentials are issued to the runner per Run and never shown here."
BUDGET_NOTE = (
    "Each Run spends its own budget. A call is refused once either side is spent; calls in "
    "flight may overshoot it."
)
USED_NOTE = (
    "A Run keeps the snapshot it started with, so it stays listed here even after its "
    "profile moves to another policy."
)
POLICY_FORM_NOTE = (
    "A policy never changes once created. The same document returns the policy that "
    "already holds it."
)
POLICY_FORM_HINTS = {
    "mount": "A host path is absolute, normalized and inside one of the roots the API allows; "
    "anything else is refused with 422.",
    "network": "A rule sets at least one of protocol, host or ip. Hosts are lowercased and lose "
    "a trailing dot; an address goes in ip, never in host; localhost is refused.",
    "shell": "At least one capability. The gate serves only the paths the Run's mount policy "
    "names, whatever this list grants.",
    "mcp": "A rule names a tool, or * for every tool of the server, or a resource prefix. "
    "A call no rule allows is denied and a matching deny wins; shell and network are servers too.",
    "model": "Credential is the name of a secret, never its value. A model belongs to one "
    "provider. The budget is for one whole Run.",
}
WORKSPACE_HINT = (
    "Mounted at /naos/<last segment of the host path> and used as the workdir. rw writes land "
    "on the upper disk and reach the host only through a merge."
)
EXISTS_NOTE = (
    "This document resolves to a policy the API already holds, so nothing new was created. "
    "Equivalent documents share one digest and one id; pick that id in a profile."
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
    "An MCP or a model policy names its secrets; their values never reach this form."
)


@dataclass(frozen=True)
class RowAction:
    label: str
    href: str
    post: bool = False
    confirm: str = ""
    overlay: bool = False


# Cancel is the one action that changes a Run, so it asks first; the api authorizes it either way.
ROW_ACTIONS: dict[str, RowAction] = {
    "PENDING": RowAction("Cancel", "/runs/{id}/cancel", post=True, confirm="Cancel run #{seq}?"),
    "WAITING_MERGE": RowAction("Review", "/runs/{id}/changes", overlay=True),
    "COMPLETED": RowAction("Diff", "/runs/{id}/changes", overlay=True),
    "FAILED": RowAction("Logs", "/runs/{id}/logs"),
}
OPEN_ACTION = RowAction("Open", "/runs/{id}", overlay=True)

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
