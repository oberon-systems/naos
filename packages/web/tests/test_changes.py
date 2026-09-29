import copy
import re

import pytest
from fastapi.testclient import TestClient
from stub_api import MERGES

WAITING = "run_5be317"
MERGED = "run_0d4492"
STARTED = "run_9f21c4"
HX = {"HX-Request": "true"}


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _rows(body: str) -> list[str]:
    rows = re.findall(r'<(?:a|div) class="changes__row[^"]*"[^>]*>(.*?)</(?:a|div)>\s', body, re.S)
    return [_text(row) for row in rows]


def _card(body: str, title: str) -> str:
    start = body.index(title)
    return _text(body[start : body.index("</section>", start)])


def test_the_tab_shows_once_the_diff_is_collected(client: TestClient) -> None:
    waiting = client.get(f"/runs/{WAITING}", headers=HX).text
    started = client.get(f"/runs/{STARTED}", headers=HX).text

    assert f'href="/runs/{WAITING}/changes"' in waiting
    assert "Changes · 12" in waiting
    assert "/changes" not in started


def test_the_list_keeps_the_api_order_and_every_field(client: TestClient) -> None:
    body = client.get(f"/runs/{WAITING}/changes", headers=HX).text

    assert re.search(r'run__tab run__tab--active"\s+href="/runs/run_5be317/changes"', body)
    assert _rows(body) == [
        "modified .github/workflows/ci.yml sensitive 1.3 KiB",
        "modified Makefile sensitive 2.0 KiB",
        "created docs/notes.md 2.1 KiB",
        "created latest \u2192 build/out",
        "modified src/api/__init__.py 212 B",
        "modified src/api/handlers.py 4.2 KiB",
        "deleted src/api/legacy.py 1.8 KiB",
        "renamed src/api/routes.py \u2190 src/api/router.py 3.4 KiB",
        "created src/api/util dir",
        "created src/api/util/strings.py 640 B",
        "rejected tests/fixtures/sock special file",
        "modified tests/test_handlers.py 5.6 KiB",
    ]
    assert "Waiting for your decision · merge policy ask" in body
    assert 'class="pill tone-violet">renamed' in body


def test_the_filters_count_and_narrow_the_list(client: TestClient) -> None:
    body = client.get(f"/runs/{WAITING}/changes", headers=HX).text
    filters = re.findall(r'class="changes__filter[^"]*"[^>]*>([^<]+)</a>', body, re.S)

    assert [_text(label) for label in filters] == [
        "All 12",
        "Created 4",
        "Modified 5",
        "Deleted 1",
        "Renamed 1",
        "Rejected 1",
        "Sensitive 2",
    ]
    sensitive = client.get(f"/runs/{WAITING}/changes?change=sensitive", headers=HX).text
    assert [row.split()[1] for row in _rows(sensitive)] == [".github/workflows/ci.yml", "Makefile"]
    assert 'href="/runs/run_5be317/changes?change=sensitive&amp;entry=1"' in sensitive


def test_the_selected_entry_shows_only_its_own_fields(client: TestClient) -> None:
    body = client.get(f"/runs/{WAITING}/changes?entry=5", headers=HX).text

    assert _card(body, "SELECTED ENTRY").startswith(
        "SELECTED ENTRY src/api/handlers.py Change modified Kind file Size 4.2 KiB "
        "Mode 0644 · base 0644 sha256 e3b7\u202641c2 Base sha256 9a0d\u202677fe "
        "On merge Apply Skip Export The base is"
    )
    assert "changes__row changes__row--picked" in body
    link = _card(client.get(f"/runs/{WAITING}/changes?entry=3", headers=HX).text, "SELECTED")
    assert "Target build/out" in link
    assert "Size" not in link


def test_a_rejected_entry_cannot_be_picked(client: TestClient) -> None:
    body = client.get(f"/runs/{WAITING}/changes?entry=10", headers=HX).text

    assert "SELECTED ENTRY .github/workflows/ci.yml" in _card(body, "SELECTED ENTRY")
    assert "entry=10" not in body
    assert _card(body, "REJECTED · 1").startswith("REJECTED · 1 tests/fixtures/sock special file")
    assert _card(body, "SENSITIVE · 2").startswith(
        "SENSITIVE · 2 LEFT OUT .github/workflows/ci.yml Makefile"
    )


def test_a_hostile_path_renders_as_inert_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    merge = copy.deepcopy(MERGES[WAITING])
    merge["entries"][0]["path"] = '<script>alert(1)</script>"><a href="x">'
    monkeypatch.setitem(MERGES, WAITING, merge)

    body = client.get(f"/runs/{WAITING}/changes", headers=HX).text

    assert "<script>alert(1)</script>" not in body
    assert '<a href="x">' not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;&#34;&gt;&lt;a href=&#34;x&#34;&gt;" in body


def test_a_merged_run_reads_its_report(client: TestClient) -> None:
    body = client.get(f"/runs/{MERGED}/changes", headers=HX).text

    assert "Merged into alpha · 4 applied · 0 exported · 2 backed up" in body
    assert "REJECTED" not in body


def test_a_run_without_a_diff_opens_on_its_overview(client: TestClient) -> None:
    body = client.get(f"/runs/{STARTED}/changes", headers=HX).text

    assert re.search(r'run__tab run__tab--active"\s+href="/runs/run_9f21c4"', body)


def test_review_and_diff_open_the_tab(client: TestClient) -> None:
    body = client.get("/runs").text

    for run_id, label in ((WAITING, "Review"), (MERGED, "Diff")):
        assert re.search(
            rf'href="/runs/{run_id}/changes"\s+hx-get="/runs/{run_id}/changes"\s+'
            rf'hx-target="#overlay">{label}</a>',
            body,
        )
