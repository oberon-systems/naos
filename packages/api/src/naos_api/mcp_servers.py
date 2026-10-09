from collections.abc import Collection, Sequence
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, StrictInt
from sqlmodel import Session, col, func, select, update

from naos_api import audit
from naos_api.errors import NotFoundError, PolicyError, ServerConflictError
from naos_api.lifecycle import TERMINAL, RunStatus
from naos_api.mcp import (
    BUILT_IN,
    RawUrl,
    ServerName,
    external_servers,
    granted_secrets,
    https_url,
)
from naos_api.models import AuditEvent, McpServer, Policy, Run
from naos_api.secrets import SecretName
from naos_api.spec import PolicyKind, StrictModel

Timeout = Annotated[StrictInt, Field(ge=1, le=45)]
Calls = Annotated[StrictInt, Field(ge=1, le=600)]
DAY = 86_400
ENTRY = ("url", "credential", "timeout_seconds", "max_calls_per_minute")


class ServerCreate(StrictModel):
    name: ServerName
    url: RawUrl
    credential: SecretName | None = None
    timeout_seconds: Timeout = 30
    max_calls_per_minute: Calls = 60


class ServerPatch(StrictModel):
    url: RawUrl | None = None
    credential: SecretName | None = None
    timeout_seconds: Timeout | None = None
    max_calls_per_minute: Calls | None = None


class ServerRun(BaseModel):
    run_id: str
    seq: int
    status: RunStatus
    current: bool


class LastCall(BaseModel):
    at: int
    tool: str
    resource: str
    decision: str


class LastFailure(BaseModel):
    id: str
    at: int
    category: str


class ServerCalls(BaseModel):
    today: int
    denied_today: int
    last: LastCall | None
    last_failure: LastFailure | None


class ServerView(BaseModel):
    name: str
    kind: Literal["built-in", "external"]
    id: str | None = None
    url: str | None = None
    credential: str | None = None
    timeout_seconds: int | None = None
    max_calls_per_minute: int | None = None
    disabled_at: int | None = None
    created_at: int | None = None
    updated_at: int | None = None
    policies: list[str] = []
    runs: list[ServerRun] = []
    calls: ServerCalls | None = None


def _find(session: Session, name: str) -> McpServer | None:
    return session.exec(select(McpServer).where(col(McpServer.name) == name)).first()


def _external(session: Session, name: str) -> McpServer:
    if name in BUILT_IN:
        raise ServerConflictError(f"mcp server {name} is built-in and cannot change")
    server = _find(session, name)
    if server is None:
        raise NotFoundError(f"mcp server {name} does not exist")
    return server


# Policy documents are JSON, so the servers they name are read in Python, not queried.
def _naming(session: Session) -> dict[str, list[str]]:
    naming: dict[str, list[str]] = {}
    statement = select(Policy).where(col(Policy.kind) == PolicyKind.MCP).order_by(col(Policy.id))
    for policy in session.exec(statement).all():
        for name in sorted({rule["server"] for rule in policy.document["rules"]}):
            naming.setdefault(name, []).append(policy.id)
    return naming


# A Run holds an external server its copy lists, and a built-in one a rule allows.
def _holding(session: Session) -> dict[str, list[ServerRun]]:
    entries = {server.name: server for server in session.exec(select(McpServer)).all()}
    statement = select(Run).where(
        col(Run.status).not_in(TERMINAL), col(Run.mcp_document).is_not(None)
    )
    holding: dict[str, list[ServerRun]] = {}
    for run in session.exec(statement.order_by(col(Run.seq))).all():
        document = run.mcp_document or {}
        for held in document["servers"]:
            entry = entries.get(held["name"])
            current = entry is not None and all(held[key] == getattr(entry, key) for key in ENTRY)
            holder = ServerRun(run_id=run.id, seq=run.seq, status=run.status, current=current)
            holding.setdefault(held["name"], []).append(holder)
        allowed = {rule["server"] for rule in document["rules"] if rule["effect"] == "allow"}
        for name in BUILT_IN:
            if name in allowed:
                holder = ServerRun(run_id=run.id, seq=run.seq, status=run.status, current=True)
                holding.setdefault(name, []).append(holder)
    return holding


def _views(
    servers: Sequence[McpServer],
    naming: dict[str, list[str]],
    holding: dict[str, list[ServerRun]],
) -> list[ServerView]:
    return [
        ServerView(
            kind="external",
            policies=naming.get(server.name, []),
            runs=holding.get(server.name, []),
            **server.model_dump(),
        )
        for server in servers
    ]


def _built_in(
    name: str, naming: dict[str, list[str]], holding: dict[str, list[ServerRun]]
) -> ServerView:
    return ServerView(
        name=name, kind="built-in", policies=naming.get(name, []), runs=holding.get(name, [])
    )


def view_servers(session: Session, servers: Sequence[McpServer]) -> list[ServerView]:
    if not servers:
        return []
    return _views(servers, _naming(session), _holding(session))


def list_servers(session: Session) -> list[ServerView]:
    statement = select(McpServer).order_by(
        col(McpServer.created_at).desc(), col(McpServer.name).desc()
    )
    naming, holding = _naming(session), _holding(session)
    external = _views(session.exec(statement).all(), naming, holding)
    return external + [_built_in(name, naming, holding) for name in BUILT_IN]


# Counted from the mcp_call events since midnight UTC; a denial by a rule is not a failure.
def _calls(session: Session, name: str, now: int) -> ServerCalls:
    data = col(AuditEvent.data)
    calls = (col(AuditEvent.event) == "mcp_call", data["server"].as_string() == name)
    today = (*calls, col(AuditEvent.at) >= now - now % DAY)
    denied = data["decision"].as_string() == "deny"
    failed = (*calls, denied, data["category"].as_string() != "denied")
    newest = col(AuditEvent.seq).desc()
    count = select(func.count()).select_from(AuditEvent)
    last = session.exec(select(AuditEvent).where(*calls).order_by(newest).limit(1)).first()
    failure = session.exec(select(AuditEvent).where(*failed).order_by(newest).limit(1)).first()
    return ServerCalls(
        today=session.exec(count.where(*today)).one(),
        denied_today=session.exec(count.where(*today, denied)).one(),
        last=None
        if last is None
        else LastCall(
            at=last.at,
            tool=last.data["tool"],
            resource=last.data["resource"],
            decision=last.data["decision"],
        ),
        last_failure=None
        if failure is None
        else LastFailure(id=failure.id, at=failure.at, category=failure.data["category"]),
    )


def get_server(session: Session, name: str, now: int) -> ServerView:
    if name in BUILT_IN:
        view = _built_in(name, _naming(session), _holding(session))
    else:
        server = _find(session, name)
        if server is None:
            raise NotFoundError(f"mcp server {name} does not exist")
        view = view_servers(session, [server])[0]
    return view.model_copy(update={"calls": _calls(session, name, now)})


def register_server(session: Session, body: ServerCreate) -> McpServer:
    taken = ServerConflictError(f"mcp server {body.name} already exists")
    if body.name in BUILT_IN or _find(session, body.name) is not None:
        raise taken
    server = McpServer(
        id=f"mcpsrv_{uuid4().hex}",
        name=body.name,
        url=https_url(body.url),
        credential=body.credential,
        timeout_seconds=body.timeout_seconds,
        max_calls_per_minute=body.max_calls_per_minute,
    )
    session.add(server)
    audit.record(session, "mcp_server_registered", actor="operator", name=body.name)
    try:
        session.commit()
    except Exception:
        session.rollback()
        if _find(session, body.name) is None:
            raise
        raise taken from None
    return server


def patch_server(session: Session, name: str, body: ServerPatch, now: int) -> McpServer:
    server = _external(session, name)
    fields = sorted(body.model_fields_set)
    if not fields:
        raise PolicyError("a server change must name url, credential or a limit")
    for field in fields:
        value = getattr(body, field)
        if value is None and field != "credential":
            raise PolicyError(f"{field} cannot be null")
        setattr(server, field, https_url(value) if field == "url" else value)
    server.updated_at = now
    session.add(server)
    audit.record(session, "mcp_server_updated", actor="operator", name=name, fields=fields)
    session.commit()
    session.refresh(server)
    return server


# Only a Run still PENDING loses the server; the status in the WHERE keeps a started one whole.
# The rules stay, so a rule index means the same in the Run as in its policy.
def _strip(session: Session, name: str) -> int:
    statement = select(Run).where(
        col(Run.status) == RunStatus.PENDING, col(Run.mcp_document).is_not(None)
    )
    stripped = 0
    for run in session.exec(statement).all():
        document = run.mcp_document or {}
        kept = [server for server in document["servers"] if server["name"] != name]
        if len(kept) == len(document["servers"]):
            continue
        result = session.exec(
            update(Run)
            .where(col(Run.id) == run.id, col(Run.status) == RunStatus.PENDING)
            .values(mcp_document=document | {"servers": kept})
        )
        stripped += result.rowcount
    return stripped


def disable_server(session: Session, name: str, now: int) -> McpServer:
    server = _external(session, name)
    if server.disabled_at is not None:
        return server
    server.disabled_at = now
    server.updated_at = now
    session.add(server)
    runs = _strip(session, name)
    audit.record(session, "mcp_server_disabled", actor="operator", name=name, runs=runs)
    session.commit()
    session.refresh(server)
    return server


def enable_server(session: Session, name: str, now: int) -> McpServer:
    server = _external(session, name)
    if server.disabled_at is None:
        return server
    server.disabled_at = None
    server.updated_at = now
    session.add(server)
    audit.record(session, "mcp_server_enabled", actor="operator", name=name)
    session.commit()
    session.refresh(server)
    return server


def check_names(session: Session, names: Collection[str]) -> None:
    known = set(session.exec(select(McpServer.name).where(col(McpServer.name).in_(names))).all())
    unknown = sorted(set(names) - known)
    if unknown:
        raise PolicyError(f"mcp server {unknown[0]} is not registered")


def snapshot(session: Session, document: dict[str, Any]) -> dict[str, Any]:
    check_names(session, external_servers(document))
    names = external_servers(document, "allow")
    found = session.exec(select(McpServer).where(col(McpServer.name).in_(names))).all()
    servers = []
    for server in sorted(found, key=lambda server: server.name):
        if server.disabled_at is not None:
            raise PolicyError(f"mcp server {server.name} is disabled")
        servers.append(
            {
                "name": server.name,
                "url": server.url,
                "credential": server.credential,
                "timeout_seconds": server.timeout_seconds,
                "max_calls_per_minute": server.max_calls_per_minute,
            }
        )
    secrets = granted_secrets(document)
    return {"servers": servers, "rules": document["rules"], "secrets": secrets}
