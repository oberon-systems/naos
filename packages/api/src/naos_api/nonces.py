from typing import Protocol

import fakeredis
import redis


class Store(Protocol):
    def set(self, name: str, value: str, *, ex: int, nx: bool) -> object: ...


class Nonces:
    """Nonces already seen, kept for twice the signature window so no request replays."""

    def __init__(self, url: str | None) -> None:
        # Without a cache url the store is this process's own, enough for a single worker.
        self.store: Store = redis.Redis.from_url(url) if url else fakeredis.FakeRedis()

    def first(self, nonce: str, seconds: int) -> bool:
        return bool(self.store.set(f"naos:nonce:{nonce}", "1", ex=seconds, nx=True))
