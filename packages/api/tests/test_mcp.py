from typing import Any

import pytest
from sqlmodel import Session

from naos_api import mcp_servers
from naos_api.errors import PolicyError
from naos_api.mcp import McpPolicyIn, resolve_mcp_policy
from naos_api.mcp_servers import ServerCreate
from naos_api.policies import create_mcp_policy
from naos_api.secrets import create_secret, issue_credentials
from naos_api.spec import PolicyKind


def _policy(document: dict[str, Any]) -> McpPolicyIn:
    return McpPolicyIn.model_validate(document)


def _server(**values: Any) -> dict[str, Any]:
    return {"name": "alpha", "tools": ["search"]} | values


def _register(session: Session, *names: str) -> None:
    for name in names:
        body = ServerCreate(name=name, url=f"https://{name}.example.com/mcp")
        mcp_servers.register_server(session, body)


def test_servers_are_canonical() -> None:
    resolved = resolve_mcp_policy(
        _policy({"servers": [_server(name="beta", tools=["b", "a"]), _server()]})
    )

    assert [server.name for server in resolved.servers] == ["alpha", "beta"]
    assert resolved.servers[1].tools == ["a", "b"]


def test_equivalent_documents_share_one_digest(session: Session) -> None:
    _register(session, "alpha")
    first, created = create_mcp_policy(session, _policy({"servers": [_server(tools=["a", "b"])]}))
    again, created_again = create_mcp_policy(
        session, _policy({"servers": [_server(tools=["b", "a"])]})
    )

    assert created and not created_again
    assert first.id == again.id
    assert first.id.startswith("mcppol_")
    assert first.kind is PolicyKind.MCP


def test_a_policy_names_registered_servers_only(session: Session) -> None:
    _register(session, "alpha")

    with pytest.raises(PolicyError, match="beta is not registered"):
        create_mcp_policy(session, _policy({"servers": [_server(), _server(name="beta")]}))


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"servers": []},
        {"servers": [_server(), _server()]},
        {"servers": [_server(tools=[])]},
        {"servers": [_server(tools=["a", "a"])]},
        {"servers": [_server(name="shell")]},
        {"servers": [_server(name="network")]},
        {"servers": [_server(name="secrets")]},
        {
            "servers": [
                _server(resources=["docs://alpha/"]),
                _server(name="beta", resources=["docs://alpha/private/"]),
            ]
        },
    ],
)
def test_unusable_policies_are_refused(document: dict[str, Any]) -> None:
    with pytest.raises(PolicyError):
        resolve_mcp_policy(_policy(document))


@pytest.mark.parametrize(
    "server",
    [
        _server(name="alpha__beta"),
        _server(name="Alpha"),
        _server(tools=["bad tool"]),
        _server(resources=["no-scheme"]),
        _server(url="https://mcp.example.com/mcp"),
        _server(credential="alpha-token"),
        _server(timeout_seconds=30),
        _server(max_calls_per_minute=60),
    ],
)
def test_malformed_policies_are_refused_by_the_model(server: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _policy({"servers": [server]})


def test_resources_alone_are_enough() -> None:
    document = {"servers": [_server(tools=[], resources=["docs://a/"])]}

    resolved = resolve_mcp_policy(_policy(document))

    assert resolved.servers[0].resources == ["docs://a/"]


def test_credentials_are_capped_and_expired_ones_withheld(session: Session) -> None:
    now = 1000
    create_secret(session, "open", "value-open", None)
    create_secret(session, "soon", "value-soon", now + 30)
    create_secret(session, "gone", "value-gone", now)

    issued = issue_credentials(session, {"open", "soon", "gone", "missing"}, now, 300)

    assert set(issued) == {"open", "soon"}
    assert issued["open"].expires_at == now + 300
    assert issued["soon"].expires_at == now + 30
    assert issued["open"].value == "value-open"
