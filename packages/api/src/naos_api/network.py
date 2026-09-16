import re
from ipaddress import ip_address
from typing import Annotated, Literal

from pydantic import Field, IPvAnyAddress

from naos_api.errors import PolicyError
from naos_api.spec import StrictModel

MAX_RULES = 64
MAX_HOST = 253
MAX_LABEL = 63

Protocol = Literal["http", "https"]
RawHost = Annotated[str, Field(min_length=1, max_length=MAX_HOST + 1)]

_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


class NetworkRuleIn(StrictModel):
    protocol: Protocol | None = None
    host: RawHost | None = None
    ip: IPvAnyAddress | None = None


class NetworkPolicyIn(StrictModel):
    allow: Annotated[list[NetworkRuleIn], Field(max_length=MAX_RULES)] = []
    deny: Annotated[list[NetworkRuleIn], Field(max_length=MAX_RULES)] = []


class NetworkRule(StrictModel):
    protocol: Protocol | None = None
    host: str | None = None
    ip: str | None = None


class NetworkPolicy(StrictModel):
    allow: list[NetworkRule]
    deny: list[NetworkRule]


def _host(raw: str) -> str:
    host = raw.removesuffix(".").lower()
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise PolicyError(f"host {raw!r} is an address literal; use the ip field")
    labels = host.split(".")
    if not host or len(host) > MAX_HOST or host == "localhost":
        raise PolicyError(f"host {raw!r} is not a usable hostname")
    if any(len(label) > MAX_LABEL or not _LABEL.fullmatch(label) for label in labels):
        raise PolicyError(f"host {raw!r} is not a valid hostname")
    return host


def _rule(rule: NetworkRuleIn) -> NetworkRule:
    # A rule with no field set matches every destination, which turns an allow list into allow-all.
    if rule.protocol is None and rule.host is None and rule.ip is None:
        raise PolicyError("a rule must constrain at least one of protocol, host or ip")
    return NetworkRule(
        protocol=rule.protocol,
        host=_host(rule.host) if rule.host is not None else None,
        ip=str(rule.ip) if rule.ip is not None else None,
    )


def resolve_network_policy(policy: NetworkPolicyIn) -> NetworkPolicy:
    if not policy.allow and not policy.deny:
        raise PolicyError("a network policy must carry at least one allow or deny rule")
    return NetworkPolicy(
        allow=[_rule(rule) for rule in policy.allow],
        deny=[_rule(rule) for rule in policy.deny],
    )
