from typing import Any

import pytest
from sqlmodel import Session

from naos_api.errors import PolicyError
from naos_api.mcp import McpPolicyIn, resolve_mcp_policy
from naos_api.policies import create_mcp_policy
from naos_api.secrets import create_secret, issue_credentials
from naos_api.spec import PolicyKind


def _policy(document: dict[str, Any]) -> McpPolicyIn:
    return McpPolicyIn.model_validate(document)


def _server(**values: Any) -> dict[str, Any]:
    return {"name": "alpha", "url": "https://mcp.example.com/mcp", "tools": ["search"]} | values


def test_servers_are_canonical() -> None:
    resolved = resolve_mcp_policy(
        _policy(
            {
                "servers": [
                    _server(name="beta", url="https://MCP.example.com:443", tools=["b", "a"]),
                    _server(url="https://mcp.example.com:8443/mcp"),
                ]
            }
        )
    )

    assert [server.name for server in resolved.servers] == ["alpha", "beta"]
    assert resolved.servers[0].url == "https://mcp.example.com:8443/mcp"
    assert resolved.servers[1].url == "https://mcp.example.com/"
    assert resolved.servers[1].tools == ["a", "b"]


def test_equivalent_documents_share_one_digest(session: Session) -> None:
    first, created = create_mcp_policy(session, _policy({"servers": [_server(tools=["a", "b"])]}))
    again, created_again = create_mcp_policy(
        session, _policy({"servers": [_server(tools=["b", "a"])]})
    )

    assert created and not created_again
    assert first.id == again.id
    assert first.id.startswith("mcppol_")
    assert first.kind is PolicyKind.MCP


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"servers": []},
        {"servers": [_server(), _server()]},
        {"servers": [_server(tools=[])]},
        {"servers": [_server(tools=["a", "a"])]},
        {"servers": [_server(url="http://mcp.example.com/mcp")]},
        {"servers": [_server(url="https://user:pass@mcp.example.com/mcp")]},
        {"servers": [_server(url="https://mcp.example.com/mcp?token=alpha")]},
        {"servers": [_server(url="https://mcp.example.com/mcp#alpha")]},
        {"servers": [_server(url="https://192.0.2.10/mcp")]},
        {"servers": [_server(url="https://[2001:db8::1]/mcp")]},
        {"servers": [_server(url="https://localhost/mcp")]},
        {"servers": [_server(url="https://mcp.example.com:0/mcp")]},
        {"servers": [_server(url="https://mcp.example.com:65536/mcp")]},
        {"servers": [_server(url="https://mcp.example.com/a b")]},
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
        _server(credential="Bad Name"),
        _server(timeout_seconds=0),
        _server(timeout_seconds=46),
        _server(max_calls_per_minute=601),
        _server(extra=1),
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
