import asyncio
import hashlib
import hmac

import httpx

from naos_web.client import ApiClient

KEY = "b" * 64


def test_every_call_is_signed_and_the_token_only_logs_in() -> None:
    seen: list[dict[str, str]] = []
    logins: list[str] = []
    refusals = [1]

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/credential":
            logins.append(request.headers.get("authorization", ""))
            credential = f"k1.0.4102444800.{len(logins):032x}"
            return httpx.Response(
                200, json={"credential": credential, "key": KEY, "expires_at": 4102444800}
            )
        seen.append(dict(request.headers))
        if refusals:
            refusals.pop()
            return httpx.Response(401, json={"detail": "operator request is not signed"})
        return httpx.Response(200, json=[])

    async def scenario() -> None:
        api = ApiClient(
            "http://api.example.com", "operator-token", 5.0, transport=httpx.MockTransport(answer)
        )
        await api._request("GET", "/runs", params={"state": "active"})
        await api.aclose()

    asyncio.run(scenario())

    assert logins == ["Bearer operator-token", "Bearer operator-token"]
    last = seen[-1]
    assert "authorization" not in last
    message = "\n".join(
        [
            "GET",
            "/api/v1/runs?state=active",
            last["x-timestamp"],
            last["x-nonce"],
            hashlib.sha256(b"").hexdigest(),
        ]
    )
    expected = hmac.new(KEY.encode(), message.encode(), hashlib.sha256).hexdigest()
    assert last["x-signature"] == expected
    assert seen[0]["x-nonce"] != last["x-nonce"]
