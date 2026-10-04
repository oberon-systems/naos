import json
import re
from typing import Annotated, Any, Literal

from pydantic import Field, StrictInt

from naos_api.errors import PolicyError
from naos_api.network import resolve_host
from naos_api.spec import StrictModel

MAX_RULES = 256
MAX_ARGUMENTS = 16
MAX_CONSTRAINT = 4096
MAX_DEPTH = 8
MAX_URL = 2048
BUILT_IN = ("shell", "network", "secrets")

ServerName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")]
ToolName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
ResourcePrefix = Annotated[str, Field(pattern=r"^[a-z][a-z0-9+.-]*:[\x21-\x7e]{1,2048}$")]
RawUrl = Annotated[str, Field(min_length=1, max_length=MAX_URL)]

ArgumentName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
Pattern = Annotated[str, Field(min_length=1, max_length=512)]
PerMinute = Annotated[StrictInt, Field(ge=1, le=600)]
PerRun = Annotated[StrictInt, Field(ge=1, le=1_000_000)]

ANY_TOOL = "*"
_PATH = {"path": "string"}
# What a rule may name on a built-in server; secrets gets its tools with the secrets server.
TOOLS: dict[str, dict[str, dict[str, str]]] = {
    "shell": {
        "read_file": _PATH,
        "list_dir": _PATH,
        "grep": _PATH | {"pattern": "string"},
        "git_status": _PATH,
        "git_diff": _PATH,
    },
    "network": {
        "http_request": {"method": "string", "url": "string", "headers": "object", "body": "string"}
    },
    "secrets": {},
}
_JSON_TYPES = ("string", "number", "integer", "boolean", "object", "array", "null")
_COUNTS = ("minLength", "maxLength")
_BOUNDS = ("minimum", "maximum")
# The broker's engine has no lookaround, backreference, conditional, atomic group or possessive.
_UNSHARED = ("(?=", "(?!", "(?<=", "(?<!", "(?P=", "(?(", "(?>")

_URL = re.compile(r"https://([^/:?#@\[\]]+)(?::([0-9]{1,5}))?(/[\x21-\x7e]*)?")


class ConstraintIn(StrictModel):
    equals: Any = None
    prefix: Pattern | None = None
    regex: Pattern | None = None
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")


class RuleIn(StrictModel):
    server: ServerName
    tool: ToolName | Literal["*"] | None = None
    resource: ResourcePrefix | None = None
    effect: Literal["allow", "deny"]
    arguments: Annotated[dict[ArgumentName, ConstraintIn], Field(max_length=MAX_ARGUMENTS)] = {}
    max_calls_per_minute: PerMinute | None = None
    max_calls: PerRun | None = None


class McpPolicyIn(StrictModel):
    rules: Annotated[list[RuleIn], Field(max_length=MAX_RULES)] = []


class McpPolicy(StrictModel):
    rules: list[dict[str, Any]]


def https_url(raw: str, what: str = "server") -> str:
    match = _URL.fullmatch(raw)
    if match is None or "?" in raw or "#" in raw:
        raise PolicyError(f"{what} url {raw!r} must be https without userinfo, query or fragment")
    host, port, path = match.groups()
    if port is not None and not 0 < int(port) < 65536:
        raise PolicyError(f"{what} url {raw!r} has an invalid port")
    authority = resolve_host(host) + (f":{int(port)}" if port and int(port) != 443 else "")
    return f"https://{authority}{path or '/'}"


def _shared_regex(pattern: str, what: str) -> None:
    try:
        re.compile(pattern)
    except re.error as err:
        raise PolicyError(f"{what} has an invalid pattern: {err}") from None
    index, in_class = 0, False
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            escaped = pattern[index + 1]
            if escaped in "123456789ZG":
                raise PolicyError(f"{what} uses \\{escaped}, which the broker does not support")
            index += 2
            continue
        if char in "[]":
            in_class = char == "["
        possessive = char in "*+?}" and pattern[index + 1 : index + 2] == "+"
        if not in_class and (pattern.startswith(_UNSHARED, index) or possessive):
            raise PolicyError(f"{what} uses a construct the broker does not support")
        index += 1


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _schema(node: Any, what: str, depth: int = 0) -> None:
    if not isinstance(node, dict) or depth > MAX_DEPTH:
        raise PolicyError(f"{what} has a schema that is not an object or nests too deep")
    for key, value in node.items():
        valid = True
        if key == "type":
            types = value if isinstance(value, list) else [value]
            valid = bool(types) and all(item in _JSON_TYPES for item in types)
        elif key == "enum":
            valid = isinstance(value, list) and bool(value)
        elif key in _BOUNDS:
            valid = _number(value)
        elif key in _COUNTS:
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        elif key == "pattern":
            valid = isinstance(value, str)
            if valid:
                _shared_regex(value, what)
        elif key == "required":
            valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
        elif key == "items" or (key == "additionalProperties" and not isinstance(value, bool)):
            _schema(value, what, depth + 1)
        elif key == "properties":
            valid = isinstance(value, dict)
            for child in value.values() if valid else ():
                _schema(child, what, depth + 1)
        elif key not in ("const", "additionalProperties"):
            raise PolicyError(f"{what} uses the schema keyword {key!r}, which is not supported")
        if not valid:
            raise PolicyError(f"{what} has an invalid schema keyword {key!r}")


def _constraint(constraint: ConstraintIn, what: str) -> dict[str, Any]:
    kinds = constraint.model_fields_set
    if len(kinds) != 1:
        raise PolicyError(f"{what} must set exactly one of equals, prefix, regex and schema")
    (kind,) = kinds
    value = getattr(constraint, kind)
    if value is None and kind != "equals":
        raise PolicyError(f"{what} has an empty {kind.rstrip('_')}")
    if kind == "regex":
        _shared_regex(value, what)
    if kind == "schema_":
        _schema(value, what)
    resolved = {kind.rstrip("_"): value}
    if len(json.dumps(resolved)) > MAX_CONSTRAINT:
        raise PolicyError(f"{what} is too large")
    return resolved


def _fits(constraint: dict[str, Any], kind: str) -> bool:
    if "equals" in constraint:
        return _json_type(constraint["equals"]) == kind
    if "schema" in constraint:
        named = constraint["schema"].get("type", kind)
        return kind in (named if isinstance(named, list) else [named])
    return kind == "string"


def _built_in(rule: RuleIn, arguments: dict[str, dict[str, Any]], what: str) -> None:
    if rule.resource is not None:
        raise PolicyError(f"{what} names a resource of the built-in server {rule.server}")
    tools = TOOLS[rule.server]
    if rule.tool != ANY_TOOL and rule.tool not in tools:
        raise PolicyError(f"{what} names the unknown tool {rule.tool} of {rule.server}")
    named = list(tools.values()) if rule.tool == ANY_TOOL else [tools[rule.tool]]
    if not named:
        raise PolicyError(f"{what} can never match: {rule.server} has no tools")
    for name, constraint in arguments.items():
        kinds = {tool[name] for tool in named if name in tool}
        if not kinds:
            raise PolicyError(f"{what} names the unknown argument {name} of {rule.server}")
        if not any(_fits(constraint, kind) for kind in kinds):
            raise PolicyError(f"{what} can never match: {name} is of type {sorted(kinds)[0]}")


def _rule(rule: RuleIn, index: int) -> dict[str, Any]:
    what = f"rule {index}"
    if (rule.tool is None) == (rule.resource is None):
        raise PolicyError(f"{what} must name either a tool or a resource")
    budgets = rule.max_calls_per_minute is not None or rule.max_calls is not None
    if budgets and (rule.effect == "deny" or rule.resource is not None):
        raise PolicyError(f"{what} carries a budget, which only an allow rule of a tool may")
    arguments = {
        name: _constraint(rule.arguments[name], f"{what} argument {name}")
        for name in sorted(rule.arguments)
    }
    if rule.server in BUILT_IN:
        _built_in(rule, arguments, what)
    if rule.resource is not None:
        if arguments:
            raise PolicyError(f"{what} names a resource and cannot constrain arguments")
        return {"server": rule.server, "resource": rule.resource, "effect": rule.effect}
    return {
        "server": rule.server,
        "tool": rule.tool,
        "effect": rule.effect,
        "arguments": arguments,
        "max_calls_per_minute": rule.max_calls_per_minute,
        "max_calls": rule.max_calls,
    }


def _order(rule: dict[str, Any]) -> tuple[str, bool, str, str]:
    name = rule.get("tool") or rule["resource"]
    return rule["server"], "resource" in rule, name, json.dumps(rule, sort_keys=True)


def _covered(rule: dict[str, Any], rules: list[dict[str, Any]]) -> bool:
    for other in rules:
        if other["effect"] != "deny" or other["server"] != rule["server"]:
            continue
        if "resource" in rule:
            covered = rule["resource"].startswith(other.get("resource", "\x00"))
        else:
            covered = other.get("tool") in (rule["tool"], ANY_TOOL) and not other["arguments"]
        if covered:
            return True
    return False


def external_servers(document: dict[str, Any], effect: str | None = None) -> list[str]:
    return sorted(
        {
            rule["server"]
            for rule in document["rules"]
            if rule["server"] not in BUILT_IN and effect in (None, rule["effect"])
        }
    )


def resolve_mcp_policy(policy: McpPolicyIn) -> McpPolicy:
    if not policy.rules:
        raise PolicyError("an mcp policy must hold at least one rule")
    unique = {
        json.dumps(rule, sort_keys=True): rule
        for rule in (_rule(rule, index) for index, rule in enumerate(policy.rules))
    }
    rules = sorted(unique.values(), key=_order)
    allowed = [rule for rule in rules if rule["effect"] == "allow"]
    for rule in allowed:
        if _covered(rule, rules):
            raise PolicyError(f"rule {rules.index(rule)} of the stored policy is always denied")
    # A resources/read is routed by prefix, so one URI must never match two servers.
    prefixes = [(rule["server"], rule["resource"]) for rule in allowed if "resource" in rule]
    for index, (server, prefix) in enumerate(prefixes):
        for other, taken in prefixes[index + 1 :]:
            if other != server and (taken.startswith(prefix) or prefix.startswith(taken)):
                raise PolicyError(f"resource {prefix!r} of {server} overlaps a resource of {other}")
    return McpPolicy(rules=rules)
