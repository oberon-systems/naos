import hmac
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.security.utils import get_authorization_scheme_param
from sqlmodel import Session
from starlette.datastructures import URL, Headers

from naos_api import credentials, runners
from naos_api.clock import NowDep
from naos_api.db import get_session, get_socket_session
from naos_api.runners import RunnerPrincipal, hash_token
from naos_api.settings import get_settings

_bearer = HTTPBearer(auto_error=False)
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _matches(credentials: HTTPAuthorizationCredentials | None, expected: str | None) -> bool:
    return (
        credentials is not None
        and expected is not None
        and hmac.compare_digest(hash_token(credentials.credentials), expected)
    )


def _operator_hash() -> str | None:
    return get_settings().operator_token_sha256


# The operator token only logs in; it is refused on every other route.
def operator_login(token: str) -> bool:
    expected = _operator_hash()
    return expected is not None and hmac.compare_digest(hash_token(token), expected)


def _target(url: URL) -> str:
    return f"{url.path}?{url.query}" if url.query else url.path


def _signed(headers: Headers) -> credentials.Signed | None:
    names = ("x-credential", "x-timestamp", "x-nonce", "x-signature")
    values = [headers.get(name) for name in names]
    if any(value is None for value in values):
        return None
    return credentials.Signed(*(str(value) for value in values))


def _verified(
    session: Session,
    app_state: Any,
    headers: Headers,
    method: str,
    target: str,
    body: bytes,
    now: int,
) -> bool:
    signed = _signed(headers)
    settings = get_settings()
    return signed is not None and credentials.verify(
        session, settings.master_keys, app_state.nonces, signed, method, target, body, now
    )


async def require_principal(
    request: Request, session: Annotated[Session, Depends(get_session)], now: NowDep
) -> None:
    body = await request.body()
    method, target = request.method, _target(request.url)
    if not _verified(session, request.app.state, request.headers, method, target, body, now):
        raise _unauthorized("operator request is not signed")


def require_operator_connection(
    websocket: WebSocket,
    session: Annotated[Session, Depends(get_socket_session)],
    now: NowDep,
) -> None:
    target = _target(websocket.url)
    if not _verified(session, websocket.app.state, websocket.headers, "GET", target, b"", now):
        raise WebSocketException(status.WS_1008_POLICY_VIOLATION, "operator is not authorized")


def require_runner_connection(
    websocket: WebSocket,
    runner_id: str,
    session: Annotated[Session, Depends(get_socket_session)],
    now: NowDep,
) -> RunnerPrincipal:
    scheme, token = get_authorization_scheme_param(websocket.headers.get("authorization"))
    principal = (
        runners.authenticate(session, token, now) if scheme.lower() == "bearer" and token else None
    )
    if principal is None or principal.runner_id != runner_id:
        raise WebSocketException(
            status.WS_1008_POLICY_VIOLATION, "runner credentials are not valid"
        )
    return principal


def _enrollment_hash() -> str | None:
    return get_settings().runner_enrollment_token_sha256


def require_enrollment(
    credentials: BearerDep, expected: Annotated[str | None, Depends(_enrollment_hash)]
) -> None:
    if not _matches(credentials, expected):
        raise _unauthorized("runner enrollment is not authorized")


def require_runner(
    runner_id: str,
    credentials: BearerDep,
    session: Annotated[Session, Depends(get_session)],
    now: NowDep,
) -> RunnerPrincipal:
    principal = (
        None if credentials is None else runners.authenticate(session, credentials.credentials, now)
    )
    if principal is None:
        raise _unauthorized("runner credentials are not valid")
    if principal.runner_id != runner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="runner credentials do not belong to this runner",
        )
    return principal
