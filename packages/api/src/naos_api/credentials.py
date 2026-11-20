import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from sqlmodel import Session

from naos_api.models import OperatorCredential
from naos_api.nonces import Nonces

CREDENTIAL_SECONDS = 3600
WINDOW_SECONDS = 300
OPERATOR = "operator"
CREDENTIAL = re.compile(r"([A-Za-z0-9_-]{1,32})\.([0-9]{1,10})\.([0-9]{1,12})\.[0-9a-f]{32}")
TIMESTAMP = re.compile(r"[0-9]{1,12}")
NONCE = re.compile(r"[0-9a-f]{32}")
SIGNATURE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Issued:
    credential: str
    key: str
    expires_at: int


@dataclass(frozen=True)
class Signed:
    credential: str
    timestamp: str
    nonce: str
    signature: str


def generation(session: Session) -> int:
    row = session.get(OperatorCredential, OPERATOR)
    return row.generation if row is not None else 0


def bump(session: Session, now: int) -> None:
    row = session.get(OperatorCredential, OPERATOR) or OperatorCredential(
        id=OPERATOR, generation=0, updated_at=now
    )
    row.generation += 1
    row.updated_at = now
    session.add(row)
    session.commit()


# The key is derived again on every request, so the API keeps nothing per credential.
def key_of(keys: dict[str, str], credential: str) -> str | None:
    master = keys.get(credential.split(".", 1)[0])
    if master is None:
        return None
    return hmac.new(bytes.fromhex(master), credential.encode(), hashlib.sha256).hexdigest()


def issue(session: Session, keys: dict[str, str], kid: str, now: int) -> Issued:
    expires_at = now + CREDENTIAL_SECONDS
    credential = f"{kid}.{generation(session)}.{expires_at}.{secrets.token_hex(16)}"
    key = key_of(keys, credential)
    if key is None:
        raise ValueError("the issuing master key is not configured")
    return Issued(credential=credential, key=key, expires_at=expires_at)


def signature(key: str, method: str, target: str, timestamp: str, nonce: str, body: bytes) -> str:
    message = "\n".join(
        [method.upper(), target, timestamp, nonce, hashlib.sha256(body).hexdigest()]
    )
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def headers(issued: Issued, method: str, target: str, body: bytes, now: int) -> dict[str, str]:
    timestamp, nonce = str(now), secrets.token_hex(16)
    return {
        "X-Credential": issued.credential,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature(issued.key, method, target, timestamp, nonce, body),
    }


def verify(
    session: Session,
    keys: dict[str, str],
    nonces: Nonces,
    signed: Signed,
    method: str,
    target: str,
    body: bytes,
    now: int,
) -> bool:
    # Every field is checked by pattern first, so no input reaches a compare it would break.
    parsed = CREDENTIAL.fullmatch(signed.credential)
    if parsed is None or not all(
        pattern.fullmatch(value)
        for pattern, value in (
            (TIMESTAMP, signed.timestamp),
            (NONCE, signed.nonce),
            (SIGNATURE, signed.signature),
        )
    ):
        return False
    if abs(now - int(signed.timestamp)) > WINDOW_SECONDS or int(parsed[3]) <= now:
        return False
    key = key_of(keys, signed.credential)
    if key is None or int(parsed[2]) != generation(session):
        return False
    expected = signature(key, method, target, signed.timestamp, signed.nonce, body)
    if not hmac.compare_digest(expected, signed.signature):
        return False
    return nonces.first(signed.nonce, WINDOW_SECONDS * 2)
