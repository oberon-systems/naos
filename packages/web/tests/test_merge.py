import copy
import html
import re
from collections.abc import Iterator
from urllib.parse import parse_qsl, urlencode

import pytest
from fastapi.testclient import TestClient
from stub_api import MERGES, WRITES

WAITING = "run_5be317"
MERGED = "run_0d4492"
HX = {"HX-Request": "true"}
SENSITIVE = [".github/workflows/ci.yml", "Makefile"]
DEFAULT = [
    "docs/notes.md",
    "latest",
    "src/api/__init__.py",
    "src/api/handlers.py",
    "src/api/legacy.py",
    "src/api/routes.py",
    "src/api/util",
    "src/api/util/strings.py",
    "tests/test_handlers.py",
]
CONFLICTS = [
    {"path": "src/api/handlers.py", "reason": "the host changed since collection"},
    {"path": "tests/test_handlers.py", "reason": "the host changed since collection"},
]


@pytest.fixture(autouse=True)
def fresh_writes() -> Iterator[None]:
    WRITES.clear()
    yield
    WRITES.clear()


def _text(markup: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup)).strip())


def _state(body: str) -> list[tuple[str, str]]:
    form = re.search(r'<form id="merge-state" hidden>(.*?)</form>', body, re.S)
    assert form is not None
    return re.findall(r'name="([^"]+)" value="([^"]*)"', form.group(1))


def _picked(body: str) -> list[str]:
    return [value for name, value in _state(body) if name == "path"]


def _bar(body: str) -> str:
    start = body.index('<section class="card changes__bar"')
    return _text(body[start : body.index("</section>", start)])


def _tab(client: TestClient, run_id: str = WAITING, **query: str | list[str]) -> str:
    url = f"/runs/{run_id}/changes?{urlencode(query, doseq=True)}"
    body: str = client.get(url, headers=HX).text
    return body


def _conflicted(monkeypatch: pytest.MonkeyPatch) -> None:
    merge = copy.deepcopy(MERGES[WAITING]) | {"conflicts": CONFLICTS}
    monkeypatch.setitem(MERGES, WAITING, merge)


def test_the_default_selection_leaves_sensitive_and_rejected_out(client: TestClient) -> None:
    body = _tab(client)

    assert _picked(body) == DEFAULT
    assert _bar(body) == (
        "9 of 11 paths selected 2 sensitive left out · 1 rejected · decision applies to alpha "
        "Merge nothing Merge 9 paths"
    )
    assert f'href="/runs/{WAITING}/merge?touched=1&amp;path=docs%2Fnotes.md' in body


def test_a_tick_selects_a_sensitive_path_only_when_asked(client: TestClient) -> None:
    body = _tab(client, touched="1", path=DEFAULT, toggle="Makefile")

    assert _picked(body) == sorted([*DEFAULT, "Makefile"])
    assert "SENSITIVE · 1 LEFT OUT" in body
    every = _tab(client, touched="1", path=[], select="all")
    assert _picked(every) == DEFAULT


def test_clear_empties_the_selection_and_disables_the_merge(client: TestClient) -> None:
    body = _tab(client, touched="1", path=DEFAULT, select="none")

    assert _picked(body) == []
    assert "0 of 11 paths selected" in _bar(body)
    assert '<button class="button" type="button" disabled>Merge 0 paths</button>' in body


def test_a_tick_keeps_the_selection_one_the_api_accepts(client: TestClient) -> None:
    dropped = _tab(client, touched="1", path=DEFAULT, toggle="src/api/util")
    assert "src/api/util/strings.py" not in _picked(dropped)
    assert "src/api/util" not in _picked(dropped)

    added = _tab(client, touched="1", toggle="src/api/util/strings.py")
    assert _picked(added) == ["src/api/util", "src/api/util/strings.py"]


def test_the_on_merge_switch_resolves_the_selected_entry(client: TestClient) -> None:
    body = _tab(
        client,
        touched="1",
        path=DEFAULT,
        entry="5",
        resolve="src/api/handlers.py",
        to="export",
    )

    assert ("export", "src/api/handlers.py") in _state(body)
    assert re.search(r'changes__option changes__option--on"[^>]*>Export</button>', body)
    applied = _tab(
        client,
        touched="1",
        path=DEFAULT,
        export="src/api/handlers.py",
        resolve="src/api/handlers.py",
        to="apply",
    )
    assert not [name for name, _ in _state(applied) if name in ("skip", "take", "export")]


def test_merge_asks_before_it_applies_the_selection(client: TestClient) -> None:
    body = client.get(f"/runs/{WAITING}/merge", headers=HX).text
    text = _text(body)

    assert "Merge 9 paths?" in text
    assert "Apply 9 paths to alpha?" in text
    assert (
        "3 created, 3 modified, 1 deleted, 1 renamed and 1 directory. Replaced and removed host "
        "entries move to merge/backup/; nothing on the host is deleted. Left out: "
        ".github/workflows/ci.yml, Makefile." in text
    )
    assert 'type="submit">Merge</button>' in body
    assert 'hx-post="/runs/run_5be317/merge"' in body
    assert re.findall(r'name="path" value="([^"]+)"', body) == DEFAULT


def test_a_sensitive_selection_asks_the_louder_question(client: TestClient) -> None:
    every = sorted([*DEFAULT, *SENSITIVE])
    query = urlencode({"touched": "1", "path": every}, doseq=True)
    text = _text(client.get(f"/runs/{WAITING}/merge?{query}", headers=HX).text)

    assert "Merge 11 paths?" in text
    assert "Two of them run on the host or in CI once merged." in text
    assert (
        ".github/workflows/ci.yml and Makefile are sensitive. Read them in the runner's archive "
        "before you merge: naos shows their hashes, not their contents." in text
    )
    assert text.endswith("the audit keeps who decided No Merge 11 paths")


def test_merge_nothing_asks_before_it_completes_the_run(client: TestClient) -> None:
    body = client.get(f"/runs/{WAITING}/merge/reject", headers=HX).text
    text = _text(body)

    assert "Merge nothing? run_5be317" in text
    assert "Complete run_5be317 without touching alpha?" in text
    assert "None of the 11 changes reach the host and the run completes." in text
    assert re.search(r'class="button button--danger"\s+type="submit">Merge nothing</button>', body)


def test_a_confirmed_merge_posts_the_selection_and_locks_it(client: TestClient) -> None:
    form = [("path", path) for path in DEFAULT] + [("export", "tests/test_handlers.py")]

    body = client.post(
        f"/runs/{WAITING}/merge",
        content=urlencode(form),
        headers=HX | {"Content-Type": "application/x-www-form-urlencoded"},
    ).text
    assert WRITES[-1] == (
        "POST",
        f"/runs/{WAITING}/merge",
        {"paths": DEFAULT, "resolutions": {"tests/test_handlers.py": "export"}},
        None,
    )
    assert "Decision sent · runner gamma is applying it" in body
    assert _bar(body) == (
        "Decision sent · 9 paths · export 1 another decision is refused with 409 while this one "
        "is pending Applying on gamma"
    )
    assert 'hx-trigger="every 2s"' in body
    assert 'id="merge-state"' not in body
    assert 'hx-get="/runs/run_5be317/changes?entry=0&amp;toggle' not in body


def test_a_confirmed_merge_nothing_posts_the_reject(client: TestClient) -> None:
    body = client.post(f"/runs/{WAITING}/merge/reject", headers=HX).text

    assert [("POST", f"/runs/{WAITING}/merge/reject", {}, None)] == WRITES
    assert "Decision sent · 0 paths · no resolutions" in _bar(body)


def test_a_refused_selection_shows_the_api_message(client: TestClient) -> None:
    body = client.post(f"/runs/{WAITING}/merge", data={"path": "nowhere.txt"}, headers=HX).text

    assert re.search(r'role="alert">[^<]*nowhere.txt is not a mergeable path of the diff', body)
    assert "Waiting for your decision" in body
    assert _picked(body) == []


def test_a_pending_decision_reloads_the_tab(client: TestClient) -> None:
    MERGES[WAITING]["decision"] = {"paths": ["latest"], "resolutions": {}}

    body = client.post(f"/runs/{WAITING}/merge", data={"path": "docs/notes.md"}, headers=HX).text

    assert "Decision sent · 1 path · no resolutions" in _bar(body)
    assert 'role="alert"' not in body


def test_a_conflict_waits_for_every_resolution(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _conflicted(monkeypatch)

    body = _tab(client)
    assert "2 conflicts · nothing was written" in body
    assert "Changes · 2 conflicts" in body
    assert "CONFLICT · 1 OF 2 src/api/handlers.py Reason the host changed since collection" in (
        _text(body)
    )
    assert _bar(body).startswith(
        "9 paths selected · 0 of 2 conflicts resolved the previous decision was cleared"
    )
    assert '<button class="button" type="button" disabled>Send decision again</button>' in body

    done = _tab(
        client,
        touched="1",
        path=DEFAULT,
        take="src/api/handlers.py",
        resolve="tests/test_handlers.py",
        to="export",
    )
    assert _bar(done).startswith("9 paths selected · 2 of 2 conflicts resolved take 1 · export 1")
    assert re.search(
        r'hx-get="/runs/run_5be317/merge\?[^"]*"\s+hx-target="#overlay">Send decision again</a>',
        done,
    )


@pytest.mark.parametrize("kind", ["skip", "take", "export"])
def test_a_conflict_is_resolved_with_each_resolution(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    _conflicted(monkeypatch)
    paths = [conflict["path"] for conflict in CONFLICTS]
    query = urlencode({"touched": "1", "path": DEFAULT, kind: paths}, doseq=True)
    asked = client.get(f"/runs/{WAITING}/merge?{query}", headers=HX).text
    fields = re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', asked)

    client.post(
        f"/runs/{WAITING}/merge",
        content=urlencode(fields),
        headers=HX | {"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert WRITES[-1][2] == {
        "paths": DEFAULT,
        "resolutions": {conflict["path"]: kind for conflict in CONFLICTS},
    }
    assert f"Resolutions: {kind} 2." in _text(asked)


def test_a_merged_run_shows_every_outcome(client: TestClient) -> None:
    body = _tab(client, MERGED)
    text = _text(body)
    filters = re.findall(r'class="changes__filter[^"]*"[^>]*>([^<]+)</a>', body)

    assert [_text(label) for label in filters] == [
        "All 4",
        "Applied 4",
        "Exported 0",
        "Backed up 2",
        "Left out 0",
    ]
    assert "notes.txt applied · backed up" in text
    assert "added.txt applied" in text
    assert "REPORT Applied 4 Exported 0 Skipped 0 Backed up 2 Left out 0" in text
    assert "Runner alpha · archive/vm_3f0a/" in text
    assert 'href="/runners/rnr_8c1f42aa"' in body
    assert _bar(body).startswith("Decided by operator 33m ago · 4 paths the runner reported")
    assert "Changes · merged" in body
    assert 'id="merge-state"' not in body
    backed = _tab(client, MERGED, change="backed_up")
    assert "added.txt" not in _text(backed)


def test_the_selection_survives_a_filter(client: TestClient) -> None:
    body = _tab(client, touched="1", path=["latest"], change="deleted")

    assert _picked(body) == ["latest"]
    assert re.search(
        r'href="/runs/run_5be317/changes\?change=created"\s+hx-get="[^"]+"\s+'
        r'hx-include="#merge-state"',
        body,
    )
    toggle = re.search(r'hx-get="(/runs/run_5be317/changes\?[^"]*toggle=[^"]*)"', body)
    assert toggle is not None
    assert dict(parse_qsl(toggle.group(1).partition("?")[2].replace("&amp;", "&")))["change"] == (
        "deleted"
    )
