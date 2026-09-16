from typing import Any

import pytest
from sqlmodel import Session

from naos_api.errors import PolicyError
from naos_api.policies import create_shell_policy
from naos_api.shell import ShellPolicyIn, resolve_shell_policy
from naos_api.spec import PolicyKind


def _policy(document: dict[str, Any]) -> ShellPolicyIn:
    return ShellPolicyIn.model_validate(document)


def test_capabilities_are_ordered() -> None:
    resolved = resolve_shell_policy(_policy({"allow": ["git_diff", "read_file"]}))

    assert resolved.allow == ["read_file", "git_diff"]


def test_equivalent_documents_share_one_digest(session: Session) -> None:
    first, created = create_shell_policy(session, _policy({"allow": ["read_file", "grep"]}))
    again, created_again = create_shell_policy(session, _policy({"allow": ["grep", "read_file"]}))

    assert created and not created_again
    assert first.id == again.id
    assert first.id.startswith("shellpol_")
    assert first.kind is PolicyKind.SHELL


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"allow": []},
        {"allow": ["read_file", "read_file"]},
    ],
)
def test_unusable_policies_are_refused(document: dict[str, Any]) -> None:
    with pytest.raises(PolicyError):
        resolve_shell_policy(_policy(document))


@pytest.mark.parametrize(
    "document",
    [
        {"allow": ["write_file"]},
        {"allow": ["read_file"], "extra": 1},
        {"allow": "read_file"},
    ],
)
def test_malformed_policies_are_refused_by_the_model(document: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _policy(document)
