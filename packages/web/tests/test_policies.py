import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stub_api import MCPPOL, MNTPOL, MODELPOL, NETPOL, SHELLPOL, WRITES

HX = {"HX-Request": "true"}
ROW = '<tr class="runs-table__row"'


@pytest.fixture(autouse=True)
def writes() -> Iterator[list[Any]]:
    WRITES.clear()
    yield WRITES
    WRITES.clear()


def test_the_nav_places_policies_between_profiles_and_secrets(client: TestClient) -> None:
    body = client.get("/policies").text

    assert re.findall(r'class="nav__item[^"]*"\s+href="(/[a-z]+)"', body)[-4:] == [
        "/profiles",
        "/policies",
        "/secrets",
        "/audit",
    ]


def test_the_tiles_count_each_kind(client: TestClient) -> None:
    body = client.get("/policies", params={"kind": "shell"}).text

    assert re.findall(r'class="tile__number">([^<]+)<', body) == ["1", "1", "1", "1", "1"]
    assert "5 policies \u00b7 5 kinds \u00b7 4 in use" in body
    assert "1 secret named, never shown" in body
    assert "1 provider, keys never shown" in body
    assert 'class="dot tone-violet"' in body


def test_a_row_shows_document_usage_and_created(client: TestClient) -> None:
    body = client.get("/policies").text

    assert body.count(ROW) == 5
    assert "sha256 00000000\u2026000000" in body
    assert "https alpha.example.com \u00b7 https beta.example.com" in body
    assert "3 allow \u00b7 1 deny" in body
    assert "+ 1 home mount \u00b7 ro" in body
    assert "4 of 5 capabilities" in body
    assert "3 profiles" in body and "28 runs \u00b7 1 open" in body
    assert "no profile" in body and "never used" in body
    assert body.count(">Open</a>") == 5
    assert "1 provider \u00b7 1 model \u00b7 secrets alpha-key \u2014 values never shown" in body
    assert "100 000 input \u00b7 10 000 output tokens per run" in body


def test_filters_and_search_narrow_the_table_through_the_api(client: TestClient) -> None:
    assert client.get("/policies", params={"kind": "mount"}).text.count(ROW) == 1
    assert client.get("/policies", params={"kind": "model"}).text.count(ROW) == 1
    assert client.get("/policies", params={"q": "NETPOL_9A07"}).text.count(ROW) == 1
    assert client.get("/policies", params={"q": "3333"}).text.count(ROW) == 1
    assert "no policy matches" in client.get("/policies", params={"q": "missing"}).text


def test_the_network_document_shows_rules_identity_and_json(client: TestClient) -> None:
    body = client.get(f"/policies/{NETPOL}", headers=HX).text

    assert ">IN USE</span>" in body
    assert "ALLOW \u00b7 3 RULES" in body and "DENY \u00b7 1 RULE" in body
    assert "192.0.2.10" in body
    assert "https only \u00b7 3 hosts" in body
    assert "immutable" in body and "CANONICAL DOCUMENT" in body
    assert f'data-copy="{NETPOL}"' in body
    assert ">New from this</a>" in body
    assert ">Edit<" not in body and ">Delete<" not in body


def test_the_mount_and_shell_documents(client: TestClient) -> None:
    mount = client.get(f"/policies/{MNTPOL}", headers=HX).text
    shell = client.get(f"/policies/{SHELLPOL}", headers=HX).text

    assert "MOUNTS \u00b7 2" in mount and "WORKDIR" in mount and "/naos/api" in mount
    assert "upper disk \u00b7 merge" in mount
    assert "CAPABILITIES \u00b7 4 OF 5" in shell
    assert "not granted" in shell and "git_diff" in shell


def test_the_mcp_document_reads_secret_expiry_only(client: TestClient) -> None:
    body = client.get(f"/policies/{MCPPOL}", headers=HX).text

    assert "SERVERS \u00b7 2" in body and "SECRETS \u00b7 1" in body
    assert "beta__fetch" in body
    assert "expires in 12d" in body
    assert "sec_alpha" not in body


def test_used_by_lists_profiles_and_runs(client: TestClient) -> None:
    body = client.get(f"/policies/{NETPOL}/used", headers=HX).text

    assert "PROFILES \u00b7 3" in body and ">Open profile</a>" in body
    assert "run_9f21c4" in body and "build-small" in body
    assert "1 open \u00b7 27 finished" in body


def test_the_form_switches_kind_and_adds_rows(client: TestClient) -> None:
    network = client.get("/policies/new", params={"kind": "network"}, headers=HX).text
    added = client.post(
        "/policies/new", data={"kind": "network", "step": "add:deny"}, headers=HX
    ).text

    assert "ALLOW \u00b7 1 OF 64" in network and "DENY \u00b7 0 OF 64" in network
    assert "DENY \u00b7 1 OF 64" in added
    assert WRITES == []


def test_a_new_policy_opens_it(client: TestClient, writes: list[Any]) -> None:
    form = {
        "kind": "network",
        "allow.0.protocol": "https",
        "allow.0.host": "example.com",
        "allow.0.ip": "",
        "deny.0.protocol": "any",
        "deny.0.host": "",
        "deny.0.ip": "192.0.2.10",
    }
    response = client.post("/policies/new", data=form, headers=HX)

    assert response.headers["HX-Redirect"] == "/policies/netpol_new001"
    assert writes[0][2] == {
        "kind": "network",
        "document": {
            "allow": [{"protocol": "https", "host": "example.com"}],
            "deny": [{"ip": "192.0.2.10"}],
        },
    }


def test_an_equivalent_document_shows_the_existing_policy(client: TestClient) -> None:
    names = ("read_file", "grep", "list_dir", "git_status")
    form = {"kind": "shell"} | {f"cap.{name}": "on" for name in names}
    body = client.post("/policies/new", data=form, headers=HX).text

    assert "Policy already exists" in body
    assert f"The API answered 200 with {SHELLPOL}" in body
    assert f'hx-get="/policies/{SHELLPOL}"' in body
    assert 'name="cap.grep" value="on"' in body


def test_a_refused_document_shows_the_api_message(client: TestClient) -> None:
    form = {"kind": "mount", "workspace": "/etc/alpha", "workspace_mode": "rw"}
    body = client.post("/policies/new", data=form, headers=HX).text

    assert "dialog__error" in body and "outside the allowed roots" in body


def test_new_from_this_maps_a_mount_document_back(client: TestClient) -> None:
    body = client.get(f"/policies/{MNTPOL}/new", headers=HX).text

    assert re.search(r'name="workspace"\s+value="/srv/projects/alpha/api"', body)
    assert re.search(r'name="home.0.guest_path"\s+value=".cache/alpha"', body)
    assert "HOME \u00b7 1 OF 32" in body


def test_no_view_renders_a_secret_value(client: TestClient) -> None:
    paths = [f"/policies/{policy}{tail}" for policy in (MCPPOL, MODELPOL) for tail in ("", "/new")]
    for path in ("/policies", *paths):
        body = client.get(path).text
        assert "sec_alpha" not in body and "sec_8c21d0" not in body and "Bearer" not in body


def test_the_model_document_shows_providers_budget_and_secrets(client: TestClient) -> None:
    body = client.get(f"/policies/{MODELPOL}", headers=HX).text

    assert "PROVIDERS \u00b7 1" in body and "https://models.example.com" in body
    assert "api openai" in body and "models alpha-mini" in body
    assert "600s timeout \u00b7 60 requests/min" in body
    assert "BUDGET \u00b7 PER RUN" in body and "100 000" in body and "10 000" in body
    assert "SECRETS \u00b7 1" in body and "expires in 5d" in body
    assert "ENFORCEMENT" not in body


def test_the_model_form_adds_a_provider(client: TestClient) -> None:
    opened = client.get("/policies/new", params={"kind": "model"}, headers=HX).text
    added = client.post(
        "/policies/new",
        data={"kind": "model", "max_input": "2000000", "step": "add:providers"},
        headers=HX,
    ).text

    assert "BUDGET \u00b7 PER RUN" in opened and "PROVIDERS \u00b7 1" in opened
    assert "PROVIDERS \u00b7 1" in added
    assert re.search(r'name="max_input"\s+value="2000000"', added)
    assert WRITES == []


def test_a_new_model_policy_sends_the_document(client: TestClient, writes: list[Any]) -> None:
    form = {
        "kind": "model",
        "max_input": "2000000",
        "max_output": "200000",
        "providers.0.name": "alpha",
        "providers.0.api": "anthropic",
        "providers.0.url": "https://api.example.com",
        "providers.0.models": "alpha-large, alpha-mini",
        "providers.0.credential": "alpha-key",
        "providers.0.timeout": "300",
        "providers.0.requests": "30",
    }
    response = client.post("/policies/new", data=form, headers=HX)

    assert response.headers["HX-Redirect"] == "/policies/modelpol_new001"
    assert writes[0][2] == {
        "kind": "model",
        "document": {
            "providers": [
                {
                    "name": "alpha",
                    "api": "anthropic",
                    "url": "https://api.example.com",
                    "credential": "alpha-key",
                    "models": ["alpha-large", "alpha-mini"],
                    "timeout_seconds": 300,
                    "max_requests_per_minute": 30,
                }
            ],
            "max_input_tokens": 2000000,
            "max_output_tokens": 200000,
        },
    }


def test_a_model_budget_must_be_a_number(client: TestClient, writes: list[Any]) -> None:
    form = {"kind": "model", "max_input": "plenty", "max_output": "200000"}
    body = client.post("/policies/new", data=form, headers=HX).text

    assert "max input tokens must be a whole number" in body
    assert writes == []


def test_new_from_this_maps_a_model_document_back(client: TestClient) -> None:
    body = client.get(f"/policies/{MODELPOL}/new", headers=HX).text

    assert re.search(r'name="max_input"\s+value="100000"', body)
    assert re.search(r'name="providers.0.models"\s+value="alpha-mini"', body)
    assert re.search(r'<option value="openai"\s+selected>', body)
