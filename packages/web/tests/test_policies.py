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

    assert re.findall(r'class="tile__number">([^<]+)<', body) == ["1", "1", "1", "1"]
    assert "4 policies \u00b7 4 kinds \u00b7 3 in use" in body
    assert "1 secret named, never shown" in body


def test_a_row_shows_document_usage_and_created(client: TestClient) -> None:
    body = client.get("/policies").text

    assert body.count(ROW) == 4
    assert "sha256 00000000\u2026000000" in body
    assert "https alpha.example.com \u00b7 https beta.example.com" in body
    assert "3 allow \u00b7 1 deny" in body
    assert "+ 1 home mount \u00b7 ro" in body
    assert "4 of 5 capabilities" in body
    assert "3 profiles" in body and "28 runs \u00b7 1 open" in body
    assert "no profile" in body and "never used" in body
    assert body.count(">Open</a>") == 4


def test_filters_and_search_narrow_the_table_through_the_api(client: TestClient) -> None:
    assert client.get("/policies", params={"kind": "mount"}).text.count(ROW) == 1
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
    for path in ("/policies", f"/policies/{MCPPOL}", f"/policies/{MCPPOL}/new"):
        body = client.get(path).text
        assert "sec_alpha" not in body and "Bearer" not in body


def test_a_model_policy_waits_for_its_own_screens(client: TestClient) -> None:
    listed = client.get("/policies").text
    opened = client.get(f"/policies/{MODELPOL}", headers=HX)

    assert MODELPOL not in listed
    assert listed.count(ROW) == 4
    assert opened.status_code == 200
    assert "not shown here" in opened.text
