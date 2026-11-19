from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def hostname(host: str) -> str:
    try:
        return (urlsplit(f"//{host}").hostname or "").lower()
    except ValueError:
        return ""


# A request carrying neither header is not from a browser, so no other origin can have sent it.
def same_origin(headers: Headers) -> bool:
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    origin = headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc.lower() == headers.get("host", "").lower()


class Guard:
    """The UI acts as the operator, so it serves its own hosts and its own origin only."""

    def __init__(self, app: ASGIApp, hosts: list[str]) -> None:
        self.app = app
        self.hosts = {host.lower() for host in hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = Headers(scope=scope)
            if "*" not in self.hosts and hostname(headers.get("host", "")) not in self.hosts:
                await self.refuse(scope, receive, send, 400, "invalid host")
                return
            writes = scope["type"] == "websocket" or scope["method"] not in SAFE_METHODS
            if writes and not same_origin(headers):
                await self.refuse(scope, receive, send, 403, "cross-origin request refused")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def refuse(scope: Scope, receive: Receive, send: Send, code: int, reason: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": reason})
            return
        await PlainTextResponse(reason, status_code=code)(scope, receive, send)
