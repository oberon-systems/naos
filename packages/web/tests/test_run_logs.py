import json
import re

from fastapi.testclient import TestClient
from stub_api import RUN_EVENTS

from naos_web import events

STARTED = "run_9f21c4"
HX = {"HX-Request": "true"}


def _rows(body: str) -> list[list[str]]:
    table = body[body.index("<tbody>") : body.index("</tbody>")]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
    cells = [re.findall(r"<td[^>]*>(.*?)</td>", row, re.S) for row in rows]
    return [[re.sub(r"<[^>]+>", "", cell).strip() for cell in row] for row in cells]


def _event(event: str, data: dict[str, object], source: str = "api") -> dict[str, object]:
    return {
        "at": 0,
        "source": source,
        "event": event,
        "actor": "system",
        "runner_id": None,
        "vm_id": None,
        "data": data,
    }


def test_the_tab_reads_the_timeline_of_the_run(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}/logs", headers=HX).text

    assert re.search(r'run__tab run__tab--active"\s+href="/runs/run_9f21c4/logs"', body)
    assert f"{len(RUN_EVENTS)} entries" in body
    rows = _rows(body)
    assert [row[2] for row in rows[:3]] == ["run_created", "run_assigned", "run_transition"]
    assert rows[0][1:] == ["api", "run_created", "operator", "alpha · profile build-small"]
    assert rows[1][3:] == ["system", "lease_5d2a91 · slot 1 of 2"]
    assert rows[2][3:] == ["runner:alpha", "PENDING \u2192 STARTING"]
    assert rows[4][4] == "1 name · ttl 45m"
    assert rows[5][1:] == ["runner", "vm_created", "runner:alpha", "vm_7c1e"]
    assert re.fullmatch(r"\d\d:\d\d:\d\d", rows[0][0])


def test_what_the_renderer_cannot_vouch_for_is_refused(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}/logs", headers=HX).text

    rows = _rows(body)
    assert rows[-2][2:] == ["unknown event", "runner:alpha", "refused: unknown event"]
    assert rows[-1][2:] == ["run_claimed", "runner:alpha", "refused: unexpected field token"]
    for value in ("vm_exploded", "rm -rf", "secret-alpha-value", "operator-token"):
        assert value not in body


def test_the_filters_narrow_the_table(client: TestClient) -> None:
    def events_of(kind: str) -> list[str]:
        body = client.get(f"/runs/{STARTED}/logs?kind={kind}", headers=HX).text
        return [row[2] for row in _rows(body)]

    assert set(events_of("api")) == {
        "run_created",
        "run_assigned",
        "run_transition",
        "credentials_issued",
    }
    assert "run_created" not in events_of("runner")
    assert events_of("errors") == ["network_denied", "unknown event", "run_claimed"]
    body = client.get(f"/runs/{STARTED}/logs?kind=errors", headers=HX).text
    assert "3 entries" in body
    assert re.search(r'logs__kind logs__kind--active"[^>]*>Errors</a>', body.replace("\n", " "))


def test_an_unknown_filter_is_refused(client: TestClient) -> None:
    assert client.get(f"/runs/{STARTED}/logs?kind=secrets").status_code == 422


def test_the_tab_without_htmx_comes_inside_the_shell(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}/logs").text

    assert "<html" in body
    assert "logs__timeline" in body


def test_export_hands_over_the_whole_timeline(client: TestClient) -> None:
    response = client.get(f"/runs/{STARTED}/logs/export")

    assert response.headers["content-type"] == "application/json"
    disposition = f'attachment; filename="{STARTED}-events.json"'
    assert response.headers["content-disposition"] == disposition
    assert json.loads(response.text) == RUN_EVENTS


def test_an_unknown_run_answers_with_the_api_error(client: TestClient) -> None:
    assert "not found" in client.get("/runs/run_missing/logs", headers=HX).text


def test_a_failed_transition_and_a_denied_call_are_errors() -> None:
    failed = events.log_row(
        _event("run_transition", {"from": "STARTED", "to": "FAILED", "reason": "timeout"}), {}
    )
    denied = events.log_row(
        _event(
            "mcp_call",
            {
                "server": "alpha",
                "tool": "read",
                "resource": "",
                "decision": "deny",
                "duration_ms": 3,
                "category": "files",
            },
            source="runner",
        ),
        {},
    )

    assert (failed.error, failed.detail) == (True, "STARTED \u2192 FAILED · timeout")
    assert (denied.error, denied.detail) == (True, "alpha/read · deny · 3ms")


def test_a_wrongly_typed_field_is_refused() -> None:
    row = events.log_row(_event("run_queued", {"position": "2"}), {})

    assert (row.refused, row.detail) == (True, "refused: field position has the wrong type")


def test_older_rows_without_the_new_fields_still_render() -> None:
    created = events.log_row(_event("run_created", {}), {})
    issued = events.log_row(_event("credentials_issued", {"names": ["a", "b"]}), {})

    assert (created.refused, created.detail) == (False, "\u2014")
    assert (issued.refused, issued.detail) == (False, "2 names")


def test_a_conflicted_merge_carries_no_counts_and_is_an_error() -> None:
    row = events.log_row(_event("merge_reported", {"outcome": "conflict", "conflicts": 1}), {})

    assert (row.refused, row.error, row.detail) == (False, True, "conflict · 1 conflict")
