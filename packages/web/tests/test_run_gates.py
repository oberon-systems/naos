import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stub_api import (
    IDLE_GATE,
    MCP_EVENTS,
    MCPPOL,
    MODEL_GATE,
    MODELPOL,
    NETPOL,
    NOW,
    RUNS,
    SHELLPOL,
    policy_row,
)

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
    assert f'hx-get="/runs/{STARTED}/gates/mcp"' in body
    assert f'hx-get="/runs/{STARTED}/gates/shell"' in body
    assert f'hx-get="/runs/{STARTED}/gates/model"' in body


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


def _shell(client: TestClient, run_id: str = STARTED, **params: str) -> str:
    response = client.get(f"/runs/{run_id}/gates/shell", params=params, headers=HX)
    assert response.status_code == 200, response.text
    body: str = response.text
    return body


def test_the_shell_view_reads_the_gate_from_the_audit(client: TestClient) -> None:
    body = _shell(client)

    active = r'logs__kind--active"\s+href="[^"]+"\s+hx-get="[^"]+"\s+hx-target="#overlay">'
    assert re.search(active + "Shell<", body)
    assert f'hx-get="/runs/{STARTED}/gates"' in body
    assert "SHELL POLICY" in body and SHELLPOL in body and ">stored</code>" in body
    assert ">list_dir</code>" in body and ">/naos/alpha</code>" in body
    assert "2 granted · 1 not granted" in body
    assert "All · 60" in body and "Allowed · 57" in body and "Denied · 3" in body
    groups = body.split("CALLS BY CAPABILITY ·", 1)[1].split("CALLS ·", 1)[0]
    order = [groups.index(name) for name in ("read_file", "git_diff", "list_dir")]
    assert order == sorted(order)


def test_shell_guest_text_is_inert_and_an_untyped_event_is_refused(client: TestClient) -> None:
    body = _shell(client)

    assert "<b>alpha</b>" not in body and "/naos/&lt;b&gt;alpha&lt;/b&gt;/../etc/hosts" in body
    assert "<script>" not in body
    assert "refused: unexpected field note" in body
    assert f'hx-get="/audit/evt_{51:032x}"' in body


def test_shell_calls_are_filtered_by_decision(client: TestClient) -> None:
    denied = _shell(client, decision="denied").split("CALLS ·", 1)[1]

    assert f"/audit/evt_{50:032x}" not in denied and f"/audit/evt_{51:032x}" in denied
    assert 'href="/runs/run_9f21c4/gates/shell?decision=allowed"' in denied


def test_a_run_without_a_shell_policy_grants_nothing(client: TestClient) -> None:
    body = _shell(client, WAITING)

    assert "grants nothing" in body and "No call yet" in body and ">/naos/beta</code>" in body
    assert "CALLS BY CAPABILITY ·" not in body


def test_a_pending_run_waits_for_its_shell_gate(client: TestClient) -> None:
    body = _shell(client, PENDING)

    assert "The Run has not started" in body and "CALLS ·" not in body


def test_a_shell_gate_without_calls_counts_nothing() -> None:
    gate: dict[str, Any] = {
        "policy_id": None,
        "document": None,
        "configured_at": None,
        "roots": [],
        "allowed": 0,
        "denied": 0,
        "called": [],
        "groups_total": 0,
        "groups": [],
    }

    view = gates.shell(RUNS[0], gate, [], "all", gates.PAGE)

    assert view.policy_tag == ("grants nothing", "amber") and view.configured == "never"
    assert [(tile.value, tile.note) for tile in view.tiles] == [
        ("0", "no capability is granted"),
        ("0", "no call was made"),
        ("0", "none granted"),
    ]


def test_each_gate_names_its_policy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(policy_row(NETPOL), "name", "network_alpha")
    monkeypatch.setitem(policy_row(SHELLPOL), "name", "shell_alpha")

    link = r'hx-get="/policies/{}"\s+hx-target="#overlay">{}</a>'
    assert re.search(link.format(NETPOL, "network_alpha"), _gates(client))
    assert re.search(link.format(SHELLPOL, "shell_alpha"), _shell(client))


def _model(client: TestClient, run_id: str = STARTED, **params: str) -> str:
    response = client.get(f"/runs/{run_id}/gates/model", params=params, headers=HX)
    assert response.status_code == 200, response.text
    body: str = response.text
    return body


def test_the_model_view_reads_the_gate_from_the_audit(client: TestClient) -> None:
    body = _model(client)

    active = r'logs__kind--active"\s+href="[^"]+"\s+hx-get="[^"]+"\s+hx-target="#overlay">'
    assert re.search(active + "Model<", body)
    assert "MODEL POLICY" in body and MODELPOL in body and ">stored</code>" in body
    assert 'hx-get="/secrets/alpha-key"' in body and "60/min" in body
    assert "All · 14" in body and "Allowed · 12" in body and "Denied · 2" in body
    assert "41.2 s" in body and "alpha / alpha-mini · limit 600 s" in body
    groups = body.split("CALLS BY MODEL ·", 1)[1].split("CALLS ·", 1)[0]
    assert groups.index("alpha-mini") < groups.index("beta-max")


def test_the_spend_is_drawn_against_the_budget_and_the_rate(client: TestClient) -> None:
    body = _model(client)

    assert "SPENT OVER THE RUN" in body
    assert "64 000 / 100 000 · 64%" in body and "9 800 / 10 000 · 98%" in body
    assert "60/min · peak 55/min" in body and "1 refused" in body
    assert 'class="spend__bar--red"' in body


def test_model_guest_text_is_inert_and_an_untyped_event_is_refused(client: TestClient) -> None:
    body = _model(client)

    assert "<b>alpha</b>" not in body and "&lt;b&gt;alpha&lt;/b&gt;-mini" in body
    assert "<script>" not in body
    assert "refused: unexpected field prompt" in body
    assert ">token budget spent</span>" in body
    assert f'hx-get="/audit/evt_{60:032x}"' in body


def test_model_calls_are_filtered_by_decision(client: TestClient) -> None:
    denied = _model(client, decision="denied").split("CALLS ·", 1)[1]

    assert f"/audit/evt_{60:032x}" not in denied and f"/audit/evt_{61:032x}" in denied
    assert 'href="/runs/run_9f21c4/gates/model?decision=allowed"' in denied


def test_a_run_without_model_calls_says_so(client: TestClient) -> None:
    body = _model(client, WAITING)

    assert "allows nothing" in body and "No call yet" in body
    assert "CALLS BY MODEL ·" not in body and "SPENT OVER THE RUN" not in body


def test_a_pending_run_waits_for_its_model_gate(client: TestClient) -> None:
    body = _model(client, PENDING)

    assert "The Run has not started" in body and "CALLS ·" not in body


def test_a_model_gate_without_calls_counts_nothing() -> None:
    gate = IDLE_GATE | {"document": MODEL_GATE["document"], "policy_id": MODELPOL}

    view = gates.model(RUNS[0], gate, [], "all", gates.PAGE)

    assert view.spend is None and view.policy_tag == ("stored", "grey")
    assert [(tile.value, tile.note) for tile in view.tiles] == [
        ("0", "calls the policy let through"),
        ("0", "no call was made"),
        (format.DASH, "no call was answered"),
    ]


def test_a_long_run_is_drawn_in_wider_buckets() -> None:
    minutes = [
        {
            "at": NOW + 60 * index,
            "provider": "alpha",
            "requests": 1,
            "denied": 0,
            "input_tokens": 10,
            "output_tokens": 1,
        }
        for index in range(0, 600, 3)
    ]
    gate = MODEL_GATE | {"minutes": minutes}

    spend = gates.model(RUNS[0], gate, [], "all", gates.PAGE).spend

    assert spend is not None and len(spend.strips[0].bars) <= gates.BARS
    assert spend.input == "2 000 / 100 000 · 2%"


def test_the_model_policy_is_named(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(policy_row(MODELPOL), "name", "model_alpha")

    link = r'hx-get="/policies/{}"\s+hx-target="#overlay">{}</a>'
    assert re.search(link.format(MODELPOL, "model_alpha"), _model(client))


def _mcp(client: TestClient, run_id: str = STARTED, **params: str) -> str:
    response = client.get(f"/runs/{run_id}/gates/mcp", params=params, headers=HX)
    assert response.status_code == 200, response.text
    body: str = response.text
    return body


def _calls(body: str) -> str:
    return body.split("CALLS ·", 1)[1]


def test_the_mcp_view_reads_the_gate_from_the_audit(client: TestClient) -> None:
    body = _mcp(client)

    active = r'logs__kind--active"\s+href="[^"]+"\s+hx-get="[^"]+"\s+hx-target="#overlay">'
    assert re.search(active + "MCP<", body)
    assert "MCP POLICY" in body and ">temporary</code>" in body and "edited from" in body
    assert f'hx-get="/policies/{MCPPOL}"' in body and 'hx-get="/mcp/alpha"' in body
    assert "3 · 1 deny · 1 with a budget" in body and "2 edits" not in body
    assert "All · 212" in body and "Allowed · 208" in body and "Denied · 4" in body
    assert "Secrets · 3" in body and "agent-key 2 · 1 refused" in body
    groups = body.split("CALLS BY SERVER AND TOOL · 4", 1)[1].split("CALLS ·", 1)[0]
    tools = re.findall(r'class="gates__host">\s*([^<\s]+)', groups)
    assert tools == ["unknown", "delete", "get", "search"]


def test_the_mcp_timeline_marks_every_gate_event(client: TestClient) -> None:
    body = _mcp(client)

    assert "MCP OVER THE RUN" in body and "lanes__bar--denied" in body
    lanes = re.findall(r'class="lanes__name[^"]*"[^>]*>([^<]+)<', body)
    assert lanes == ["policy", "alpha", "beta", "secrets", "shell", "unknown"]
    assert "GATE EVENTS · 6" in body and "the broker serves the VM" in body
    assert "&lt;b&gt;duplicate&lt;/b&gt; request id" in body and "<b>duplicate" not in body
    assert ">alpha-token</code>" in body
    assert f'hx-get="/audit/evt_{70:032x}"' in body and f'href="/audit/evt_{72:032x}"' in body


def test_a_rule_budget_shows_what_it_spent(client: TestClient) -> None:
    body = _mcp(client)

    budgets = body.split("RULE BUDGETS · 1", 1)[1].split("CALLS BY SERVER", 1)[0]
    assert "rule 2" in budgets and "name = agent-key" in budgets
    assert "5 for the Run" in budgets and "2 / 5" in budgets and "width: 40%" in budgets


def test_an_mcp_call_says_why_it_was_denied_and_never_shows_arguments(
    client: TestClient,
) -> None:
    calls = _calls(_mcp(client))

    assert "denied by rule 0" in calls and "no rule allows this call" in calls
    assert "docs://alpha/" in calls and "refused: unexpected field arguments" in calls
    assert "<b>search</b>" not in calls
    assert "Arguments and results of a call are never logged" in calls


def test_mcp_calls_are_filtered_by_decision(client: TestClient) -> None:
    denied = _calls(_mcp(client, decision="denied"))

    assert f"/audit/evt_{84:032x}" in denied and f"/audit/evt_{85:032x}" not in denied
    assert 'href="/runs/run_9f21c4/gates/mcp?decision=secrets"' in denied


def test_the_secrets_view_names_each_secret_and_never_a_value(client: TestClient) -> None:
    body = _mcp(client, decision="secrets")

    asked = body.split("SECRETS THE AGENT ASKED FOR · 2", 1)[1].split("CALLS TO SECRETS", 1)[0]
    assert 'hx-get="/secrets/agent-key"' in asked and 'hx-get="/secrets/prod-key"' not in asked
    assert ">not granted<" in asked and "2 of 5 for the Run" in asked
    assert "A value read belongs to the agent" in body
    calls = body.split("CALLS TO SECRETS · 3", 1)[1]
    assert calls.index("agent-key") < calls.index("prod-key")
    assert f"/audit/evt_{84:032x}" not in calls
    assert "secret-alpha-value" not in body


def test_a_secret_read_is_paired_with_its_call() -> None:
    calls = [row for row in MCP_EVENTS if row["event"] == "mcp_call"]
    reads = [row for row in MCP_EVENTS if row["event"] == "secret_read"]

    assert gates.paired(calls, reads) == {
        f"evt_{80:032x}": "prod-key",
        f"evt_{83:032x}": "agent-key",
    }
    assert gates.paired(calls, []) == {}


def test_a_run_without_mcp_calls_says_so(client: TestClient) -> None:
    body = _mcp(client, WAITING)

    assert "allows nothing" in body and "No call yet" in body
    assert "CALLS BY SERVER AND TOOL" not in body and "RULE BUDGETS" not in body


def test_a_pending_run_waits_for_its_mcp_gate(client: TestClient) -> None:
    body = _mcp(client, PENDING)

    assert "The Run has not started" in body and "MCP OVER THE RUN" not in body
