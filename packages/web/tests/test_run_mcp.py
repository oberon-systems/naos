import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stub_api import MCPPOL, WRITES

HX = {"HX-Request": "true"}
RUN = "/runs/run_9f21c4"
EDIT = {
    "source": "edit",
    "held": "2",
    "rules.0.effect": "deny",
    "rules.0.server": "alpha",
    "rules.0.target": "delete",
    "rules.1.effect": "allow",
    "rules.1.server": "alpha",
    "rules.1.target": "search",
    "rules.2.effect": "allow",
    "rules.2.server": "beta",
    "rules.2.target": "search",
    "rules.2.per_minute": "30",
    "grants.0.name": "agent-key",
    "grants.0.reads": "5",
    "grants.1.name": "alpha-token",
    "grants.1.reads": "1",
    "keep": "temporary",
}


@pytest.fixture(autouse=True)
def writes() -> Iterator[list[Any]]:
    WRITES.clear()
    yield WRITES
    WRITES.clear()


def flat(body: str) -> str:
    return re.sub(r"\s+", " ", body)


def test_the_overview_shows_what_the_run_holds(client: TestClient) -> None:
    body = flat(client.get(RUN, headers=HX).text)

    assert "SERVERS HELD NOW \u00b7 2" in body
    assert "edited from mcppol_5b2d0" in body
    assert "212 \u00b7 4 denied" in body
    assert "2 \u00b7 agent-key 2 of 5" in body
    assert "4 s after the change" in body
    assert "3 \u00b7 1 deny" in body
    assert "added server alpha \u00b7 took away beta \u00b7 took away shell" in body
    assert f'href="{RUN}/mcp"' in body
    assert f'hx-get="/policies/{MCPPOL}"' in body


def test_a_run_without_mcp_has_no_mcp_card(client: TestClient) -> None:
    body = client.get("/runs/run_7c08ab", headers=HX).text

    assert "SERVERS HELD NOW" not in body


def test_the_edit_folds_what_the_run_holds(client: TestClient) -> None:
    body = flat(client.get(f"{RUN}/mcp", headers=HX).text)
    added = flat(client.post(f"{RUN}/mcp", data=EDIT | {"step": "add:rules"}, headers=HX).text)
    unfolded = client.post(f"{RUN}/mcp", data=EDIT | {"step": "unfold"}, headers=HX).text

    assert "2 unchanged rules are folded" in body
    assert 'type="hidden" name="rules.0.target" value="delete"' in body
    assert "SECRETS GRANTED TO AGENTS \u00b7 1" in body
    assert "RULES \u00b7 4" in added and "new</span>" in added
    assert 'name="unfolded"' in unfolded and "unchanged" not in unfolded
    assert WRITES == []


def test_review_names_what_the_agent_gains_then_apply_sends_it(
    client: TestClient, writes: list[Any]
) -> None:
    review = flat(client.post(f"{RUN}/mcp", data=EDIT | {"step": "review"}, headers=HX).text)
    moved = client.post(f"{RUN}/mcp/apply", data=EDIT, headers=HX)

    assert "Change the MCP policy of Run #128?" in review
    assert "1 rule \u00b7 30 calls/min" in review
    assert "1 read" in review
    assert "The agent can call beta and read alpha-token" in review
    assert moved.headers["HX-Redirect"] == RUN
    [(method, path, sent, _)] = writes
    assert (method, path, sent["kind"]) == ("POST", f"{RUN}/policies", "mcp")
    assert sent["document"]["rules"][2] == {
        "server": "beta",
        "effect": "allow",
        "tool": "search",
        "max_calls_per_minute": 30,
    }
    assert "save" not in sent


def test_save_as_a_new_policy_sends_the_name(client: TestClient, writes: list[Any]) -> None:
    fields = EDIT | {"keep": "save", "save_name": "alpha-search-beta"}
    review = client.post(f"{RUN}/mcp", data=fields | {"step": "review"}, headers=HX).text
    client.post(f"{RUN}/mcp/apply", data=fields, headers=HX)

    assert "saved as a new policy" in review
    assert writes[0][2]["save"] is True and writes[0][2]["name"] == "alpha-search-beta"


def test_a_stored_policy_shows_what_the_run_gets(client: TestClient, writes: list[Any]) -> None:
    listed = client.get(f"{RUN}/mcp", params={"source": "stored"}, headers=HX).text
    fields = {"source": "stored", "policy_id": MCPPOL}
    picked = flat(client.post(f"{RUN}/mcp", data=fields, headers=HX).text)
    review = client.post(f"{RUN}/mcp", data=fields | {"step": "review"}, headers=HX).text
    client.post(f"{RUN}/mcp/apply", data=fields, headers=HX)

    assert f'value="{MCPPOL}"' in listed
    assert "WHAT THE RUN GETS" in picked
    assert '<dt>Taken away</dt> <dd> <span class="profile__tag">alpha</span>' in picked
    assert "stored policy" in review
    assert writes == [("POST", f"{RUN}/policies", {"kind": "mcp", "policy_id": MCPPOL}, None)]


def test_a_review_without_a_pick_asks_for_one(client: TestClient) -> None:
    body = client.post(f"{RUN}/mcp", data={"source": "stored", "step": "review"}, headers=HX).text

    assert "pick a stored mcp policy" in body
