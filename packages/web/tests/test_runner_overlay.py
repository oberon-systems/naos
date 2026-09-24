import re

from fastapi.testclient import TestClient

ALPHA = "rnr_8c1f42aa"
DELTA = "rnr_91ba35dd"
EPSILON = "rnr_6f0d1811"
HX = {"HX-Request": "true"}


def _facts(body: str) -> dict[str, str]:
    pairs = re.findall(r"<dt>([^<]+)</dt>\s*<dd[^>]*>([^<]+)</dd>", body)
    return {label: value.strip() for label, value in pairs}


def _overlay(client: TestClient, path: str) -> str:
    body: str = client.get(path, headers=HX).text
    return body


def test_every_runner_row_opens_the_overlay(client: TestClient) -> None:
    body = client.get("/runs").text

    opens = re.findall(r'hx-get="/runners/(rnr_\w+)"', body)
    assert len(opens) == 5
    assert all(target == "#overlay" for target in re.findall(r'hx-target="(#overlay)"', body))


def test_the_overlay_has_three_tabs(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}")

    tabs = re.findall(r'class="run__tab[^"]*"\s+href="([^"]+)"', body)
    assert tabs == [f"/runners/{ALPHA}", f"/runners/{ALPHA}/runs", f"/runners/{ALPHA}/audit"]
    assert re.search(rf'run__tab--active"\s+href="/runners/{ALPHA}"', body)


def test_the_header_names_the_runner_and_its_agent(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}")

    assert ">alpha</h2>" in body
    assert "naos-runner v0.1.0" in body
    assert "zone-a" in body
    assert "2/2 slots" in body
    assert "tone-green" in body


def test_overview_shows_what_the_agent_reported(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}")
    facts = _facts(body)

    assert facts["Host"] == "alpha-01.example.com"
    assert facts["Address"] == "192.0.2.11"
    assert facts["Platform"] == "linux/amd64 \u00b7 Ubuntu 24.04"
    assert facts["Agent"] == "naos-runner v0.1.0"
    assert re.findall(r'class="runner__label">([^<]+)<', body) == ["ci", "amd64"]


def test_a_runner_that_reported_nothing_shows_dashes(client: TestClient) -> None:
    facts = _facts(_overlay(client, f"/runners/{DELTA}"))

    for label in ("Host", "Address", "Zone", "Platform", "Agent"):
        assert facts[label] == "\u2014"


def test_overview_names_the_lease_and_the_token_windows(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}")
    facts = _facts(body)

    assert "lease_5d2a91" in body
    assert "52s of 60s left" in body
    assert facts["Token"] == "rotates in 11h 42m"  # noqa: S105  a rotation window, not a secret
    assert facts["Previous token"] == "valid 4h more"
    assert facts["Heartbeat"] == "2s ago \u00b7 every 20s"
    assert facts["State"] == "LIVE \u00b7 heartbeat under 30s"


def test_overview_reads_the_slots_by_meaning(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}")

    assert 'class="runner__load tone-blue">2/2<' in body
    assert "0 free" in body
    held = re.findall(r'class="panel__run runner__run"[^>]*hx-get="/runs/(run_\w+)"', body)
    assert held == ["run_9f21c4", "run_2f55d1"]


def test_the_runs_tab_lists_held_runs_first_and_opens_them(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}/runs")

    opened = re.findall(r'<tr class="runs-table__row"[^>]*hx-get="/runs/(run_\w+)"', body)
    assert opened == ["run_9f21c4", "run_2f55d1", "run_0d4492"]
    assert "2 in the slots \u00b7 1 finished" in body


def test_the_audit_tab_links_each_run_to_its_popup(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}/audit")

    assert "run_claimed" in body
    assert "token_rotated" in body
    assert "lease_acquired" in body
    assert 'hx-get="/runs/run_9f21c4"' in body
    assert ">#128</a>" in body


def test_the_audit_tab_filters_by_type(client: TestClient) -> None:
    lease = _overlay(client, f"/runners/{ALPHA}/audit?scope=lease")
    errors = _overlay(client, f"/runners/{ALPHA}/audit?scope=errors")

    assert "lease_acquired" in lease and "token_rotated" not in lease
    assert "run_transition" in errors and "lease_acquired" not in errors
    assert "logs__row--error" in errors


def test_the_audit_tab_filters_by_run(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}/audit?run=run_9f21c4")

    assert "run_claimed" in body and "run_transition" in body
    assert "token_rotated" not in body
    assert re.search(r'runner__menu-toggle">\s*#128<', body)
    active = re.findall(r'runner__menu-item--active"\s+href="([^"]+)"', body)
    assert active == [f"/runners/{ALPHA}/audit?scope=all&run=run_9f21c4"]


def test_an_unknown_run_filter_shows_every_run(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}/audit?run=run_forged")

    assert "token_rotated" in body
    assert re.search(r'runner__menu-toggle">\s*All runs<', body)


def test_the_run_menu_lists_every_run_of_the_runner(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}/audit?scope=errors")

    runs = re.findall(r'href="/runners/[^"]+/audit\?scope=errors&run=(run_\w+)"', body)
    assert runs == ["run_9f21c4", "run_2f55d1", "run_0d4492"]
    assert "<select" not in body


def test_the_audit_exports_as_json(client: TestClient) -> None:
    response = client.get(f"/runners/{ALPHA}/audit/export")

    assert response.headers["content-type"] == "application/json"
    assert f'filename="{ALPHA}-audit.json"' in response.headers["content-disposition"]


def test_the_overlay_offers_drain_and_revoke(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}")

    assert f'hx-get="/runners/{ALPHA}/drain"' in body
    assert f'hx-get="/runners/{ALPHA}/revoke"' in body


def test_a_revoked_runner_offers_neither(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{EPSILON}")

    assert "/drain" not in body
    assert "/revoke" not in body
    assert _facts(body)["State"] == "REVOKED \u00b7 token refused"


def test_the_overlay_is_a_fragment_not_a_page(client: TestClient) -> None:
    body = _overlay(client, f"/runners/{ALPHA}")

    assert "<!DOCTYPE html>" not in body
    assert '<header class="topbar">' not in body


def test_without_htmx_the_overlay_comes_over_the_list(client: TestClient) -> None:
    body = client.get(f"/runners/{ALPHA}").text

    assert '<header class="topbar">' in body
    assert 'class="panel panel--run panel--runner"' in body
    assert 'id="runners"' in body


def test_an_unknown_runner_answers_in_the_overlay(client: TestClient) -> None:
    response = client.get("/runners/rnr_nope", headers=HX)

    assert response.status_code == 200
    assert "<!DOCTYPE html>" not in response.text
    assert "knows no runner" in response.text


def test_the_overlay_closes(client: TestClient) -> None:
    assert client.get("/overlay/close").text == ""


def test_the_overlay_never_renders_a_token(client: TestClient) -> None:
    for tab in ("", "/runs", "/audit"):
        body = _overlay(client, f"/runners/{ALPHA}{tab}")

        assert "operator-token" not in body
        assert "Bearer" not in body
        assert "token_hash" not in body
