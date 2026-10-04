from collections.abc import Collection, Sequence
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, StrictInt
from sqlmodel import Session, col, select, update

from naos_api import audit
from naos_api.errors import NotFoundError, PolicyError, ServerConflictError
from naos_api.lifecycle import RunStatus
from naos_api.mcp import BUILT_IN, RawUrl, ServerName, external_servers, https_url
from naos_api.models import McpServer, Policy, Run
from naos_api.secrets import SecretName
from naos_api.spec import PolicyKind, StrictModel

Timeout = Annotated[StrictInt, Field(ge=1, le=45)]
Calls = Annotated[StrictInt, Field(ge=1, le=600)]


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


def view_servers(session: Session, servers: Sequence[McpServer]) -> list[ServerView]:
    naming = _naming(session) if servers else {}
    return [
        ServerView(kind="external", policies=naming.get(server.name, []), **server.model_dump())
        for server in servers
    ]


def list_servers(session: Session) -> list[ServerView]:
    statement = select(McpServer).order_by(
        col(McpServer.created_at).desc(), col(McpServer.name).desc()
    )
    external = view_servers(session, session.exec(statement).all())
    naming = _naming(session)
    return external + [
        ServerView(name=name, kind="built-in", policies=naming.get(name, [])) for name in BUILT_IN
    ]


def get_server(session: Session, name: str) -> ServerView:
    if name in BUILT_IN:
        return ServerView(name=name, kind="built-in", policies=_naming(session).get(name, []))
    server = _find(session, name)
    if server is None:
        raise NotFoundError(f"mcp server {name} does not exist")
    return view_servers(session, [server])[0]


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
    return {"servers": servers, "rules": document["rules"]}
