import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stub_api import WRITES

HX = {"HX-Request": "true"}
BUILD_SMALL = "prof_7a1c30"
DOCS = "prof_a93e07"
NETWORK = "netpol_9a07" + "0" * 28
KEY = "dialog-alpha"


@pytest.fixture(autouse=True)
def writes() -> Iterator[list[Any]]:
    WRITES.clear()
    yield WRITES
    WRITES.clear()


def draft(profile: str, **values: str) -> dict[str, str]:
    base = {
        "key": KEY,
        "profile": profile,
        "cpu": "1",
        "memory_mib": "2048",
        "disk_gib": "20",
        "timeout": "3600",
        "merge": "ask",
        "mounts": "",
        "network": NETWORK,
        "shell": "",
        "mcp": "",
        "image": "auto",
        "runner": "auto",
    }
    return base | values


def test_the_button_opens_the_dialog_in_the_overlay(client: TestClient) -> None:
    body = client.get("/runs").text

    assert 'hx-get="/runs/new"' in body
    assert 'href="/runs/new"' in body


def test_the_profile_step_lists_profiles_with_usage(client: TestClient) -> None:
    body = client.get("/runs/new", headers=HX).text

    assert 'aria-label="New run"' in body
    assert "<!DOCTYPE html>" not in body
    assert "Profiles · 3" in body
    assert "2 cpu · 4096 MiB · 20 GiB · 3600s · ask" in body
    assert re.findall(r'class="choice__usage">([^<]+)<', body) == [
        "1 active",
        "idle",
        "never used",
        "+",
    ]
    assert "Create new profile" in body
    assert re.search(r'name="key" value="[0-9a-f]{32}"', body)


def test_the_search_swaps_only_the_list(client: TestClient) -> None:
    headers = HX | {"HX-Target": "new-run-profiles"}
    body = client.get("/runs/new", params={"q": "DOC"}, headers=headers).text

    assert body.lstrip().startswith('<div id="new-run-profiles">')
    assert "Profiles · 1" in body
    assert DOCS in body and BUILD_SMALL not in body


def test_a_plain_request_gets_the_dialog_inside_the_shell(client: TestClient) -> None:
    body = client.get("/runs/new").text

    assert "<!DOCTYPE html>" in body
    assert 'class="topbar"' in body
    assert 'aria-label="New run"' in body


def test_configure_copies_the_picked_profile(client: TestClient) -> None:
    body = client.post(
        "/runs/new/configure", data={"key": KEY, "profile": BUILD_SMALL}, headers=HX
    ).text

    assert re.search(r'name="cpu"\s+value="2"', body)
    assert re.search(r'name="memory_mib"\s+value="4096"', body)
    assert "field--edited" not in body
    assert "allowlist · 3 hosts" in body
    assert "naos-agents · v1.4.2" in body
    assert "sha256:3f9a\u2026c21e · pinned by digest" in body
    assert "any live runner with a free slot · alpha, beta, gamma" in body
    assert "delta" not in body and "epsilon" not in body
    assert "alpha-token" not in body


def test_configure_marks_edited_fields_with_the_profile_value(client: TestClient) -> None:
    form = draft(BUILD_SMALL, cpu="4", memory_mib="8192")
    body = client.post("/runs/new/configure", data=form, headers=HX).text

    assert "edited · 2 fields" in body
    assert body.count('class="field field--edited"') == 2
    assert "profile: 2 vCPU" in body
    assert "profile: 4096 MiB" in body


def test_review_locks_update_while_a_run_is_active(client: TestClient) -> None:
    body = client.post("/runs/new/review", data=draft(BUILD_SMALL, cpu="4"), headers=HX).text

    assert "Changes to build-small" in body
    assert "2 \u2192 4 vCPU" in body
    assert "Update build-small is off: #128 is STARTED on it" in body
    assert re.search(r'value="update"\s+disabled', body)
    assert re.search(r'value="new"\s+checked', body)


def test_review_of_an_unedited_profile_saves_nothing(client: TestClient) -> None:
    form = draft(DOCS)
    body = client.post("/runs/new/review", data=form, headers=HX).text

    assert "Save profile" not in body
    assert "The profile is unchanged" in body


def test_review_refuses_a_value_that_is_not_a_number(client: TestClient) -> None:
    body = client.post("/runs/new/review", data=draft(DOCS, cpu="many"), headers=HX).text

    assert "CPU must be a whole number" in body
    assert "Next: save &amp; run" in body


def test_an_unedited_profile_is_run_without_saving(client: TestClient, writes: list[Any]) -> None:
    response = client.post("/runs/new", data=draft(DOCS), headers=HX)

    assert response.headers["HX-Redirect"] == "/runs"
    ((method, path, body, key),) = writes
    assert (method, path, key) == ("POST", f"/profiles/{DOCS}/runs", KEY)
    assert body == {
        "image": {"id": "naos-agents", "digest": "sha256:3f9a" + "0" * 56 + "c21e"},
        "runner": None,
    }


def test_an_edited_profile_is_updated_then_run(client: TestClient, writes: list[Any]) -> None:
    form = draft(DOCS, cpu="2", mode="update", runner="rnr_4ad907bb")
    client.post("/runs/new", data=form, headers=HX)

    assert [(method, path) for method, path, _, _ in writes] == [
        ("PUT", f"/profiles/{DOCS}"),
        ("POST", f"/profiles/{DOCS}/runs"),
    ]
    assert writes[0][2]["spec"]["runtime"]["cpu"] == 2
    assert writes[1][2]["runner"] == "rnr_4ad907bb"


def test_save_as_new_creates_a_profile_under_its_name(
    client: TestClient, writes: list[Any]
) -> None:
    form = draft(BUILD_SMALL, cpu="4", mode="new", name="build-small-4cpu")
    client.post("/runs/new", data=form, headers=HX)

    assert writes[0][:2] == ("POST", "/profiles")
    assert writes[0][2]["name"] == "build-small-4cpu"
    assert writes[1][1] == "/profiles/prof_new001/runs"


def test_save_as_new_needs_a_name(client: TestClient, writes: list[Any]) -> None:
    body = client.post("/runs/new", data=draft("", mode="new"), headers=HX).text

    assert "a new profile needs a name" in body
    assert writes == []


def test_a_refused_update_is_shown_and_creates_no_run(
    client: TestClient, writes: list[Any]
) -> None:
    form = draft(BUILD_SMALL, cpu="4", mode="update")
    body = client.post("/runs/new", data=form, headers=HX).text

    assert "the api answered 409" in body
    assert "#128 is STARTED on it" in body
    assert [path for _, path, _, _ in writes] == [f"/profiles/{BUILD_SMALL}"]


def test_a_double_submit_sends_one_key(client: TestClient, writes: list[Any]) -> None:
    for _ in range(2):
        client.post("/runs/new", data=draft(DOCS), headers=HX)

    assert {key for _, _, _, key in writes} == {KEY}


def test_a_plain_submit_redirects_to_the_runs(client: TestClient) -> None:
    response = client.post("/runs/new", data=draft(DOCS), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/runs"
