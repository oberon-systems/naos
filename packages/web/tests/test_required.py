import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HX = {"HX-Request": "true"}
RUN = "/runs/run_9f21c4"
BUILD_SMALL = "prof_7a1c30"
CSS = Path(__file__).parents[1] / "src/naos_web/static/naos.css"
MODEL = {"kind": "model", "step": "add:providers"}
MCP = {"kind": "mcp", "step": "add:rules"}
RUN_MCP = {"source": "edit", "held": "0", "step": "add:rules"}
CONFIGURE = {"key": "dialog-alpha", "profile": BUILD_SMALL}
FORMS = [
    ("GET", "/images/register", {}, {"id", "version", "digest", "url"}),
    ("GET", "/profiles/new", {}, {"name", "cpu", "memory_mib", "disk_gib", "timeout"}),
    ("GET", "/secrets/_new", {}, {"name", "value"}),
    ("GET", "/mcp/_new", {}, {"name", "url", "timeout", "calls"}),
    ("GET", "/policies/new?kind=mount", {}, {"workspace"}),
    ("POST", "/policies/new", MCP, {"rules.0.target"}),
    (
        "POST",
        "/policies/new",
        MODEL,
        {"providers.0." + key for key in ("name", "url", "models", "credential")},
    ),
    ("POST", f"{RUN}/mcp", RUN_MCP, {"rules.0.target"}),
    ("POST", "/runs/new/configure", CONFIGURE, {"cpu", "memory_mib", "disk_gib", "timeout"}),
]
MESSAGES = [
    ("GET", "/images/register", {}, "required · sha256 of the qcow2"),
    ("GET", "/profiles/new", {}, "required · a unique profile name"),
    ("GET", "/policies/new?kind=mount", {}, "required · a host directory under an allowed root"),
    ("POST", "/policies/new", MCP, "required · a tool or a resource prefix"),
    ("POST", "/policies/new", MODEL, "required · at least one model"),
    ("POST", "/policies/new", MODEL, "required · a secret from the list"),
    ("POST", f"{RUN}/mcp", RUN_MCP, "required · a tool or a resource prefix"),
    ("POST", "/runs/new/configure", CONFIGURE, "required · seconds"),
]


def _body(client: TestClient, method: str, path: str, data: dict[str, str]) -> str:
    body: str = client.request(method, path, data=data or None, headers=HX).text
    return body


def _required(body: str) -> set[str]:
    tags = re.findall(r"<(?:input|textarea|select)\b[^>]*>", body)
    return {
        found.group(1)
        for tag in tags
        if re.search(r"\srequired\b", tag) and (found := re.search(r'name="([^"]+)"', tag))
    }


@pytest.mark.parametrize(("method", "path", "data", "names"), FORMS)
def test_a_form_marks_its_required_fields(
    client: TestClient, method: str, path: str, data: dict[str, str], names: set[str]
) -> None:
    assert names <= _required(_body(client, method, path, data))


@pytest.mark.parametrize(("method", "path", "data", "message"), MESSAGES)
def test_a_required_field_carries_its_message(
    client: TestClient, method: str, path: str, data: dict[str, str], message: str
) -> None:
    assert f'<span class="field__error">{message}</span>' in _body(client, method, path, data)


def test_an_optional_field_is_not_required(client: TestClient) -> None:
    assert not {"name", "size", "built"} & _required(_body(client, "GET", "/images/register", {}))


def test_the_digest_is_checked_in_the_browser(client: TestClient) -> None:
    assert 'pattern="sha256:[0-9a-f]{64}"' in _body(client, "GET", "/images/register", {})


def test_step_buttons_skip_the_check(client: TestClient) -> None:
    for method, path, data in (("POST", "/policies/new", MODEL), ("POST", f"{RUN}/mcp", RUN_MCP)):
        found = re.findall(r'<button[^>]*name="step"[^>]*>', _body(client, method, path, data))
        # `review` is the submit of the run dialog: it is the one button that does check.
        steps = [step for step in found if 'value="review"' not in step]
        assert steps and all("formnovalidate" in step for step in steps)
    back = re.search(
        r'<button[^>]*formaction="/runs/new/profile"[^>]*>',
        _body(client, "POST", "/runs/new/configure", CONFIGURE),
    )
    assert back and "formnovalidate" in back.group(0)


def test_nothing_is_red_before_the_browser_refuses_a_field() -> None:
    css = CSS.read_text()

    assert re.search(r"\.field__error,\s*\.dialog__invalid \{\s*display: none;", css)
    assert ".field:has(:user-invalid) .field__error" in css
    assert ".field__box:has(:user-invalid)" in css
    assert ":invalid " not in css.replace(":user-invalid", "")
