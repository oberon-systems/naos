import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from httpx_ws import AsyncWebSocketSession, HTTPXWSException, WebSocketDisconnect, aconnect_ws

Row = dict[str, Any]


async def _send(ws: AsyncWebSocketSession, outbox: "asyncio.Queue[str]") -> None:
    while True:
        await ws.send_text(await outbox.get())


class ApiError(Exception):
    """The api refused or never answered, so the page says so instead of inventing data."""


# A domain refusal carries a sentence meant for the operator; a validation error does not.
def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return ""
    return f": {detail}" if isinstance(detail, str) else ""


@dataclass(frozen=True)
class Dashboard:
    runs: list[Row]
    summary: Row
    runners: list[Row]


@dataclass(frozen=True)
class Choices:
    policies: list[Row]
    images: list[Row]
    runners: list[Row]


@dataclass(frozen=True)
class RunDetail:
    run: Row
    events: list[Row]
    runner: Row | None
    policies: dict[str, Row]
    images: list[Row]
    profile: Row | None


@dataclass(frozen=True)
class RunnerDetail:
    runner: Row
    runs: list[Row]
    events: list[Row]


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

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            status = err.response.status_code
            raise ApiError(f"the api answered {status} for {path}{_detail(err.response)}") from err
        except httpx.HTTPError as err:
            raise ApiError(f"the api did not answer {path}") from err
        return response

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        return (await self._request(method, path, **kwargs)).json()

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

    async def events(self, runner_id: str, limit: int = 5) -> list[Row]:
        rows: list[Row] = await self._call(
            "GET", "/audit", params={"runner_id": runner_id, "limit": limit}
        )
        return rows

    async def run(self, run_id: str) -> Row:
        row: Row = await self._call("GET", f"/runs/{run_id}")
        return row

    async def run_events(self, run_id: str) -> list[Row]:
        rows: list[Row] = await self._call("GET", f"/runs/{run_id}/events")
        return rows

    async def console_log(self, run_id: str) -> bytes:
        return (await self._request("GET", f"/runs/{run_id}/console")).content

    # The api ends the stream once the run is over; a refused attach is an ApiError.
    # Console output comes down as bytes, the size the viewer asked for goes up
    # and its answer comes back down as text.
    async def attach(
        self, run_id: str, outbox: "asyncio.Queue[str] | None" = None
    ) -> AsyncIterator[bytes | str]:
        path = f"/runs/{run_id}/attach"
        try:
            async with aconnect_ws(path, self._http, session_class=AsyncWebSocketSession) as ws:
                sender = asyncio.create_task(_send(ws, outbox)) if outbox is not None else None
                try:
                    while True:
                        frame = getattr(await ws.receive(), "data", None)
                        if isinstance(frame, (bytes, str)):
                            yield frame
                finally:
                    if sender is not None:
                        sender.cancel()
        except WebSocketDisconnect:
            return
        except (HTTPXWSException, httpx.HTTPError) as err:
            raise ApiError(f"the api refused {path}") from err

    async def policy(self, policy_id: str) -> Row:
        row: Row = await self._call("GET", f"/policies/{policy_id}")
        return row

    async def create_run(self, spec: Row, idempotency_key: str) -> Row:
        row: Row = await self._call(
            "POST", "/runs", json=spec, headers={"Idempotency-Key": idempotency_key}
        )
        return row

    async def stop_run(self, run_id: str) -> Row:
        row: Row = await self._call("POST", f"/runs/{run_id}/stop")
        return row

    async def profiles(self, query: str | None = None) -> list[Row]:
        params = {"q": query} if query else {}
        rows: list[Row] = await self._call("GET", "/profiles", params=params)
        return rows

    async def profile(self, profile_id: str) -> Row:
        row: Row = await self._call("GET", f"/profiles/{profile_id}")
        return row

    async def create_profile(self, name: str, spec: Row) -> Row:
        row: Row = await self._call("POST", "/profiles", json={"name": name, "spec": spec})
        return row

    async def update_profile(self, profile_id: str, spec: Row) -> Row:
        row: Row = await self._call("PUT", f"/profiles/{profile_id}", json={"spec": spec})
        return row

    async def run_from_profile(
        self, profile_id: str, image: Row, runner: str | None, idempotency_key: str
    ) -> Row:
        row: Row = await self._call(
            "POST",
            f"/profiles/{profile_id}/runs",
            json={"image": image, "runner": runner},
            headers={"Idempotency-Key": idempotency_key},
        )
        return row

    async def policies(self) -> list[Row]:
        rows: list[Row] = await self._call("GET", "/policies")
        return rows

    async def images(self) -> list[Row]:
        rows: list[Row] = await self._call("GET", "/images")
        return rows

    # One page, three independent reads: fetch them together rather than in turn.
    async def dashboard(self, state: str | None = None) -> Dashboard:
        runs, summary, runners = await asyncio.gather(
            self.runs(state), self.summary(), self.runners()
        )
        return Dashboard(runs=runs, summary=summary, runners=runners)

    async def runner(self, runner_id: str) -> RunnerDetail:
        runners, runs, events = await asyncio.gather(
            self.runners(), self.runs(limit=100), self.events(runner_id)
        )
        found = next((row for row in runners if row["id"] == runner_id), None)
        if found is None:
            raise ApiError(f"the api knows no runner {runner_id}")
        return RunnerDetail(runner=found, runs=runs, events=events)

    # The policies are the ones the spec names, so the card shows what this Run was given.
    async def run_detail(self, run_id: str) -> RunDetail:
        run = await self.run(run_id)
        spec = run["spec"]
        named = [spec[kind]["policy"] for kind in ("network", "mcp") if spec[kind]["policy"]]
        events, runners, images, policies = await asyncio.gather(
            self.run_events(run_id),
            self.runners(),
            self.images(),
            asyncio.gather(*(self.policy(policy_id) for policy_id in named)),
        )
        profile = await self.profile(run["profile_id"]) if run.get("profile_id") else None
        holder = run["runner"]["id"] if run["runner"] else spec.get("runner")
        return RunDetail(
            run=run,
            events=events,
            runner=next((row for row in runners if row["id"] == holder), None),
            policies={policy["kind"]: policy for policy in policies},
            images=images,
            profile=profile,
        )

    async def choices(self) -> Choices:
        policies, images, runners = await asyncio.gather(
            self.policies(), self.images(), self.runners()
        )
        return Choices(policies=policies, images=images, runners=runners)
