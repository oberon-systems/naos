from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 32 * 1024 * 1024


class BodyLimit:
    """Refuses a request body past the limit, declared or streamed, before a route parses it."""

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length", "")
        if declared and (not declared.isdigit() or int(declared) > self.limit):
            await PlainTextResponse("request body too large", status_code=413)(scope, receive, send)
            return
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > self.limit:
                raise HTTPException(413, "request body too large")
            return message

        await self.app(scope, limited, send)
