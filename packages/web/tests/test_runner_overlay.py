import re

from fastapi.testclient import TestClient

ALPHA = "rnr_8c1f42aa"
EPSILON = "rnr_6f0d1811"
HX = {"HX-Request": "true"}


def test_every_runner_row_opens_the_overlay(client: TestClient) -> None:
    body = client.get("/runs").text

    opens = re.findall(r'hx-get="/runners/(rnr_\w+)"', body)
    assert len(opens) == 5
    assert all(target == "#overlay" for target in re.findall(r'hx-target="(#overlay)"', body))


def test_the_overlay_reads_the_runner(client: TestClient) -> None:
    body = client.get(f"/runners/{ALPHA}", headers=HX).text

    assert 'class="panel"' in body
    assert ">alpha</h2>" in body
    assert ALPHA in body
    assert "2 / 2 busy" in body
    assert "0 free" in body
    assert "lease 52s of 60s" in body


def test_the_overlay_lists_the_runs_on_the_lease(client: TestClient) -> None:
    body = client.get(f"/runners/{ALPHA}", headers=HX).text

    assert re.findall(r"<span>#(\d+)</span>", body) == ["128", "123"]
    assert ">STARTED</span>" in body and ">STOPPING</span>" in body


def test_the_overlay_lists_recent_events(client: TestClient) -> None:
    body = client.get(f"/runners/{ALPHA}", headers=HX).text

    for event in ("run_claimed", "token_rotated", "lease_acquired"):
        assert event in body
    assert "lease_5d2a91" in body


def test_a_runner_with_no_lease_still_opens(client: TestClient) -> None:
    body = client.get(f"/runners/{EPSILON}", headers=HX).text

    assert ">epsilon</h2>" in body
    assert "no runs on this lease" in body
    assert "revoked 2h ago" in body


def test_the_overlay_is_a_fragment_not_a_page(client: TestClient) -> None:
    body = client.get(f"/runners/{ALPHA}", headers=HX).text

    assert "<!DOCTYPE html>" not in body
    assert '<header class="topbar">' not in body


def test_without_htmx_the_overlay_comes_inside_the_shell(client: TestClient) -> None:
    body = client.get(f"/runners/{ALPHA}").text

    assert '<header class="topbar">' in body
    assert 'class="panel"' in body


def test_an_unknown_runner_answers_in_the_overlay(client: TestClient) -> None:
    response = client.get("/runners/rnr_nope", headers=HX)

    assert response.status_code == 200
    assert "<!DOCTYPE html>" not in response.text
    assert "knows no runner" in response.text


def test_the_overlay_closes(client: TestClient) -> None:
    assert client.get("/overlay/close").text == ""


def test_the_overlay_never_renders_a_token(client: TestClient) -> None:
    body = client.get(f"/runners/{ALPHA}", headers=HX).text

    assert "operator-token" not in body
    assert "Bearer" not in body
    assert "token_hash" not in body
