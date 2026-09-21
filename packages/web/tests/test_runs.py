import re

import pytest
from fastapi.testclient import TestClient

from naos_web.pages import RUN_FILTERS, STATUS_TONE

HX = {"HX-Request": "true"}


def numbers(body: str) -> list[str]:
    return re.findall(r"<div>#(\d+)</div>", body)


def test_the_list_shows_every_state_the_board_draws(client: TestClient) -> None:
    body = client.get("/runs").text

    assert numbers(body) == ["128", "127", "126", "125", "124", "123", "122", "121", "120"]
    for status in STATUS_TONE:
        assert f">{status}</span>" in body


def test_a_row_carries_its_number_and_its_id(client: TestClient) -> None:
    body = client.get("/runs").text

    assert "<div>#128</div>" in body
    assert '<div class="table__id">run_9f21c4</div>' in body


def test_the_spec_column_names_the_workspace_and_the_image(client: TestClient) -> None:
    body = client.get("/runs").text

    assert "<div>alpha</div>" in body
    assert "naos-agents \u00b7 ab12cd34ef56" in body


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("all", ["128", "127", "126", "125", "124", "123", "122", "121", "120"]),
        ("active", ["128", "127", "125", "123"]),
        ("queued", ["124"]),
        ("waiting_merge", ["126"]),
        ("failed", ["122"]),
    ],
)
def test_each_filter_narrows_the_list(client: TestClient, state: str, expected: list[str]) -> None:
    body = client.get("/runs", params={"state": state}).text

    assert numbers(body) == expected


def test_a_filter_narrows_server_side(client: TestClient) -> None:
    every = numbers(client.get("/runs").text)
    active = numbers(client.get("/runs", params={"state": "active"}).text)

    assert len(active) < len(every)


def test_the_current_filter_is_the_only_one_marked(client: TestClient) -> None:
    for item in RUN_FILTERS:
        body = client.get(item.href).text
        assert body.count("filter--active") == 1
        assert f'filter--active"\n       href="{item.href}"' in body


def test_an_unknown_filter_is_refused(client: TestClient) -> None:
    assert client.get("/runs", params={"state": "everything"}).status_code == 422


def test_htmx_gets_the_body_without_the_shell(client: TestClient) -> None:
    fragment = client.get("/runs", params={"state": "active"}, headers=HX).text

    assert '<header class="topbar">' not in fragment
    assert "<!DOCTYPE html>" not in fragment
    assert numbers(fragment) == ["128", "127", "125", "123"]
    assert 'class="tile"' in fragment


def test_the_tiles_carry_the_summary(client: TestClient) -> None:
    body = client.get("/runs").text

    for number, note in [
        ("4", "of 6 runner slots"),
        ("1", "oldest waiting 3m"),
        ("1", "needs approval"),
        ("1", "guest exited 1"),
    ]:
        assert f'<span class="tile__number">{number}</span>' in body
        assert note in body
    assert ">6 open</span>" in body


def test_each_state_gets_the_action_the_board_gives_it(client: TestClient) -> None:
    body = client.get("/runs").text
    rows = [row for row in body.split("<tr>") if 'class="pill' in row]
    actions = {
        re.findall(r'class="pill [^"]*">([A-Z_]+)</span>', row)[0]: re.findall(
            r'class="action"[\s\S]*?>([A-Za-z]+)</', row
        )[0]
        for row in rows
    }

    assert actions == {
        "STARTED": "Open",
        "COLLECTING": "Open",
        "WAITING_MERGE": "Review",
        "STARTING": "Open",
        "PENDING": "Cancel",
        "STOPPING": "Open",
        "FAILED": "Logs",
        "COMPLETED": "Diff",
        "CANCELLED": "Open",
    }


def test_only_a_queued_run_can_be_cancelled_and_it_asks_first(client: TestClient) -> None:
    body = client.get("/runs").text

    assert body.count("hx-post=") == 1
    assert 'hx-post="/runs/run_3a90f8/cancel?state=all"' in body
    assert 'hx-confirm="Cancel run #124?"' in body


def test_cancelling_goes_through_the_api(client: TestClient) -> None:
    before = client.get("/runs", params={"state": "queued"}).text
    assert numbers(before) == ["124"]

    answered = client.post("/runs/run_3a90f8/cancel", params={"state": "queued"}, headers=HX)

    assert answered.status_code == 200
    assert numbers(answered.text) == []
    assert numbers(client.get("/runs", params={"state": "queued"}).text) == []


def test_no_row_action_addresses_the_api_itself(client: TestClient) -> None:
    body = client.get("/runs").text

    assert "api.example.com" not in body
    assert "/api/v1/" not in body


def test_the_operator_token_never_reaches_the_page(client: TestClient) -> None:
    body = client.get("/runs").text

    assert "operator-token" not in body
    assert "Authorization" not in body
    assert "Bearer" not in body


def test_a_row_shows_the_reason_or_the_merge_but_never_a_guess(client: TestClient) -> None:
    body = client.get("/runs").text

    assert "guest exited 1" in body
    assert "stop requested by operator" in body
    assert "12 files changed, 0 conflicts" in body


def test_the_runners_card_reads_every_runner_condition(client: TestClient) -> None:
    body = client.get("/runs").text

    assert ">3 live</span>" in body
    for text in [
        "2 / 2 slots",
        "0 / 2 slots",
        "\u2014 slots",
        "lease 52s of 60s",
        "lease expired \u00b7 fenced",
        "revoked 2h ago",
        "hb 94s ago",
        "no heartbeat",
    ]:
        assert text in body
    assert ">#123</span>" in body and ">#128</span>" in body


def test_the_lifecycle_footer_states_the_run_model(client: TestClient) -> None:
    body = client.get("/runs").text

    assert "LIFECYCLE" in body and "EXITS" in body
    assert "any state before COMPLETED" in body
    assert "an expired lease fences its runs" in body


def test_an_unreachable_api_says_so_instead_of_inventing_rows(offline: TestClient) -> None:
    response = offline.get("/runs")

    assert response.status_code == 200
    assert "the api did not answer" in response.text
    assert numbers(response.text) == []
    assert "tile__number" not in response.text
