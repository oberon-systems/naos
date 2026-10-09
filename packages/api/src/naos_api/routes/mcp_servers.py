from fastapi import APIRouter

from naos_api import mcp_servers
from naos_api.clock import NowDep
from naos_api.mcp_servers import ServerCreate, ServerPatch, ServerView
from naos_api.models import McpServer
from naos_api.routes.deps import SessionDep


def _view(session: SessionDep, server: McpServer) -> ServerView:
    return mcp_servers.view_servers(session, [server])[0]


router = APIRouter()


@router.post("/mcp-servers", status_code=201)
def register_server(body: ServerCreate, session: SessionDep) -> ServerView:
    return _view(session, mcp_servers.register_server(session, body))


@router.get("/mcp-servers")
def list_servers(session: SessionDep) -> list[ServerView]:
    return mcp_servers.list_servers(session)


@router.get("/mcp-servers/{name}")
def get_server(name: str, session: SessionDep, now: NowDep) -> ServerView:
    return mcp_servers.get_server(session, name, now)


@router.patch("/mcp-servers/{name}")
def patch_server(name: str, body: ServerPatch, session: SessionDep, now: NowDep) -> ServerView:
    return _view(session, mcp_servers.patch_server(session, name, body, now))


@router.post("/mcp-servers/{name}/disable")
def disable_server(name: str, session: SessionDep, now: NowDep) -> ServerView:
    return _view(session, mcp_servers.disable_server(session, name, now))


@router.post("/mcp-servers/{name}/enable")
def enable_server(name: str, session: SessionDep, now: NowDep) -> ServerView:
    return _view(session, mcp_servers.enable_server(session, name, now))
