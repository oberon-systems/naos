import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from stub_api import RUNNERS, WRITES

ALPHA = "rnr_8c1f42aa"
BETA = "rnr_4ad907bb"
DELTA = "rnr_91ba35dd"
EPSILON = "rnr_6f0d1811"
HX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def writes() -> Iterator[list[tuple[str, str, dict[str, object], str | None]]]:
    WRITES.clear()
    yield WRITES
    WRITES.clear()


def _rows(body: str) -> list[str]:
    return re.findall(r'<div class="table__id">(rnr_\w+)</div>', body)


def _tiles(body: str) -> list[tuple[str, str]]:
    return re.findall(
        r'tile__number">([^<]*)</span>\s*<span class="tile__note">([^<]*)</span>', body
    )


def test_the_tiles_count_the_fleet(client: TestClient) -> None:
    body = client.get("/runners").text

    assert _tiles(body) == [
        ("3", "heartbeat under 30s"),
        ("1", "lease expired, fenced"),
        ("1", "token refused"),
        ("5 / 6", "across live runners"),
    ]
    assert "5 enrolled \u00b7 3 live \u00b7 5 of 6 slots busy" in body


def test_the_table_has_the_board_columns(client: TestClient) -> None:
    body = client.get("/runners").text

    headers = re.findall(r"<th>([^<]*)</th>", body)
    assert headers == ["RUNNER", "SLOTS", "LEASE", "HEARTBEAT", "ROTATES IN", ""]
    assert _rows(body) == [row["id"] for row in RUNNERS]


def test_status_is_a_coloured_dot_not_a_word(client: TestClient) -> None:
    body = client.get("/runners").text

    assert ">LIVE<" not in body and ">STALE<" not in body and ">REVOKED<" not in body
    dots = re.findall(r'class="dot dot--lg tone-(\w+)"', body)
    assert dots == ["green", "green", "green", "amber", "red"]


def test_slots_are_coloured_by_meaning(client: TestClient) -> None:
    body = client.get("/runners").text

    loads = re.findall(r'class="runners-table__load([^"]*)">([^<]+)<', body)
    assert loads == [
        (" tone-blue", "2/2"),
        (" tone-green", "1/2"),
        (" tone-blue", "2/2"),
        (" tone-green", "0/2"),
        ("", "\u2014"),
    ]


def test_a_runner_with_no_slots_is_red(client: TestClient) -> None:
    RUNNERS[1]["capacity"] = 0
    RUNNERS[1]["runs"] = []

    body = client.get("/runners").text

    assert 'class="runners-table__load tone-red">0/0<' in body


def test_the_lease_is_a_bar_and_the_token_a_number(client: TestClient) -> None:
    body = client.get("/runners").text

    assert body.count('class="runners-table__bar"') == 4
    rotates = re.findall(r'class="runners-table__rotates">([^<]+)<', body)
    assert rotates == ["11h", "6h", "2h", "9h", "\u2014"]
    seen = re.findall(r'class="runners-table__seen[^"]*">([^<]+)<', body)
    assert seen == ["2s ago", "4s ago", "1s ago", "1m ago", "\u2014"]


def test_every_row_opens_the_popup(client: TestClient) -> None:
    body = client.get("/runners").text

    opened = re.findall(r'<tr class="runs-table__row"[^>]*hx-get="/runners/(rnr_\w+)"', body)
    assert opened == [row["id"] for row in RUNNERS]
    assert '<aside class="card">' not in body


def test_revoke_is_a_button_on_every_runner_but_the_revoked(client: TestClient) -> None:
    body = client.get("/runners").text

    revokes = re.findall(r'hx-get="/runners/(rnr_\w+)/revoke"', body)
    assert revokes == [ALPHA, BETA, "rnr_2e77b0cc", DELTA]
    assert "button--outline-danger" in body


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("live", ["rnr_8c1f42aa", "rnr_4ad907bb", "rnr_2e77b0cc"]),
        ("stale", [DELTA]),
        ("revoked", [EPSILON]),
    ],
)
def test_a_filter_keeps_its_state(client: TestClient, state: str, expected: list[str]) -> None:
    assert _rows(client.get(f"/runners?state={state}", headers=HX).text) == expected


@pytest.mark.parametrize("query", ["gam", "RNR_2E77"])
def test_search_matches_name_or_id(client: TestClient, query: str) -> None:
    assert _rows(client.get(f"/runners?q={query}", headers=HX).text) == ["rnr_2e77b0cc"]


def test_a_search_with_no_match_says_so(client: TestClient) -> None:
    assert "no runner matches" in client.get("/runners?q=omega", headers=HX).text


def test_a_filter_answers_with_the_table_not_the_page(client: TestClient) -> None:
    body = client.get("/runners?state=live", headers=HX).text

    assert body.lstrip().startswith('<section class="card" id="runners">')
    assert '<header class="topbar">' not in body


def test_revoke_asks_first(client: TestClient, writes: list[object]) -> None:
    body = client.get(f"/runners/{ALPHA}/revoke", headers=HX).text

    assert "Revoke runner?" in body
    assert f'hx-post="/runners/{ALPHA}/revoke"' in body
    assert "button--danger" in body
    assert writes == []


def test_a_confirmed_revoke_reaches_the_api(client: TestClient, writes: list[object]) -> None:
    response = client.post(f"/runners/{ALPHA}/revoke", headers=HX)

    assert response.headers["HX-Redirect"] == f"/runners/{ALPHA}"
    assert writes == [("POST", f"/runners/{ALPHA}/revoke", {}, None)]
    body = client.get("/runners").text
    assert f'hx-get="/runners/{ALPHA}/revoke"' not in body


def test_without_htmx_a_revoke_redirects(client: TestClient) -> None:
    response = client.post(f"/runners/{ALPHA}/revoke", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/runners/{ALPHA}"


def test_a_confirmed_drain_reaches_the_api(client: TestClient, writes: list[object]) -> None:
    asked = client.get(f"/runners/{ALPHA}/drain", headers=HX).text
    assert "Drain runner?" in asked

    client.post(f"/runners/{ALPHA}/drain", headers=HX)

    assert writes == [("POST", f"/runners/{ALPHA}/drain", {}, None)]
    body = client.get(f"/runners/{ALPHA}", headers=HX).text
    assert "LIVE \u00b7 draining" in body
    assert f'hx-get="/runners/{ALPHA}/drain"' not in body


def test_a_refused_action_is_shown_in_the_overlay(client: TestClient) -> None:
    response = client.post(f"/runners/{EPSILON}/drain", headers=HX)

    assert response.status_code == 200
    assert "409" in response.text
    assert "is revoked" in response.text


def test_an_unknown_action_is_not_routed(client: TestClient) -> None:
    assert client.post(f"/runners/{ALPHA}/delete", headers=HX).status_code in {404, 405, 422}


@pytest.mark.parametrize("path", ["/runners", f"/runners/{DELTA}", f"/runners/{ALPHA}/revoke"])
def test_no_runners_view_renders_a_token(client: TestClient, path: str) -> None:
    body = client.get(path).text

    assert "operator-token" not in body
    assert "Bearer" not in body
    assert "token_hash" not in body
