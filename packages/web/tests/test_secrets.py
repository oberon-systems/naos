import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from stub_api import MCPSRV, MODELPOL, NOW, WRITES

HX = {"HX-Request": "true"}
ROW = '<tr class="runs-table__row"'
VALUE = "alpha-secret-value-0001"
DAY = 86400


@pytest.fixture(autouse=True)
def writes() -> Iterator[list[Any]]:
    WRITES.clear()
    yield WRITES
    WRITES.clear()


def flat(body: str) -> str:
    return re.sub(r"\s+", " ", body)


def test_the_nav_places_secrets_between_policies_and_audit(client: TestClient) -> None:
    body = client.get("/secrets").text

    assert re.findall(r'class="nav__item[^"]*"\s+href="(/[a-z]+)"', body)[-3:] == [
        "/policies",
        "/secrets",
        "/audit",
    ]


def test_the_tiles_count_use_expiry_and_holders(client: TestClient) -> None:
    body = client.get("/secrets", params={"state": "expired"}).text

    assert re.findall(r'class="tile__number">([^<]+)<', body) == ["4", "3", "2", "1"]
    assert "4 secrets \u00b7 named by 3 \u00b7 held by 1 run" in body
    assert "within 7 days \u00b7 1 expired" in body


def test_a_row_shows_state_usage_rotation_and_holders(client: TestClient) -> None:
    body = flat(client.get("/secrets").text)

    assert body.count(ROW) == 4
    assert ">VALID</span>" in body and ">EXPIRING</span>" in body and ">EXPIRED</span>" in body
    assert "12d left" in body and "since 2d" in body and "no expiry" in body
    assert '<span class="profile__tag">reg</span>' in body
    assert '<span class="profile__tag profile__tag--off">grant</span>' in body
    assert "1 server \u00b7 issue refused" in body and "1 model policy" in body
    assert "unused \u00b7 can be deleted" in body
    assert "by operator" in body and "never rotated" in body
    assert "#128" in body and "none open" in body
    assert body.count(">Rotate</a>") == 4


def test_filters_and_search_narrow_the_table_through_the_api(client: TestClient) -> None:
    assert client.get("/secrets", params={"state": "expired"}).text.count(ROW) == 1
    assert client.get("/secrets", params={"state": "used"}).text.count(ROW) == 3
    assert client.get("/secrets", params={"state": "unused"}).text.count(ROW) == 1
    assert client.get("/secrets", params={"q": MODELPOL[:12]}).text.count(ROW) == 1
    assert "no secret matches" in client.get("/secrets", params={"q": "missing"}).text


def test_the_overview_reads_metadata_and_who_names_and_holds_it(client: TestClient) -> None:
    body = flat(client.get("/secrets/alpha-token", headers=HX).text)

    assert ">VALID</span>" in body and "sec_alpha" in body
    assert "rotated 3d ago" in body and "expires in 12d" in body
    assert "named by 1 \u00b7 held by 1 run" in body
    assert "3d ago \u00b7 by operator \u00b7 2 issues since" in body
    assert "NAMED BY \u00b7 1" in body and MCPSRV in body
    assert f'hx-get="/policies/{MCPSRV}"' not in body
    assert "mcp registry \u00b7 credential of server alpha" in body
    assert "HELD BY \u00b7 1 RUN" in body and 'hx-get="/runs/run_9f21c4"' in body
    for act in ("rotate", "expiry", "delete"):
        assert f'hx-get="/secrets/alpha-token/{act}"' in body


def test_a_model_policy_that_names_it_opens_on_click(client: TestClient) -> None:
    used = flat(client.get("/secrets/alpha-key/used", headers=HX).text)
    overview = client.get("/secrets/alpha-key", headers=HX).text

    assert "model policy" in used and "provider alpha" in used
    assert f'hx-get="/policies/{MODELPOL}"' in used
    assert f'hx-get="/policies/{MODELPOL}"' in overview


def test_used_by_lists_what_names_it_and_the_runs_it_reached(client: TestClient) -> None:
    body = flat(client.get("/secrets/alpha-token/used", headers=HX).text)

    assert "NAMED BY \u00b7 1" in body and ">CREDENTIAL</span>" in body
    assert "mcp registry" in body and "server alpha" in body
    assert "RUNS \u00b7 2" in body and "1 hold it now" in body
    assert "issued 2\u00d7" in body and "holds it now" in body
    assert "build-small" in body and 'hx-get="/runs/run_0d4492"' in body


def test_the_events_tab_reads_changes_and_issues(client: TestClient) -> None:
    every = flat(client.get("/secrets/alpha-token/events", headers=HX).text)
    changes = flat(client.get("/secrets/alpha-token/events?scope=changes", headers=HX).text)
    issued = flat(client.get("/secrets/alpha-token/events?scope=issued", headers=HX).text)

    assert "6 entries" in every and "refused:" not in every
    assert "with 1 other \u00b7 ttl 900s" in every and "alone \u00b7 ttl 900s" in every
    assert "named by 1 \u00b7 held by 1 run" in every and "logs__row--error" in every
    assert "value replaced" in every and "never \u2192 " in every
    assert "4 entries" in changes and "credentials_issued" not in changes
    assert "2 entries" in issued and "secret_created" not in issued


def test_the_events_export_is_the_api_rows(client: TestClient) -> None:
    response = client.get("/secrets/alpha-token/events/export")

    disposition = response.headers["content-disposition"]
    assert disposition == 'attachment; filename="alpha-token-events.json"'
    assert [row["event"] for row in json.loads(response.text)][-1] == "secret_created"


def test_the_new_dialog_starts_masked_and_empty(client: TestClient) -> None:
    body = flat(client.get("/secrets/_new", headers=HX).text)

    assert 'class="secret__input secret__input--masked"' in body
    assert 'aria-pressed="false"' in body and "data-secret-eye" in body
    assert "data-secret-value></textarea>" in body
    assert re.search(r'<option value="7d"[^>]*selected', body)
    assert "in 7 days" in body and 'name="never" value=""' in body
    assert "Never expire" in body and "data-secret-confirm hidden" in body


def test_a_new_secret_is_posted_once_and_opens_by_name(
    client: TestClient, writes: list[Any]
) -> None:
    form = {"name": "delta-token", "value": VALUE, "term": "7d", "never": ""}
    response = client.post("/secrets/_new", data=form, headers=HX)

    assert response.headers["HX-Redirect"] == "/secrets/delta-token"
    assert VALUE not in response.text and VALUE not in str(response.headers)
    assert writes == [
        (
            "POST",
            "/secrets",
            {"name": "delta-token", "value": VALUE, "expires_at": NOW + 7 * DAY},
            None,
        )
    ]


def test_a_plain_post_redirects_without_the_value(client: TestClient) -> None:
    form = {"name": "delta-token", "value": VALUE, "term": "1h"}
    response = client.post("/secrets/_new", data=form, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/secrets/delta-token"
    assert VALUE not in response.text


def test_line_breaks_reach_the_api_as_they_were_typed(
    client: TestClient, writes: list[Any]
) -> None:
    form = {"name": "delta-token", "value": '{\r\n  "key": 1\r\n}', "term": "1d"}
    client.post("/secrets/_new", data=form, headers=HX)

    assert writes[0][2]["value"] == '{\n  "key": 1\n}'


def test_a_refused_form_comes_back_without_the_value(client: TestClient) -> None:
    refused = "alpha-secret-\u00e9-0002"
    form = {"name": "delta-token", "value": refused, "term": "3h"}
    body = flat(client.post("/secrets/_new", data=form, headers=HX).text)
    taken = flat(client.post("/secrets/_new", data=form | {"name": "alpha-token"}, headers=HX).text)

    assert "dialog__error" in body and "the api refused the name or the value" in body
    assert 'value="delta-token"' in body and re.search(r'<option value="3h"[^>]*selected', body)
    assert "secret alpha-token already exists" in taken
    for page in (body, taken):
        assert refused not in page and "alpha-secret" not in page
        assert "data-secret-value></textarea>" in page


def test_an_empty_value_never_reaches_the_api(client: TestClient, writes: list[Any]) -> None:
    body = client.post("/secrets/_new", data={"name": "delta-token", "value": " \r\n"}, headers=HX)

    assert "a secret needs a value" in body.text
    assert writes == []


def test_never_is_refused_until_it_was_confirmed(client: TestClient, writes: list[Any]) -> None:
    form = {"name": "delta-token", "value": VALUE, "term": "never", "never": ""}
    asked = client.post("/secrets/_new", data=form, headers=HX)

    assert "never needs its confirmation" in asked.text and VALUE not in asked.text
    assert re.search(r'<option value="7d"[^>]*selected', flat(asked.text))
    assert writes == []

    client.post("/secrets/_new", data=form | {"never": "confirmed"}, headers=HX)

    assert writes[0][2]["expires_at"] is None


def test_rotate_posts_the_new_value_and_keeps_the_name(
    client: TestClient, writes: list[Any]
) -> None:
    dialog = flat(client.get("/secrets/alpha-token/rotate", headers=HX).text)
    response = client.post("/secrets/alpha-token/rotate", data={"value": VALUE}, headers=HX)

    assert "Rotate alpha-token" in dialog and "secret__input--masked" in dialog
    assert 'name="name"' not in dialog and "data-secret-value></textarea>" in dialog
    assert response.headers["HX-Redirect"] == "/secrets/alpha-token"
    assert VALUE not in response.text
    assert writes == [("POST", "/secrets/alpha-token/rotate", {"value": VALUE}, None)]


def test_expiry_changes_the_term_only(client: TestClient, writes: list[Any]) -> None:
    dialog = flat(client.get("/secrets/alpha-token/expiry", headers=HX).text)
    response = client.post("/secrets/alpha-token/expiry", data={"term": "30d"}, headers=HX)
    refused = client.post("/secrets/alpha-token/expiry", data={"term": "never"}, headers=HX)

    assert "Expiry of alpha-token" in dialog and 'name="value"' not in dialog
    assert response.headers["HX-Redirect"] == "/secrets/alpha-token"
    assert "never needs its confirmation" in refused.text
    assert writes == [("PATCH", "/secrets/alpha-token", {"expires_at": NOW + 30 * DAY}, None)]


def test_delete_asks_first_and_leaves_the_list(client: TestClient, writes: list[Any]) -> None:
    asked = flat(client.get("/secrets/gamma-key/delete", headers=HX).text)

    assert "Delete gamma-key?" in asked and ">Yes</button>" in asked
    assert "named by nothing \u00b7 held by no Run" in asked
    assert writes == []

    response = client.post("/secrets/gamma-key/delete", headers=HX)

    assert response.headers["HX-Redirect"] == "/secrets"
    assert writes == [("DELETE", "/secrets/gamma-key", {}, None)]


def test_a_secret_in_use_is_refused_by_the_api(client: TestClient, writes: list[Any]) -> None:
    body = flat(client.post("/secrets/alpha-token/delete", headers=HX).text)

    assert "alpha-token cannot be deleted" in body and ">Yes</button>" not in body
    assert "the api answered 409 for /secrets/alpha-token" in body
    assert f"named by {MCPSRV} (server alpha). Drop it from them" in body
    assert writes == [("DELETE", "/secrets/alpha-token", {}, None)]


def test_a_secret_named_new_is_a_secret_not_the_dialog(client: TestClient) -> None:
    body = client.get("/secrets/new", headers=HX).text

    assert "the api knows no secret new" in body and "New secret" not in body


@pytest.mark.parametrize("name", ["Alpha", "_alpha", "a" * 65, "alpha%20token"])
def test_a_name_the_api_would_refuse_never_reaches_it(
    client: TestClient, writes: list[Any], name: str
) -> None:
    assert client.get(f"/secrets/{name}").status_code == 422
    assert client.post(f"/secrets/{name}/delete").status_code == 422
    assert writes == []


def test_the_page_behind_a_shared_link_is_the_list(client: TestClient) -> None:
    body = client.get("/secrets/alpha-token").text

    assert body.count('<header class="topbar">') == 1
    assert body.count(ROW) >= 4 and 'aria-label="Secret alpha-token"' in body
    assert 'src="/static/secret.js"' in body
    assert client.get("/static/secret.js").status_code == 200
