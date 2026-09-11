from typing import Any

import pytest
from pydantic import ValidationError

from naos_api.spec import PolicyKind, RunSpec, digest_of


def test_minimal_spec_grants_no_policies(spec_body: dict[str, Any]) -> None:
    spec = RunSpec.model_validate(spec_body)

    assert spec.merge.policy == "ask"
    assert set(spec.policy_refs()) == set(PolicyKind)
    assert all(ref is None for ref in spec.policy_refs().values())


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("status",), "STARTED"),
        (("runtime", "gpu"), 1),
        (("runtime", "cpu"), 0),
        (("runtime", "cpu"), 65),
        (("runtime", "cpu"), "4"),
        (("runtime", "cpu"), True),
        (("runtime", "memory_mib"), 128),
        (("runtime", "disk_gib"), 0),
        (("timeout",), 10),
        (("timeout",), 86401),
        (("image", "digest"), "sha256:xyz"),
        (("image", "digest"), "md5:" + "a" * 32),
        (("image", "id"), "../alpha"),
        (("merge",), {"policy": "auto"}),
        (("mounts",), {"policy_snapshot": "../../etc"}),
        (("network",), {"policy_snapshot": "netpol_1", "allow": ["*"]}),
    ],
)
def test_invalid_spec_is_rejected(
    spec_body: dict[str, Any], path: tuple[str, ...], value: object
) -> None:
    target = spec_body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        RunSpec.model_validate(spec_body)


def test_digest_is_independent_of_key_order() -> None:
    assert digest_of({"a": 1, "b": {"c": 2, "d": 3}}) == digest_of({"b": {"d": 3, "c": 2}, "a": 1})
    assert digest_of({"a": 1}) != digest_of({"a": 2})
