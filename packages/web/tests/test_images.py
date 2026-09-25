import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stub_api import WRITES

AGENTS = "naos-agents"
OLD = "naos-agents-old"
HX = {"HX-Request": "true"}
FORM = {
    "id": "image_epsilon",
    "name": "epsilon",
    "version": "2026.09.2",
    "digest": "sha256:" + "e" * 64,
    "url": "https://mirror.example.net/pub/epsilon.qcow2",
    "size": "4.2 GiB",
    "built": "2026-09-24",
}


@pytest.fixture(autouse=True)
def writes() -> Iterator[list[Any]]:
    WRITES.clear()
    yield WRITES
    WRITES.clear()


def _facts(body: str) -> dict[str, str]:
    pairs = re.findall(r"<dt>([^<]+)</dt>\s*<dd[^>]*>([^<]+)</dd>", body)
    return {label: value.strip() for label, value in pairs}


def test_the_tiles_count_the_whole_catalog(client: TestClient) -> None:
    body = client.get("/images", params={"state": "unused"}).text

    numbers = re.findall(r'class="tile__number">([^<]+)<', body)
    assert numbers == ["2", "1", "1", "3.8"]
    assert "booting 2 runs" in body
    assert "GiB \u00b7 size stated for 1 of 2" in body
    assert "2 registered \u00b7 1 in use \u00b7 3.8 GiB stated" in body


def test_a_row_shows_what_was_stated_and_what_was_not(client: TestClient) -> None:
    body = client.get("/images").text

    assert ">agents</span>" in body
    assert f'<div class="table__id">{AGENTS}</div>' in body
    assert "sha256:3f9a0000\u2026c21e" in body
    assert "built 4d ago" in body
    assert "3.8 GiB" in body
    assert "build date not stated" in body
    assert "size not stated" in body
    assert "mirror.example.net" in body
    assert "no runs" in body
    assert ">IN USE</span>" in body and ">UNUSED</span>" in body


def test_filters_and_search_narrow_the_table(client: TestClient) -> None:
    rows = 'hx-get="/images/naos'

    assert client.get("/images", params={"state": "in_use"}).text.count(rows) == 1
    assert client.get("/images", params={"state": "unused"}).text.count(rows) == 1
    assert client.get("/images", params={"q": "sha256:bbbb"}).text.count(rows) == 1
    assert client.get("/images", params={"q": "agents"}).text.count(rows) == 2
    assert "no image matches" in client.get("/images", params={"q": "missing"}).text


def test_no_view_offers_to_rewrite_or_delete(client: TestClient) -> None:
    pages = [client.get(path, headers=HX).text for path in ("/images", f"/images/{AGENTS}")]
    for body in pages:
        assert "Delete" not in body
        assert "Edit" not in body
        assert "hx-delete" not in body and "hx-put" not in body


def test_the_popup_has_three_tabs_and_copies_the_url(client: TestClient) -> None:
    body = client.get(f"/images/{AGENTS}", headers=HX).text

    tabs = re.findall(r'class="run__tab[^"]*"\s+href="([^"]+)"', body)
    assert tabs == [f"/images/{AGENTS}", f"/images/{AGENTS}/runs", f"/images/{AGENTS}/audit"]
    assert 'data-copy="https://images.example.com/a.qcow2"' in body
    assert ">agents</h2>" in body


def test_overview_shows_the_full_digest_and_the_facts(client: TestClient) -> None:
    body = client.get(f"/images/{AGENTS}", headers=HX).text
    facts = _facts(body)

    assert "sha256:3f9a" + "0" * 28 + "<br>" + "0" * 28 + "c21e" in body
    assert facts["Size"] == "3.8 GiB \u00b7 stated at registration"
    assert facts["Built"] == "4d ago \u00b7 stated at registration"
    assert facts["Booting"] == "2 runs"


def test_an_image_without_facts_says_so(client: TestClient) -> None:
    facts = _facts(client.get(f"/images/{OLD}", headers=HX).text)

    assert facts["Size"] == "not stated"
    assert facts["Built"] == "not stated"
    assert facts["Booting"] == "none"


def test_runs_tab_lists_the_runs_booting_the_image(client: TestClient) -> None:
    body = client.get(f"/images/{AGENTS}/runs", headers=HX).text

    opened = re.findall(r'hx-get="/runs/(run_\w+)"', body)
    assert opened
    assert "booting now" in body


def test_audit_tab_shows_registration_and_run_events(client: TestClient) -> None:
    body = client.get(f"/images/{AGENTS}/audit", headers=HX).text

    assert "image_registered" in body
    assert "1.4.2 \u00b7 sha256:3f9a0000\u2026c21e" in body
    assert "logs__row--error" in body
    registered = client.get(f"/images/{AGENTS}/audit", params={"scope": "registered"}, headers=HX)
    assert "run_transition" not in registered.text


def test_the_audit_exports_as_json(client: TestClient) -> None:
    response = client.get(f"/images/{AGENTS}/audit/export")

    assert response.headers["content-type"] == "application/json"
    assert [row["event"] for row in response.json()] == ["run_transition", "image_registered"]


def test_register_opens_a_dialog(client: TestClient) -> None:
    listed = client.get("/images").text
    body = client.get("/images/register", headers=HX).text

    assert 'hx-get="/images/register" hx-target="#overlay"' in listed
    assert "Register image</h2>" in body
    assert 'hx-post="/images/register"' in body


def test_registration_sends_what_the_operator_stated(client: TestClient, writes: list[Any]) -> None:
    response = client.post("/images/register", data=FORM, headers=HX)

    assert response.headers["HX-Redirect"] == "/images/image_epsilon"
    [(_, path, body, _)] = writes
    assert path == "/images"
    assert body == {
        "id": "image_epsilon",
        "version": "2026.09.2",
        "digest": "sha256:" + "e" * 64,
        "url": "https://mirror.example.net/pub/epsilon.qcow2",
        "name": "epsilon",
        "size_bytes": round(4.2 * (1 << 30)),
        "built_at": 1790208000,
    }


def test_optional_facts_are_left_out_when_empty(client: TestClient, writes: list[Any]) -> None:
    client.post("/images/register", data=FORM | {"name": "", "size": "", "built": ""}, headers=HX)

    [(_, _, body, _)] = writes
    assert set(body) == {"id", "version", "digest", "url"}


@pytest.mark.parametrize(
    ("field", "value", "said"),
    [("size", "a lot", "size a lot is not a number"), ("built", "yesterday", "YYYY-MM-DD")],
)
def test_a_fact_that_does_not_parse_stays_in_the_dialog(
    client: TestClient, writes: list[Any], field: str, value: str, said: str
) -> None:
    body = client.post("/images/register", data=FORM | {field: value}, headers=HX).text

    assert said in body
    assert 'class="dialog__error"' in body
    assert f'value="{value}"' in body
    assert writes == []


def test_a_conflict_is_shown_in_the_dialog(client: TestClient) -> None:
    body = client.post("/images/register", data=FORM | {"id": AGENTS}, headers=HX).text

    assert "409" in body
    assert "already registered with other values" in body
    assert 'value="2026.09.2"' in body
