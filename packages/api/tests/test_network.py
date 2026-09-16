from typing import Any

import pytest
from sqlmodel import Session

from naos_api.errors import PolicyError
from naos_api.network import NetworkPolicyIn, resolve_network_policy
from naos_api.policies import create_network_policy
from naos_api.spec import PolicyKind


def _policy(document: dict[str, Any]) -> NetworkPolicyIn:
    return NetworkPolicyIn.model_validate(document)


def test_hosts_are_normalized() -> None:
    resolved = resolve_network_policy(
        _policy({"allow": [{"protocol": "https", "host": "EXAMPLE.COM."}]})
    )

    assert resolved.allow[0].model_dump() == {
        "protocol": "https",
        "host": "example.com",
        "ip": None,
    }
    assert resolved.deny == []


def test_equivalent_documents_share_one_digest(session: Session) -> None:
    first, created = create_network_policy(session, _policy({"allow": [{"host": "example.com"}]}))
    again, created_again = create_network_policy(
        session, _policy({"allow": [{"host": "Example.com."}]})
    )

    assert created and not created_again
    assert first.id == again.id
    assert first.id.startswith("netpol_")
    assert first.kind is PolicyKind.NETWORK


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"allow": [], "deny": []},
        {"allow": [{}]},
        {"allow": [{"host": "bad_host"}]},
        {"allow": [{"host": "-leading.example.com"}]},
        {"allow": [{"host": "trailing-.example.com"}]},
        {"allow": [{"host": "localhost"}]},
        {"allow": [{"host": "192.0.2.1"}]},
        {"allow": [{"host": "::1"}]},
        {"allow": [{"host": "example..com"}]},
        {"deny": [{"host": "bad_host"}]},
    ],
)
def test_unusable_rules_are_refused(document: dict[str, Any]) -> None:
    with pytest.raises(PolicyError):
        resolve_network_policy(_policy(document))


@pytest.mark.parametrize(
    "document",
    [
        {"allow": [{"protocol": "ftp", "host": "example.com"}]},
        {"allow": [{"host": "example.com", "extra": 1}]},
        {"allow": [{"ip": "not-an-address"}]},
    ],
)
def test_malformed_rules_are_refused_by_the_model(document: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _policy(document)


def test_an_address_rule_keeps_its_literal() -> None:
    resolved = resolve_network_policy(_policy({"allow": [{"ip": "192.0.2.10"}]}))

    assert resolved.allow[0].ip == "192.0.2.10"
    assert resolved.allow[0].host is None
