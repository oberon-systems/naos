import re

from fastapi.testclient import TestClient
from stub_api import NETPOL, NOW, RUNS

from naos_web import format, gates

STARTED = "run_9f21c4"
WAITING = "run_5be317"
PENDING = "run_3a90f8"
HX = {"HX-Request": "true"}


def _gates(client: TestClient, run_id: str = STARTED, **params: str) -> str:
    response = client.get(f"/runs/{run_id}/gates", params=params, headers=HX)
    assert response.status_code == 200, response.text
    body: str = response.text
    return body


def _requests(body: str) -> str:
    return body.split("REQUESTS ·", 1)[1]


def test_every_run_tab_bar_offers_the_gates_tab(client: TestClient) -> None:
    for path in ("", "/terminal", "/logs"):
        body = client.get(f"/runs/{STARTED}{path}", headers=HX).text
        assert f'hx-get="/runs/{STARTED}/gates"' in body, path


def test_the_network_view_reads_the_gate_from_the_audit(client: TestClient) -> None:
    body = _gates(client)

    assert "run__tab run__tab--active" in body and ">Gates</a>" in body
    assert "NETWORK POLICY" in body and NETPOL in body and ">stored</code>" in body
    assert "allow 1 · deny 0" in body
    assert "All · 44" in body and "Allowed · 41" in body and "Denied · 3" in body
    hosts = body.split("HOSTS ·", 1)[1].split("REQUESTS ·", 1)[0]
    order = [hosts.index(host) for host in ("private", "internal.example.com", "alpha.example")]
    assert order == sorted(order)


def test_the_gates_of_the_run_are_switched_between(client: TestClient) -> None:
    body = _gates(client)

    active = r'logs__kind--active"\s+href="[^"]+"\s+hx-get="[^"]+"\s+hx-target="#overlay">'
    assert re.search(active + "Network<", body)
    assert 'logs__kind--idle">MCP</span>' in body and 'logs__kind--idle">Model</span>' in body
    assert ">Shell<" not in body


def test_guest_text_is_inert_and_an_untyped_event_is_refused(client: TestClient) -> None:
    body = _gates(client)

    assert "<b>private</b>" not in body and "&lt;b&gt;private&lt;/b&gt;.example.com" in body
    assert "<script>" not in body
    assert "refused: unexpected field note" in body
    assert f'hx-get="/audit/evt_{41:032x}"' in body


def test_requests_are_filtered_by_decision_and_by_host(client: TestClient) -> None:
    denied = _requests(_gates(client, decision="denied"))
    by_host = _requests(_gates(client, host="alpha.example.com"))

    assert f"/audit/evt_{40:032x}" not in denied and f"/audit/evt_{41:032x}" in denied
    assert f"/audit/evt_{40:032x}" in by_host and f"/audit/evt_{41:032x}" not in by_host
    assert "Every host" in by_host


def test_a_run_whose_policy_allows_nothing_says_so(client: TestClient) -> None:
    body = _gates(client, WAITING)

    assert "allows nothing" in body and "No request yet" in body
    assert "HOSTS ·" not in body


def test_a_pending_run_waits_for_its_gate(client: TestClient) -> None:
    body = _gates(client, PENDING)

    assert "The Run has not started" in body and "REQUESTS ·" not in body


def test_a_run_that_has_not_started_has_nothing_to_count() -> None:
    run = RUNS[0] | {"status": "PENDING", "runner": None, "policy_history": []}
    gate = {
        "policy_id": NETPOL,
        "document": {"allow": [{"host": "alpha.example.com"}]},
        "configured_at": None,
        "allowed": 0,
        "denied": 0,
        "hosts_total": 0,
        "hosts": [],
    }

    view = gates.network(run, gate, [], "all", gates.PAGE, None)

    assert not view.started
    assert view.configured == "not yet · when the runner starts the VM"
    assert [tile.value for tile in view.tiles] == [format.DASH] * 3
    assert view.policy_tag == ("stored", "grey")


def test_a_live_edit_shows_on_the_policy_card() -> None:
    history = [{"kind": "network", "created_at": NOW - 10}, {"kind": "shell", "created_at": NOW}]
    run = RUNS[0] | {"policy_history": history}
    gate = {
        "policy_id": None,
        "document": {"allow": [{"host": "alpha.example.com"}], "deny": []},
        "configured_at": NOW - 5,
        "allowed": 0,
        "denied": 0,
        "hosts_total": 0,
        "hosts": [],
    }

    view = gates.network(run, gate, [], "all", gates.PAGE, None)

    assert view.policy_tag == ("temporary", "blue")
    assert view.changed.startswith("1 edit · last ")


def test_load_older_asks_for_the_next_page_only_when_one_is_full() -> None:
    gate = {
        "policy_id": NETPOL,
        "document": {"allow": [{"host": "alpha.example.com"}]},
        "configured_at": NOW,
        "allowed": 2,
        "denied": 0,
        "hosts_total": 1,
        "hosts": [],
    }
    row = {
        "id": "evt_" + "a" * 32,
        "at": NOW,
        "source": "runner",
        "event": "network_allowed",
        "actor": "runner",
        "data": {"protocol": "https", "host": "alpha.example.com", "rule": "allow[0]"},
    }

    full = gates.network(RUNS[0], gate, [row, row], "all", 2, None)
    short = gates.network(RUNS[0], gate, [row], "all", 2, None)

    assert full.more == 2 + gates.PAGE and short.more is None
