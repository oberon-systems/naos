import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stub_api import TRAIL_QUERIES

HX = {"HX-Request": "true"}
Queries = list[dict[str, Any]]
DENIED = f"evt_{41887:032x}"
FORGED = f"evt_{41901:032x}"


@pytest.fixture(autouse=True)
def queries() -> Iterator[Queries]:
    TRAIL_QUERIES.clear()
    yield TRAIL_QUERIES
    TRAIL_QUERIES.clear()


def _facts(body: str) -> dict[str, str]:
    pairs = re.findall(r"<dt>([^<]+)</dt>\s*<dd[^>]*>\s*(?:<a[^>]*>)?([^<]+?)\s*<", body)
    return dict(pairs)


def _events(body: str) -> list[str]:
    return re.findall(r'data-event="(evt_\w+)"', body)


def test_the_tiles_and_the_header_count_the_whole_trail(client: TestClient) -> None:
    body = client.get("/audit", params={"state": "merge"}).text

    numbers = re.findall(r'class="tile__number">([^<]+)<', body)
    assert numbers == ["1 284", "7", "0", "4s"]
    assert "network 5 \u00b7 shell 2" in body
    assert "runner events rejected" in body and "behind the runner log" in body
    assert "1 284 events \u00b7 api and runner \u00b7 up to seq 41 902" in body
    assert 'href="/audit/export"' in body and "live tail" in body


def test_rows_are_newest_first_and_name_run_and_runner(client: TestClient) -> None:
    body = client.get("/audit").text

    assert _events(body)[:3] == [FORGED, f"evt_{41900:032x}", f"evt_{41899:032x}"]
    assert [c for c in re.findall(r"<th>([A-Z]+)</th>", body)] == [
        "TIME",
        "EVENT",
        "ACTOR",
        "SOURCE",
        "RUN",
        "RUNNER",
        "DATA",
    ]
    assert "<div>#128</div>" in body and "<div>#126</div>" in body
    assert "<div>alpha</div>" in body and "no runner" in body and "no run" in body
    assert "gate decision" in body and "runner health" in body and "vm lifecycle" in body
    assert "https registry.example.com \u00b7 rule no rule matched" in body


def test_a_forged_row_is_refused_and_never_shown(client: TestClient) -> None:
    body = client.get("/audit").text

    assert "unknown event" in body
    assert "refused: unexpected field token" in body
    assert "refused: unknown event" in body
    assert "audit-table__row--refused" in body
    assert "secret-alpha-value" not in body and "rm -rf" not in body


def test_a_row_opens_its_popup(client: TestClient) -> None:
    body = client.get("/audit").text

    assert f'hx-get="/audit/{DENIED}"' in body
    assert 'hx-target="#overlay"' in body
    assert "audit-detail" not in body and 'class="split"' not in body


def test_a_denial_popup_names_its_run_and_policy(client: TestClient) -> None:
    body = client.get(f"/audit/{DENIED}", headers=HX).text

    facts = _facts(body)
    assert facts["protocol"] == "https"
    assert facts["host"] == "registry.example.com"
    assert facts["rule"] == "no rule matched"
    assert facts["Reached the API"] == "4s after the runner wrote it"
    assert facts["Run"] == "run_9f21c4 \u00b7 #128"
    assert facts["VM"] == "vm_7f3a91"
    assert facts["Runner"] == "rnr_8c1f42aa \u00b7 alpha"
    assert facts["Seq"] == "41 887"
    assert facts["Gate"] == "network"
    assert facts["Policy"].startswith("netpol_9a07")
    assert "allows protocol, host, rule and reason" in body
    assert "the Run keeps going" in body
    assert '<span class="run__mono">seq 41 887</span>' in body
    assert "<span>gate decision</span>" in body and "<span>run #128 on alpha</span>" in body
    assert f'data-copy="{DENIED}"' in body
    assert f'hx-get="/audit/{DENIED}/logs"' in body
    assert 'hx-get="/runs/run_9f21c4"' in body and 'hx-target="#overlay">run_9f21c4' in body
    assert "<html" not in body


def test_the_logs_tab_is_the_run_timeline_inside_the_popup(client: TestClient) -> None:
    body = client.get(f"/audit/{DENIED}/logs", headers=HX).text

    assert "timeline of run #128 \u00b7 10 entries" in body
    assert "Audit entries are append-only" in body
    assert "private.example.com" in body
    assert "refused: unknown event" in body
    assert "rm -rf" not in body and "secret-alpha-value" not in body
    assert 'href="/runs' not in body


def test_an_event_link_opens_the_popup_over_the_trail(client: TestClient) -> None:
    body = client.get(f"/audit/{DENIED}").text

    assert "<html" in body and "tile__number" in body
    assert 'class="panel panel--run panel--audit"' in body


def test_a_refused_event_says_so_and_shows_nothing(client: TestClient) -> None:
    body = client.get(f"/audit/{FORGED}", headers=HX).text

    assert ">REFUSED</span>" in body
    assert "refused: unexpected field token" in body
    assert "secret-alpha-value" not in body


def test_an_api_event_reached_the_api_itself(client: TestClient) -> None:
    body = client.get(f"/audit/evt_{41899:032x}", headers=HX).text

    facts = _facts(body)
    assert facts["Reached the API"] == "written by the API"
    assert facts["Run"] == "\u2014"
    assert facts["lease_id"] == "lease_7fe201"
    assert "Gate" not in facts
    assert f'hx-get="/audit/evt_{41899:032x}/logs"' in body


def test_an_unknown_event_is_a_notice(client: TestClient) -> None:
    body = client.get(f"/audit/evt_{0:032x}", headers=HX).text

    assert 'class="notice"' in body and "404" in body


def test_a_filter_asks_the_api_for_its_events(client: TestClient, queries: Queries) -> None:
    body = client.get("/audit", params={"state": "gates"}, headers=HX).text

    asked = queries[-1]["event"]
    assert "network_denied" in asked and "mcp_call" in asked and "run_created" not in asked
    assert _events(body) == [f"evt_{41890:032x}", DENIED]
    assert "tile__number" not in body


def test_search_narrows_by_run_runner_or_event(client: TestClient, queries: Queries) -> None:
    by_run = client.get("/audit", params={"q": "run_5be317"}, headers=HX).text
    assert queries[-1]["run_id"] == "run_5be317"
    assert _events(by_run) == [f"evt_{41897:032x}", f"evt_{41895:032x}"]

    by_event = client.get("/audit", params={"q": "vm_created"}, headers=HX).text
    assert _events(by_event) == [f"evt_{41882:032x}"]

    by_runner = client.get("/audit", params={"q": "rnr_91ba35dd"}, headers=HX).text
    assert "rnr_91ba35dd" in by_runner


def test_an_event_outside_the_filter_matches_nothing(client: TestClient, queries: Queries) -> None:
    body = client.get("/audit", params={"state": "merge", "q": "vm_created"}, headers=HX).text

    assert "no event matches" in body
    assert queries == []


def test_the_tail_adds_only_newer_rows(client: TestClient) -> None:
    response = client.get("/audit/tail", params={"after": 41897}, headers=HX)

    assert _events(response.text) == [FORGED, f"evt_{41900:032x}", f"evt_{41899:032x}"]
    assert 'id="audit-after" name="after" value="41901" hx-swap-oob="true"' in response.text
    assert "secret-alpha-value" not in response.text
    assert client.get("/audit/tail", params={"after": 41901}, headers=HX).status_code == 204


def test_the_tail_keeps_the_filter(client: TestClient) -> None:
    response = client.get("/audit/tail", params={"after": 0, "state": "merge"}, headers=HX)

    assert _events(response.text) == [f"evt_{41897:032x}", f"evt_{41895:032x}"]


def test_export_is_the_filtered_trail(client: TestClient) -> None:
    response = client.get("/audit/export", params={"state": "gates"})

    assert response.headers["content-disposition"] == 'attachment; filename="audit.json"'
    assert [row["event"] for row in json.loads(response.text)] == [
        "shell_allowed",
        "network_denied",
    ]


def test_an_unreachable_api_is_named(offline: TestClient) -> None:
    body = offline.get("/audit").text

    assert "the api did not answer" in body
