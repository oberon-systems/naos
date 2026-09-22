import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from stub_api import RUNS, WRITES

STARTED = "run_9f21c4"
PENDING = "run_3a90f8"
FAILED = "run_1e0c6b"
HX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def writes() -> Iterator[list[tuple[str, str, dict[str, object], str | None]]]:
    WRITES.clear()
    yield WRITES
    WRITES.clear()


def _facts(body: str) -> dict[str, str]:
    pairs = re.findall(r"<dt>([^<]+)</dt>\s*<dd[^>]*>([^<]+)</dd>", body)
    return {label: value.strip() for label, value in pairs}


def test_every_run_row_opens_the_overlay(client: TestClient) -> None:
    body = client.get("/runs").text

    opened = set(re.findall(r'<tr class="runs-table__row"[^>]*hx-get="/runs/(run_\w+)"', body))
    assert opened == {row["id"] for row in RUNS}
    assert re.search(r'hx-get="/runs/run_0d4492"\s+hx-target="#overlay">Diff</a>', body)
    assert 'href="/runs/run_1e0c6b/logs">Logs</a>' in body


def test_the_overlay_reads_the_run(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}", headers=HX).text

    assert "Run #128</h2>" in body
    assert ">STARTED</span>" in body
    assert "started 2m ago" in body
    assert "00:02:14" in body
    for tab in ("Overview", "Terminal", "Logs &amp; Audit"):
        assert f">{tab}</a>" in body
    facts = _facts(body)
    assert facts["State"] == "STARTED \u00b7 claimed by alpha"
    assert facts["Image"] == "naos-agents \u00b7 ab12cd34ef56"
    assert facts["Profile"] == "build-small \u00b7 2 vCPU \u00b7 2 GiB"
    assert facts["Attempt"] == "\u2014"


def test_the_policy_card_reads_the_run_spec(client: TestClient) -> None:
    facts = _facts(client.get(f"/runs/{STARTED}", headers=HX).text)

    assert facts["Merge policy"] == "ask \u00b7 manual approval"
    assert facts["Timeouts"] == "run 1h \u00b7 idle \u2014"
    assert facts["Network egress"] == "allowlist \u00b7 3 hosts"
    assert facts["Secrets"] == "1 bound \u00b7 never logged"
    assert facts["Lease fencing"] == "on \u00b7 60s ttl"
    for gap in ("Approvals", "Artifacts"):
        assert facts[gap] == "\u2014"


def test_a_run_without_policies_says_so(client: TestClient) -> None:
    facts = _facts(client.get(f"/runs/{PENDING}", headers=HX).text)

    assert facts["Network egress"] == "no policy"
    assert facts["Secrets"] == "none bound"
    assert facts["Lease fencing"] == "off \u00b7 no lease held"


def test_the_runner_card_opens_the_runner(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}", headers=HX).text

    assert 'hx-get="/runners/rnr_8c1f42aa"' in body
    assert "2 / 2 slots" in body
    assert "lease 52s of 60s \u00b7 renewed on every heartbeat" in body
    assert _facts(body)["Heartbeat"] == "2s ago"


def test_the_lifecycle_marks_done_current_and_future(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}", headers=HX).text

    steps = re.findall(
        r'timeline__step--(\w+)">\s*<span class="timeline__dot"></span>\s*'
        r'<span class="timeline__name">(\w+)</span>',
        body,
    )
    assert steps == [
        ("done", "PENDING"),
        ("done", "STARTING"),
        ("current", "STARTED"),
        ("future", "STOPPING"),
        ("future", "COLLECTING"),
        ("future", "WAITING_MERGE"),
        ("future", "COMPLETED"),
    ]


def test_a_failed_run_ends_its_lifecycle_on_the_failure(client: TestClient) -> None:
    body = client.get(f"/runs/{FAILED}", headers=HX).text

    assert re.findall(r'timeline__step--(\w+)"', body) == ["exit"]
    assert ">FAILED</span>" in body


def test_only_a_stoppable_run_offers_stop_and_it_asks_first(client: TestClient) -> None:
    started = client.get(f"/runs/{STARTED}", headers=HX).text
    failed = client.get(f"/runs/{FAILED}", headers=HX).text

    assert 'hx-get="/runs/run_9f21c4/stop"' in started
    assert "hx-confirm" not in started
    assert 'hx-get="/runs/run_1e0c6b/stop"' not in failed
    assert "Rerun</a>" in failed


def test_the_stop_confirm_asks_before_it_posts(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}/stop", headers=HX).text

    assert "Stop run?</h2>" in body
    assert f"Are you sure you want to stop {STARTED}?" in body
    assert f'hx-post="/runs/{STARTED}/stop"' in body
    assert 'class="button button--danger" type="submit">Yes' in body
    assert f'href="/runs/{STARTED}"' in body
    assert ">No</a>" in body


def test_the_rerun_confirm_lists_what_the_new_run_gets(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}/rerun", headers=HX).text

    assert "Rerun?</h2>" in body
    assert f"Are you sure you want to rerun {STARTED}?" in body
    assert "A new run will be created with these options:" in body
    facts = _facts(body)
    assert list(facts) == ["Spec", "Image", "Profile", "Runner", "Timeouts"]
    assert facts["Runner"] == "any live runner with a free slot"


def test_a_confirm_without_htmx_comes_inside_the_shell(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}/stop").text

    assert "<!DOCTYPE html>" in body
    assert "Stop run?</h2>" in body


def test_a_panel_asked_for_as_a_page_closes_back_to_the_list(client: TestClient) -> None:
    page = client.get(f"/runs/{STARTED}").text
    fragment = client.get(f"/runs/{STARTED}", headers=HX).text

    assert 'hx-get="/overlay/close"' in fragment
    assert 'hx-get="/overlay/close"' not in page
    assert 'href="/runs"' in page


def test_a_confirm_asked_for_as_a_page_closes_back_to_the_run(client: TestClient) -> None:
    page = client.get(f"/runs/{STARTED}/stop").text
    fragment = client.get(f"/runs/{STARTED}/stop", headers=HX).text

    assert f'hx-get="/runs/{STARTED}"' in fragment
    assert f'hx-get="/runs/{STARTED}"' not in page
    assert f'href="/runs/{STARTED}"' in page


def test_stopping_goes_through_the_api_and_shows_the_run_again(
    client: TestClient, writes: list[tuple[str, str, dict[str, object], str | None]]
) -> None:
    body = client.post(f"/runs/{STARTED}/stop", headers=HX).text

    assert [(method, path) for method, path, *_ in writes] == [("POST", f"/runs/{STARTED}/stop")]
    assert ">STOPPING</span>" in body
    assert "<!DOCTYPE html>" not in body


def test_without_htmx_stop_comes_back_to_the_run(client: TestClient) -> None:
    response = client.post(f"/runs/{STARTED}/stop", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/runs/{STARTED}"


def test_rerun_posts_the_spec_under_the_rendered_key(
    client: TestClient, writes: list[tuple[str, str, dict[str, object], str | None]]
) -> None:
    body = client.get(f"/runs/{STARTED}/rerun", headers=HX).text
    key = re.findall(r'name="key" value="(\w+)"', body)[0]

    first = client.post(f"/runs/{STARTED}/rerun", data={"key": key}, headers=HX)
    client.post(f"/runs/{STARTED}/rerun", data={"key": key}, headers=HX)

    assert first.headers["HX-Redirect"] == "/runs/run_a0c311"
    assert [(path, sent) for _, path, _, sent in writes] == [("/runs", key), ("/runs", key)]
    assert writes[0][2] == RUNS[0]["spec"]


def test_the_overlay_is_a_fragment_not_a_page(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}", headers=HX).text

    assert "<!DOCTYPE html>" not in body
    assert '<header class="topbar">' not in body


def test_without_htmx_the_overlay_comes_inside_the_shell(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}").text

    assert '<header class="topbar">' in body
    assert "panel--run" in body


def test_new_run_is_not_taken_for_a_run_id(client: TestClient) -> None:
    assert "panel--dialog" in client.get("/runs/new", headers=HX).text


def test_an_unknown_run_answers_in_the_overlay(client: TestClient) -> None:
    response = client.get("/runs/run_nope", headers=HX)

    assert response.status_code == 200
    assert "<!DOCTYPE html>" not in response.text
    assert "the api answered 404" in response.text


def test_the_overlay_never_renders_a_secret(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}", headers=HX).text

    assert "alpha-token" not in body
    assert "operator-token" not in body
    assert "Bearer" not in body
    assert "Authorization" not in body
