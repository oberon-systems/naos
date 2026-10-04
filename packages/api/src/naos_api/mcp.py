import re
from typing import Annotated

from pydantic import Field

from naos_api.errors import PolicyError
from naos_api.network import resolve_host
from naos_api.spec import StrictModel

MAX_SERVERS = 16
MAX_TOOLS = 128
MAX_RESOURCES = 64
MAX_URL = 2048
BUILT_IN = ("shell", "network", "secrets")

ServerName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")]
ToolName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
ResourcePrefix = Annotated[str, Field(pattern=r"^[a-z][a-z0-9+.-]*:[\x21-\x7e]{1,2048}$")]
RawUrl = Annotated[str, Field(min_length=1, max_length=MAX_URL)]

_URL = re.compile(r"https://([^/:?#@\[\]]+)(?::([0-9]{1,5}))?(/[\x21-\x7e]*)?")


class ServerGrantIn(StrictModel):
    name: ServerName
    tools: Annotated[list[ToolName], Field(max_length=MAX_TOOLS)] = []
    resources: Annotated[list[ResourcePrefix], Field(max_length=MAX_RESOURCES)] = []


class McpPolicyIn(StrictModel):
    servers: Annotated[list[ServerGrantIn], Field(max_length=MAX_SERVERS)] = []


class ServerGrant(StrictModel):
    name: str
    tools: list[str]
    resources: list[str]


class McpPolicy(StrictModel):
    servers: list[ServerGrant]


def https_url(raw: str, what: str = "server") -> str:
    match = _URL.fullmatch(raw)
    if match is None or "?" in raw or "#" in raw:
        raise PolicyError(f"{what} url {raw!r} must be https without userinfo, query or fragment")
    host, port, path = match.groups()
    if port is not None and not 0 < int(port) < 65536:
        raise PolicyError(f"{what} url {raw!r} has an invalid port")
    authority = resolve_host(host) + (f":{int(port)}" if port and int(port) != 443 else "")
    return f"https://{authority}{path or '/'}"


def _unique(values: list[str], what: str, server: str) -> list[str]:
    if len(set(values)) != len(values):
        raise PolicyError(f"server {server} repeats a {what}")
    return sorted(values)


def _server(server: ServerGrantIn) -> ServerGrant:
    if server.name in BUILT_IN:
        raise PolicyError(f"server {server.name} is built-in and is not granted by name")
    if not server.tools and not server.resources:
        raise PolicyError(f"server {server.name} must allow at least one tool or resource")
    return ServerGrant(
        name=server.name,
        tools=_unique(server.tools, "tool", server.name),
        resources=_unique(server.resources, "resource", server.name),
    )


def resolve_mcp_policy(policy: McpPolicyIn) -> McpPolicy:
    if not policy.servers:
        raise PolicyError("an mcp policy must name at least one server")
    names = [server.name for server in policy.servers]
    if len(set(names)) != len(names):
        raise PolicyError("server names must be unique")
    servers = sorted((_server(server) for server in policy.servers), key=lambda s: s.name)
    # A resources/read is routed by prefix, so one URI must never match two servers.
    for index, server in enumerate(servers):
        for other in servers[index + 1 :]:
            for prefix in server.resources:
                if any(p.startswith(prefix) or prefix.startswith(p) for p in other.resources):
                    raise PolicyError(
                        f"resource {prefix!r} of {server.name} overlaps a resource of {other.name}"
                    )
    return McpPolicy(servers=servers)
