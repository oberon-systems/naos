import pytest
from fastapi.testclient import TestClient

from naos_web.pages import NAV

PATHS = [page.href for page in NAV]


@pytest.mark.parametrize("path", PATHS)
def test_every_list_page_renders(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("path", PATHS)
def test_every_list_page_shares_the_shell(client: TestClient, path: str) -> None:
    body = client.get(path).text
    assert body.count('<header class="topbar">') == 1
    assert body.count('<a class="brand" href="/runs">naos</a>') == 1
    assert body.count('class="content"') == 1
    for page in NAV:
        assert f'href="{page.href}"' in body
    assert ">New Run</a>" in body


@pytest.mark.parametrize("page", NAV, ids=[page.key for page in NAV])
def test_active_nav_item_is_the_current_page(client: TestClient, page) -> None:  # type: ignore[no-untyped-def]
    body = client.get(page.href).text
    assert body.count("nav__item--active") == 1
    active = body.split("nav__item--active", 1)[1]
    assert active.split(">", 1)[1].startswith(page.title)


@pytest.mark.parametrize("page", NAV, ids=[page.key for page in NAV])
def test_every_list_page_shows_its_four_tiles(client: TestClient, page) -> None:  # type: ignore[no-untyped-def]
    body = client.get(page.href).text
    assert len(page.tiles) == 4
    assert body.count('class="tile"') == 4
    for tile in page.tiles:
        assert f'tone-{tile.tone}"></span>' in body
        assert tile.label in body


@pytest.mark.parametrize("page", NAV, ids=[page.key for page in NAV])
def test_page_header_follows_the_board(client: TestClient, page) -> None:  # type: ignore[no-untyped-def]
    body = client.get(page.href).text
    if not page.header:
        assert "page-header" not in body
        return
    assert f'<h1 class="page-header__title">{page.title}</h1>' in body
    assert page.chip is not None
    assert page.chip.label in body
    assert page.action is not None
    assert f">{page.action.label}</a>" in body


@pytest.mark.parametrize("path", PATHS)
def test_no_tile_values_are_invented(client: TestClient, path: str) -> None:
    body = client.get(path).text
    assert "tile__number" not in body
    assert "tile__note" not in body
    assert "page-header__subtitle" not in body


def test_index_redirects_to_runs(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/runs"


def test_htmx_is_served_from_the_application(client: TestClient) -> None:
    body = client.get("/runs").text
    assert 'src="/static/vendor/htmx.min.js"' in body
    assert "//unpkg" not in body
    assert "fonts.googleapis.com" not in body
    assert client.get("/static/vendor/htmx.min.js").status_code == 200
    assert client.get("/static/naos.css").status_code == 200
