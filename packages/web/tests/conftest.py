import copy
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from naos_web.app import create_app
from naos_web.client import ApiClient
from naos_web.clock import get_now

sys.path.insert(0, str(Path(__file__).parent))

from stub_api import NOW, RUNNERS, RUNS, stub  # noqa: E402


# The stub's writes edit its rows in place; each test starts from the rows the board draws.
@pytest.fixture(autouse=True)
def fresh_runs() -> Iterator[None]:
    saved, runners = copy.deepcopy(RUNS), copy.deepcopy(RUNNERS)
    yield
    RUNS[:] = saved
    RUNNERS[:] = runners


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.state.api = ApiClient(
        "http://api.example.com",
        token="operator-token",  # noqa: S106  a stub api, not a credential
        timeout=5.0,
        transport=httpx.ASGITransport(app=stub),
    )
    # The stub's rows are placed around one instant; the page reads the same one.
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


@pytest.fixture
def offline() -> TestClient:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to the api", request=request)

    app = create_app()
    app.state.api = ApiClient(
        "http://api.example.com", token=None, timeout=5.0, transport=httpx.MockTransport(refuse)
    )
    return TestClient(app)
