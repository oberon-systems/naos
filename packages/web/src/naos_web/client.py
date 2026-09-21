import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

Row = dict[str, Any]


class ApiError(Exception):
    """The api refused or never answered, so the page says so instead of inventing data."""


@dataclass(frozen=True)
class Dashboard:
    runs: list[Row]
    summary: Row
    runners: list[Row]


def read_token(path: Path | None) -> str | None:
    if path is None:
        return None
    token = path.read_text().strip()
    return token or None


class ApiClient:
    def __init__(
        self,
        api_url: str,
        token: str | None,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.AsyncClient(
            base_url=f"{api_url.rstrip('/')}/api/v1",
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            raise ApiError(f"the api answered {err.response.status_code} for {path}") from err
        except httpx.HTTPError as err:
            raise ApiError(f"the api did not answer {path}") from err
        return response.json()

    async def runs(self, state: str | None = None, limit: int = 50) -> list[Row]:
        params: dict[str, Any] = {"limit": limit}
        if state is not None:
            params["state"] = state
        rows: list[Row] = await self._call("GET", "/runs", params=params)
        return rows

    async def summary(self) -> Row:
        row: Row = await self._call("GET", "/runs/summary")
        return row

    async def runners(self) -> list[Row]:
        rows: list[Row] = await self._call("GET", "/runners")
        return rows

    async def stop_run(self, run_id: str) -> Row:
        row: Row = await self._call("POST", f"/runs/{run_id}/stop")
        return row

    # One page, three independent reads: fetch them together rather than in turn.
    async def dashboard(self, state: str | None = None) -> Dashboard:
        runs, summary, runners = await asyncio.gather(
            self.runs(state), self.summary(), self.runners()
        )
        return Dashboard(runs=runs, summary=summary, runners=runners)
