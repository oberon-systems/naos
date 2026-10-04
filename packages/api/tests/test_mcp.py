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


def _rule(**values: Any) -> dict[str, Any]:
    return {"server": "alpha", "tool": "search", "effect": "allow"} | values


def _rules(*rules: dict[str, Any]) -> McpPolicyIn:
    return _policy({"rules": list(rules)})


def _register(session: Session, *names: str) -> None:
    for name in names:
        body = ServerCreate(name=name, url=f"https://{name}.example.com/mcp")
        mcp_servers.register_server(session, body)


def test_rules_are_canonical() -> None:
    resolved = resolve_mcp_policy(
        _rules(
            _rule(server="shell", tool="read_file"),
            _rule(resource="docs://alpha/", tool=None),
            _rule(tool="*", effect="deny", arguments={"scope": {"equals": "admin"}}),
            _rule(max_calls=5, arguments={"b": {"prefix": "x"}, "a": {"regex": "[a-z]+"}}),
            _rule(max_calls=5, arguments={"a": {"regex": "[a-z]+"}, "b": {"prefix": "x"}}),
        )
    ).rules

    assert [(rule["server"], rule.get("tool"), rule.get("resource")) for rule in resolved] == [
        ("alpha", "*", None),
        ("alpha", "search", None),
        ("alpha", None, "docs://alpha/"),
        ("shell", "read_file", None),
    ]
    assert resolved[1] == {
        "server": "alpha",
        "tool": "search",
        "effect": "allow",
        "arguments": {"a": {"regex": "[a-z]+"}, "b": {"prefix": "x"}},
        "max_calls_per_minute": None,
        "max_calls": 5,
    }
    assert resolved[2] == {"server": "alpha", "resource": "docs://alpha/", "effect": "allow"}


def test_equivalent_documents_share_one_digest(session: Session) -> None:
    _register(session, "alpha")
    first, created = create_mcp_policy(session, _rules(_rule(), _rule(tool="fetch")))
    again, created_again = create_mcp_policy(
        session, _rules(_rule(tool="fetch", arguments={}), _rule(max_calls=None), _rule())
    )

    assert created and not created_again
    assert first.id == again.id
    assert first.id.startswith("mcppol_")
    assert first.kind is PolicyKind.MCP


def test_a_policy_names_registered_servers_only(session: Session) -> None:
    _register(session, "alpha")

    with pytest.raises(PolicyError, match="beta is not registered"):
        create_mcp_policy(session, _rules(_rule(), _rule(server="beta", effect="deny")))


def test_a_whole_server_is_allowed_beside_a_narrower_deny() -> None:
    resolved = resolve_mcp_policy(
        _rules(_rule(tool="*", max_calls=500), _rule(tool="delete", effect="deny"))
    ).rules

    assert [(rule["tool"], rule["effect"]) for rule in resolved] == [
        ("*", "allow"),
        ("delete", "deny"),
    ]


@pytest.mark.parametrize(
    "constraint",
    [
        {"equals": None},
        {"equals": {"a": [1, "b"]}},
        {"prefix": "/workspace/"},
        {"regex": r"https://example\.com/[a-z]+"},
        {"schema": {}},
        {"schema": {"type": ["string", "null"], "minLength": 1, "maxLength": 8, "pattern": "^a"}},
        {"schema": {"type": "integer", "minimum": 0, "maximum": 9}},
        {"schema": {"enum": ["GET", "HEAD"]}},
        {"schema": {"const": True}},
        {
            "schema": {
                "type": "object",
                "properties": {"a": {"type": "array", "items": {"type": "string"}}},
                "required": ["a"],
                "additionalProperties": False,
            }
        },
    ],
)
def test_each_kind_of_constraint_is_kept(constraint: dict[str, Any]) -> None:
    [rule] = resolve_mcp_policy(_rules(_rule(arguments={"query": constraint}))).rules

    assert rule["arguments"] == {"query": constraint}


@pytest.mark.parametrize(
    "rule",
    [
        _rule(tool=None),
        _rule(resource="docs://alpha/"),
        _rule(effect="deny", max_calls=5),
        _rule(effect="deny", max_calls_per_minute=5),
        _rule(tool=None, resource="docs://alpha/", max_calls=5),
        _rule(tool=None, resource="docs://alpha/", arguments={"a": {"equals": 1}}),
        _rule(arguments={"a": {}}),
        _rule(arguments={"a": {"equals": 1, "prefix": "x"}}),
        _rule(arguments={"a": {"prefix": None}}),
        _rule(arguments={"a": {"regex": "(a"}}),
        _rule(arguments={"a": {"regex": "(?=a)b"}}),
        _rule(arguments={"a": {"regex": "(?<!a)b"}}),
        _rule(arguments={"a": {"regex": r"(a)\1"}}),
        _rule(arguments={"a": {"regex": "a*+"}}),
        _rule(arguments={"a": {"schema": {"format": "uri"}}}),
        _rule(arguments={"a": {"schema": {"type": "text"}}}),
        _rule(arguments={"a": {"schema": {"minLength": -1}}}),
        _rule(arguments={"a": {"schema": {"pattern": "(a"}}}),
        _rule(arguments={"a": {"schema": {"properties": {"b": {"oneOf": []}}}}}),
        _rule(arguments={"a": {"equals": "x" * 5000}}),
        _rule(server="shell", tool="rm"),
        _rule(server="shell", tool=None, resource="file:///workspace/"),
        _rule(server="shell", tool="read_file", arguments={"pattern": {"prefix": "a"}}),
        _rule(server="shell", tool="read_file", arguments={"path": {"equals": 1}}),
        _rule(server="shell", tool="*", arguments={"url": {"prefix": "a"}}),
        _rule(server="network", tool="http_request", arguments={"headers": {"prefix": "a"}}),
        _rule(server="network", tool="http_request", arguments={"url": {"equals": 1}}),
        _rule(server="network", tool="*", arguments={"url": {"schema": {"type": "null"}}}),
        _rule(server="secrets", tool="*"),
        _rule(server="secrets", tool="get"),
    ],
)
def test_a_rule_that_is_malformed_or_never_matches_is_refused(rule: dict[str, Any]) -> None:
    with pytest.raises(PolicyError):
        resolve_mcp_policy(_rules(rule))


@pytest.mark.parametrize(
    "rules",
    [
        [],
        [_rule(), _rule(effect="deny")],
        [_rule(), _rule(tool="*", effect="deny")],
        [_rule(tool="*"), _rule(tool="*", effect="deny")],
        [
            _rule(tool=None, resource="docs://alpha/private/"),
            _rule(tool=None, resource="docs://alpha/", effect="deny"),
        ],
        [
            _rule(tool=None, resource="docs://alpha/"),
            _rule(server="beta", tool=None, resource="docs://alpha/private/"),
        ],
    ],
)
def test_unusable_policies_are_refused(rules: list[dict[str, Any]]) -> None:
    with pytest.raises(PolicyError):
        resolve_mcp_policy(_rules(*rules))


def test_a_deny_with_constraints_leaves_the_allow_usable() -> None:
    rules = [_rule(), _rule(effect="deny", arguments={"scope": {"equals": "admin"}})]

    assert len(resolve_mcp_policy(_rules(*rules)).rules) == 2


@pytest.mark.parametrize(
    "rule",
    [
        _rule(server="alpha__beta"),
        _rule(server="Alpha"),
        _rule(tool="bad tool"),
        _rule(tool=None, resource="no-scheme"),
        _rule(effect="audit"),
        _rule(max_calls=0),
        _rule(max_calls_per_minute=601),
        _rule(arguments={"bad name": {"equals": 1}}),
        _rule(arguments={"a": {"contains": "x"}}),
        _rule(url="https://mcp.example.com/mcp"),
        _rule(credential="alpha-token"),
    ],
)
def test_malformed_rules_are_refused_by_the_model(rule: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _rules(rule)


def test_a_built_in_tool_takes_rules_on_its_own_arguments() -> None:
    resolved = resolve_mcp_policy(
        _rules(
            _rule(server="shell", tool="*", arguments={"path": {"prefix": "/workspace/"}}),
            _rule(
                server="shell",
                tool="grep",
                effect="deny",
                arguments={"pattern": {"regex": "key.*"}},
            ),
            _rule(
                server="network",
                tool="http_request",
                arguments={"method": {"schema": {"enum": ["GET"]}}, "headers": {"schema": {}}},
            ),
        )
    ).rules

    assert [rule["server"] for rule in resolved] == ["network", "shell", "shell"]


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
