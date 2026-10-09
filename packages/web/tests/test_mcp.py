import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stub_api import MCPPOL, WRITES

HX = {"HX-Request": "true"}
ROW = '<tr class="runs-table__row"'


@pytest.fixture(autouse=True)
def writes() -> Iterator[list[Any]]:
    WRITES.clear()
    yield WRITES
    WRITES.clear()


def flat(body: str) -> str:
    return re.sub(r"\s+", " ", body)


def test_the_nav_places_mcp_between_policies_and_secrets(client: TestClient) -> None:
    body = client.get("/mcp").text

    assert re.findall(r'class="nav__item[^"]*"\s+href="(/[a-z]+)"', body)[-4:] == [
        "/policies",
        "/mcp",
        "/secrets",
        "/audit",
    ]
    assert "MCP servers</h1>" in body


def test_the_tiles_count_every_server(client: TestClient) -> None:
    body = flat(client.get("/mcp", params={"state": "disabled"}).text)

    assert re.findall(r'class="tile__number">([^<]+)<', body) == ["6", "5", "1", "2"]
    assert "6 servers \u00b7 3 external \u00b7 named by 1 policy \u00b7 held by 2 runs" in body
    assert "3 external \u00b7 3 built-in" in body
    assert body.count(ROW) == 1


def test_a_row_names_the_entry_its_policies_and_runs(client: TestClient) -> None:
    body = flat(client.get("/mcp").text)

    assert body.count(ROW) == 6
    assert "https://alpha.example.com/mcp" in body
    assert 'href="/secrets/alpha-token"' in body
    assert "30s \u00b7 60/min" in body
    assert f'href="/policies/{MCPPOL}"' in body
    assert "1 started \u00b7 1 pending" in body
    assert "new Runs naming it are refused" in body
    assert "1 policy \u00b7 1 secret granted" in body
    assert "set by the network policy" in body


@pytest.mark.parametrize(
    ("params", "names"),
    [
        ({"state": "external"}, ["alpha", "beta", "gamma"]),
        ({"state": "built-in"}, ["shell", "network", "secrets"]),
        ({"state": "unused"}, ["gamma", "network"]),
        ({"q": "alpha-token"}, ["alpha"]),
        ({"q": "BETA.example"}, ["beta"]),
    ],
)
def test_search_and_filters_narrow_the_table(
    client: TestClient, params: dict[str, str], names: list[str]
) -> None:
    body = client.get("/mcp", params=params, headers=HX).text

    assert re.findall(r'hx-get="/mcp/([a-z-]+)"\s+hx-target="#overlay"\s+hx-trigger', body) == names


def test_the_popup_shows_the_entry_runs_and_calls(client: TestClient) -> None:
    body = flat(client.get("/mcp/alpha", headers=HX).text)

    assert "30 s per call" in body
    assert "tools appear as alpha__&lt;tool&gt;" in body
    assert 'hx-get="/runs/run_9f21c4"' in body
    assert "current entry" in body
    assert "an older entry" in body
    assert "212 \u00b7 4 denied" in body
    assert 'href="/audit/ev_mcp_fail"' in body
    assert 'href="/mcp/alpha/disable"' in body
    assert 'href="/mcp/alpha/edit"' in body


def test_a_built_in_server_has_no_edit_or_disable(client: TestClient) -> None:
    body = client.get("/mcp/shell", headers=HX).text

    assert "BUILT-IN" in body
    assert "/mcp/shell/edit" not in body
    assert "/mcp/shell/disable" not in body
    assert "held through a rule" in body


def test_a_disabled_server_offers_enable(client: TestClient, writes: list[Any]) -> None:
    body = client.get("/mcp/gamma", headers=HX).text
    moved = client.post("/mcp/gamma/enable", headers=HX)

    assert 'hx-post="/mcp/gamma/enable"' in body
    assert moved.headers["HX-Redirect"] == "/mcp/gamma"
    assert writes == [("POST", "/mcp-servers/gamma/enable", {}, None)]


def test_register_posts_the_entry_and_opens_it(client: TestClient, writes: list[Any]) -> None:
    form = client.get("/mcp/_new", headers=HX).text
    fields = {
        "name": "delta",
        "url": "https://delta.example.com/mcp",
        "credential": "alpha-token",
        "timeout": "20",
        "calls": "90",
    }
    moved = client.post("/mcp/_new", data=fields, headers=HX)

    assert "Register MCP server" in form
    assert '<option value="alpha-token"' in form
    assert moved.headers["HX-Redirect"] == "/mcp/delta"
    assert writes[0][2] == {
        "name": "delta",
        "url": "https://delta.example.com/mcp",
        "credential": "alpha-token",
        "timeout_seconds": 20,
        "max_calls_per_minute": 90,
    }


def test_a_refused_register_comes_back_with_its_reason(client: TestClient) -> None:
    fields = {"name": "alpha", "url": "https://alpha.example.com/mcp", "timeout": "30"}
    body = client.post("/mcp/_new", data=fields | {"calls": "60"}, headers=HX).text
    typed = client.post("/mcp/_new", data=fields | {"calls": "many"}, headers=HX).text

    assert "mcp server alpha already exists" in body
    assert 'value="https://alpha.example.com/mcp"' in body
    assert "calls per minute must be a whole number" in typed


def test_edit_sends_only_what_changed(client: TestClient, writes: list[Any]) -> None:
    form = client.get("/mcp/alpha/edit", headers=HX).text
    fields = {"url": "https://alpha.example.com/mcp", "credential": "", "timeout": "10"}
    moved = client.post("/mcp/alpha/edit", data=fields | {"calls": "60"}, headers=HX)

    assert "readonly" in form
    assert moved.headers["HX-Redirect"] == "/mcp/alpha"
    changed = {"credential": None, "timeout_seconds": 10}
    assert writes == [("PATCH", "/mcp-servers/alpha", changed, None)]


def test_disable_asks_first_and_names_what_changes(client: TestClient, writes: list[Any]) -> None:
    body = flat(client.get("/mcp/alpha/disable", headers=HX).text)
    moved = client.post("/mcp/alpha/disable", headers=HX)

    assert "Disable alpha?" in body
    assert 'hx-get="/runs/run_3a90f8"' in body
    assert "loses alpha, keeps its rules" in body
    assert "keeps its entry" in body
    assert moved.headers["HX-Redirect"] == "/mcp/alpha"
    assert writes == [("POST", "/mcp-servers/alpha/disable", {}, None)]


def test_a_built_in_server_cannot_be_edited(client: TestClient, writes: list[Any]) -> None:
    body = client.get("/mcp/shell/edit", headers=HX).text

    assert "built-in and cannot change" in body
    assert writes == []
