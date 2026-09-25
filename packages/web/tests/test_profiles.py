import re
from collections.abc import Iterator
from typing import Any

import pytest
import stub_api
from fastapi.testclient import TestClient
from stub_api import MCPPOL, NETPOL, PROFILES, WRITES

BUSY = "prof_7a1c30"
IDLE = "prof_a93e07"
HX = {"HX-Request": "true"}
ROW = '<tr class="runs-table__row"'
FORM = {
    "mode": "new",
    "name": "build-medium",
    "cpu": "4",
    "memory_mib": "8192",
    "disk_gib": "40",
    "mounts": "",
    "network": NETPOL,
    "shell": "",
    "mcp": "",
    "merge": "ask",
    "timeout": "3600",
}


@pytest.fixture(autouse=True)
def writes() -> Iterator[list[Any]]:
    WRITES.clear()
    yield WRITES
    WRITES.clear()


def test_the_tiles_count_profiles_policies_secrets_and_runs(client: TestClient) -> None:
    body = client.get("/profiles", params={"state": "unused"}).text

    assert re.findall(r'class="tile__number">([^<]+)<', body) == ["3", "3", "1", "6"]
    assert "mount \u00b7 net \u00b7 mcp" in body
    assert "3 profiles \u00b7 3 policies \u00b7 1 secret" in body


def test_a_row_shows_runtime_gates_merge_timeout_and_runs(client: TestClient) -> None:
    body = client.get("/profiles").text

    assert "2 cpu \u00b7 4096 MiB" in body
    assert "no mounts, no shell, no mcp" in body
    assert 'class="profile__tag profile__tag--off">mnt<' in body
    assert 'class="profile__tag">net<' in body
    assert "nothing to merge" in body
    assert "3600s" in body and ">1h<" in body
    assert "14 runs" in body and "last 10m ago" in body
    assert "never used" in body
    assert body.count(">Edit</a>") == 3


def test_filters_and_search_narrow_the_table(client: TestClient) -> None:
    assert client.get("/profiles", params={"state": "used"}).text.count(ROW) == 2
    assert client.get("/profiles", params={"state": "unused"}).text.count(ROW) == 1
    assert client.get("/profiles", params={"q": NETPOL[:10]}).text.count(ROW) == 3
    assert client.get("/profiles", params={"q": "sandbox"}).text.count(ROW) == 1
    assert "no profile matches" in client.get("/profiles", params={"q": "missing"}).text


def test_the_popup_resolves_policies_and_names_secrets_only(client: TestClient) -> None:
    body = client.get(f"/profiles/{BUSY}", headers=HX).text

    assert ">IN USE</span>" in body
    assert "https alpha.example.com \u00b7 https beta.example.com" in body
    assert "none \u2014 nothing mounted" in body
    assert "an edit here never reaches a Run that has started" in body
    for label in ("Clone", "Edit", "Delete"):
        assert f">{label}</a>" in body
    assert "#128" in body


def test_the_runs_and_audit_tabs_read_the_profile(client: TestClient) -> None:
    runs = client.get(f"/profiles/{BUSY}/runs", headers=HX).text
    audit = client.get(f"/profiles/{BUSY}/audit", headers=HX).text
    own = client.get(f"/profiles/{BUSY}/audit", params={"scope": "profile"}, headers=HX).text

    assert "run_9f21c4" in runs
    assert "profile_created" in audit and "run_created" in audit
    assert "profile_created" in own and "run_created" not in own


def test_a_new_profile_opens_with_every_gate_closed(client: TestClient) -> None:
    body = client.get("/profiles/new", headers=HX).text

    for closed in ("nothing mounted", "no network", "no shell", "no servers, no secrets"):
        assert re.search(rf'<option value=""\s*selected>none \u2014 {closed}</option>', body)
    assert "Every gate starts closed" in body


def test_creating_sends_the_spec_and_opens_the_profile(
    client: TestClient, writes: list[Any]
) -> None:
    response = client.post("/profiles/new", data=FORM, headers=HX)

    assert response.headers["HX-Redirect"] == "/profiles/prof_new001"
    [(_, path, body, _)] = writes
    assert path == "/profiles"
    assert body["name"] == "build-medium"
    assert body["spec"]["network"] == {"policy": NETPOL}
    assert body["spec"]["mcp"] == {"policy": None}
    assert body["spec"]["runtime"] == {"cpu": 4, "memory_mib": 8192, "disk_gib": 40}


def test_a_bad_number_stays_in_the_dialog(client: TestClient, writes: list[Any]) -> None:
    body = client.post("/profiles/new", data=FORM | {"cpu": "many"}, headers=HX).text

    assert "CPU must be a whole number" in body
    assert 'value="build-medium"' in body
    assert writes == []


def test_an_edit_the_api_refuses_shows_why(client: TestClient) -> None:
    body = client.post(f"/profiles/{BUSY}/edit", data=FORM, headers=HX).text

    assert "409" in body and "#128 is STARTED" in body
    assert "readonly" in body


def test_an_edit_keeps_the_name_and_sends_only_the_spec(
    client: TestClient, writes: list[Any]
) -> None:
    response = client.post(f"/profiles/{IDLE}/edit", data=FORM | {"mcp": MCPPOL}, headers=HX)

    assert response.headers["HX-Redirect"] == f"/profiles/{IDLE}"
    [(method, _, body, _)] = writes
    assert method == "PUT"
    assert set(body) == {"spec"}
    assert body["spec"]["mcp"] == {"policy": MCPPOL}


def test_a_clone_starts_from_the_source(client: TestClient) -> None:
    body = client.get(f"/profiles/{IDLE}/clone", headers=HX).text

    assert "Clone docs" in body
    assert 'value="docs-copy"' in body
    assert re.search(r'name="cpu"\s+value="1"', body)


def test_delete_asks_yes_or_no_for_an_idle_profile(client: TestClient) -> None:
    body = client.get(f"/profiles/{IDLE}/delete", headers=HX).text

    assert "Delete docs?" in body
    assert 'class="button button--danger" type="submit">Yes<' in body
    assert ">No</a>" in body


def test_delete_is_refused_up_front_while_a_run_is_open(
    client: TestClient, writes: list[Any]
) -> None:
    body = client.get(f"/profiles/{BUSY}/delete", headers=HX).text

    assert "build-small cannot be deleted" in body
    assert "is used by #128 STARTED. Delete it once they finish." in body
    assert ">Yes<" not in body and ">OK</a>" in body
    assert writes == []


def test_a_refusal_from_the_api_opens_the_same_popup(client: TestClient) -> None:
    body = client.post(f"/profiles/{BUSY}/delete", headers=HX).text

    assert "cannot be deleted" in body
    assert "the api answered 409" in body


def test_a_confirmed_delete_goes_back_to_the_list(client: TestClient, writes: list[Any]) -> None:
    response = client.post(f"/profiles/{IDLE}/delete", headers=HX)

    assert response.headers["HX-Redirect"] == "/profiles"
    assert writes[0][:2] == ("DELETE", f"/profiles/{IDLE}")


def test_a_run_links_the_profile_it_was_copied_from(client: TestClient) -> None:
    assert f'href="/profiles/{BUSY}/edit">Edit profile' in client.get("/runs/run_9f21c4").text


def test_a_run_whose_profile_is_gone_still_opens(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stub_api, "PROFILES", [row for row in PROFILES if row["id"] != BUSY])

    response = client.get("/runs/run_9f21c4")

    assert response.status_code == 200
    assert "Edit profile" not in response.text
