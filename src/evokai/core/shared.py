"""State every instance of the service must share: rate counters, concurrency, short-lived keys.

One small interface, two implementations:

- ``MemoryState`` (the default): this process's memory. Right for one instance; with several, each
  counts on its own and limits multiply by the number of instances.
- ``RedisState`` (``EVOKAI_REDIS_URL``): Redis or Valkey, shared by every instance. Counters are
  atomic (``INCRBY`` with an expiry set once), keys expire on their own.

Redis being down never refuses a request: the operation falls back to the instance's own memory
(fail open, with the per-instance behaviour of a single instance) and a warning is logged at most
every 30 seconds. After a failure Redis is left alone for a few seconds, so a dead server does not
add a timeout to every request.

Keys are opaque strings chosen by the callers; callers hash anything personal (subjects, client
addresses, codes) before it becomes part of a key.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any

log = logging.getLogger("evokai.shared")

RETRY_AFTER = 5.0
"""Seconds Redis is left alone after a failure."""
WARN_EVERY = 30.0


class SharedState:
    """Counters and short-lived keys, shared by every instance (or by this one only)."""

    kind = "memory"

    async def incr(self, key: str, ttl_seconds: float, by: int = 1) -> int:
        """Add to a counter; its expiry is set when it is created and never extended.

        Returns:
            The counter after the change.
        """
        raise NotImplementedError

    async def acquire(self, key: str, ttl_seconds: float) -> int:
        """Count one more running thing (its expiry is extended: a crashed holder ages out).

        Returns:
            The count after the change.
        """
        raise NotImplementedError

    async def release(self, key: str) -> int:
        """Count one less running thing (the key goes at zero).

        Returns:
            The count after the change, never below zero.
        """
        raise NotImplementedError

    async def get(self, key: str) -> str | None:
        """Read a value.

        Returns:
            The value, or None when missing or expired.
        """
        raise NotImplementedError

    async def count(self, key: str) -> int:
        """Read a counter.

        Returns:
            Its value, 0 when missing.
        """
        value = await self.get(key)
        try:
            return int(value) if value is not None else 0
        except ValueError:
            return 0

    async def set(self, key: str, value: str, ttl_seconds: float) -> None:
        """Write a value with an expiry."""
        raise NotImplementedError

    async def set_once(self, key: str, value: str, ttl_seconds: float) -> bool:
        """Write a value only when the key is free.

        Returns:
            Whether it was written.
        """
        raise NotImplementedError

    async def take(self, key: str) -> str | None:
        """Read and delete a value in one step (single-use codes).

        Returns:
            The value, or None when missing or expired.
        """
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        """Delete a key if present."""
        raise NotImplementedError

    async def ping(self) -> bool:
        """Whether the store answers.

        Returns:
            True when it does.
        """
        return True

    async def close(self) -> None:
        """Release connections."""
        return None


class MemoryState(SharedState):
    """This process's memory: a bounded map with an expiry per key.

    Lock-free: one event loop, and no awaits inside an operation.
    """

    kind = "memory"

    def __init__(self, max_items: int = 200_000) -> None:
        """Create the store.

        Args:
            max_items: Bound on keys (the least recently used go first).
        """
        self.max_items = max_items
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def _read(self, key: str) -> Any:
        hit = self._items.get(key)
        if hit is None:
            return None
        expires, value = hit
        if expires < time.monotonic():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return value

    def _write(self, key: str, value: Any, expires: float) -> None:
        self._items[key] = (expires, value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def _expiry(self, key: str) -> float | None:
        hit = self._items.get(key)
        return hit[0] if hit and hit[0] >= time.monotonic() else None

    async def incr(self, key: str, ttl_seconds: float, by: int = 1) -> int:  # ruff: ignore[undocumented-public-method]
        expires = self._expiry(key) or time.monotonic() + ttl_seconds
        n = int(self._read(key) or 0) + by
        self._write(key, n, expires)
        return n

    async def acquire(self, key: str, ttl_seconds: float) -> int:  # ruff: ignore[undocumented-public-method]
        n = int(self._read(key) or 0) + 1
        self._write(key, n, time.monotonic() + ttl_seconds)
        return n

    async def release(self, key: str) -> int:  # ruff: ignore[undocumented-public-method]
        expires = self._expiry(key)
        n = int(self._read(key) or 0) - 1
        if n <= 0 or expires is None:
            self._items.pop(key, None)
            return 0
        self._write(key, n, expires)
        return n

    async def get(self, key: str) -> str | None:  # ruff: ignore[undocumented-public-method]
        value = self._read(key)
        return None if value is None else str(value)

    async def set(self, key: str, value: str, ttl_seconds: float) -> None:  # ruff: ignore[undocumented-public-method]
        self._write(key, value, time.monotonic() + ttl_seconds)

    async def set_once(self, key: str, value: str, ttl_seconds: float) -> bool:  # ruff: ignore[undocumented-public-method]
        if self._read(key) is not None:
            return False
        self._write(key, value, time.monotonic() + ttl_seconds)
        return True

    async def take(self, key: str) -> str | None:  # ruff: ignore[undocumented-public-method]
        value = self._read(key)
        self._items.pop(key, None)
        return None if value is None else str(value)

    async def delete(self, key: str) -> None:  # ruff: ignore[undocumented-public-method]
        self._items.pop(key, None)

    def clear(self) -> None:
        """Forget everything (tests)."""
        self._items.clear()


class RedisState(SharedState):
    """Redis or Valkey, shared by every instance; this instance's memory when it does not answer."""

    kind = "redis"

    def __init__(self, url: str, *, prefix: str = "evokai:", client: Any = None) -> None:
        """Connect lazily.

        Args:
            url: ``redis://`` or ``rediss://`` URL (``EVOKAI_REDIS_URL``).
            prefix: Put before every key, so several deployments can share a server.
            client: A ready ``redis.asyncio`` client (tests pass fakeredis).
        """
        if client is None:
            from redis import asyncio as aioredis

            client = aioredis.from_url(
                url,
                decode_responses=True,
                socket_timeout=0.5,
                socket_connect_timeout=0.5,
                health_check_interval=30,
            )
        self.client: Any = client
        self.prefix = prefix
        self.local = MemoryState()
        """Where operations go while Redis does not answer."""
        self._down_until = 0.0
        self._warned_at = 0.0
        self.last_error: str | None = None

    def _k(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def _usable(self) -> bool:
        return time.monotonic() >= self._down_until

    def _failed(self, op: str, e: BaseException) -> None:
        now = time.monotonic()
        self._down_until = now + RETRY_AFTER
        self.last_error = type(e).__name__
        if now - self._warned_at >= WARN_EVERY:
            self._warned_at = now
            log.warning(
                "shared_state_unavailable",
                extra={"operation": op, "error": type(e).__name__, "fallback": "memory"},
            )

    async def _run(self, op: str, remote: Any, local: Any) -> Any:
        """Run on Redis, or on this instance's memory when Redis is down.

        Returns:
            What the operation returned.
        """
        if self._usable():
            try:
                return await remote()
            except Exception as e:
                self._failed(op, e)
        return await local()

    async def incr(self, key: str, ttl_seconds: float, by: int = 1) -> int:  # ruff: ignore[undocumented-public-method]
        k, ms = self._k(key), max(1, int(ttl_seconds * 1000))

        async def remote() -> int:
            async with self.client.pipeline(transaction=True) as p:
                p.set(k, 0, px=ms, nx=True)
                p.incrby(k, by)
                _, n = await p.execute()
            return int(n)

        return await self._run("incr", remote, lambda: self.local.incr(key, ttl_seconds, by))

    async def acquire(self, key: str, ttl_seconds: float) -> int:  # ruff: ignore[undocumented-public-method]
        k, ms = self._k(key), max(1, int(ttl_seconds * 1000))

        async def remote() -> int:
            async with self.client.pipeline(transaction=True) as p:
                p.incr(k)
                p.pexpire(k, ms)
                n, _ = await p.execute()
            return int(n)

        return await self._run("acquire", remote, lambda: self.local.acquire(key, ttl_seconds))

    async def release(self, key: str) -> int:  # ruff: ignore[undocumented-public-method]
        k = self._k(key)

        async def remote() -> int:
            n = int(await self.client.decr(k))
            if n <= 0:
                await self.client.delete(k)
                return 0
            return n

        return await self._run("release", remote, lambda: self.local.release(key))

    async def get(self, key: str) -> str | None:  # ruff: ignore[undocumented-public-method]
        async def remote() -> str | None:
            value = await self.client.get(self._k(key))
            return None if value is None else str(value)

        return await self._run("get", remote, lambda: self.local.get(key))

    async def set(self, key: str, value: str, ttl_seconds: float) -> None:  # ruff: ignore[undocumented-public-method]
        ms = max(1, int(ttl_seconds * 1000))

        async def remote() -> None:
            await self.client.set(self._k(key), value, px=ms)

        await self._run("set", remote, lambda: self.local.set(key, value, ttl_seconds))

    async def set_once(self, key: str, value: str, ttl_seconds: float) -> bool:  # ruff: ignore[undocumented-public-method]
        ms = max(1, int(ttl_seconds * 1000))

        async def remote() -> bool:
            return bool(await self.client.set(self._k(key), value, px=ms, nx=True))

        return await self._run(
            "set_once", remote, lambda: self.local.set_once(key, value, ttl_seconds)
        )

    async def take(self, key: str) -> str | None:  # ruff: ignore[undocumented-public-method]
        async def remote() -> str | None:
            value = await self.client.getdel(self._k(key))
            return None if value is None else str(value)

        return await self._run("take", remote, lambda: self.local.take(key))

    async def delete(self, key: str) -> None:  # ruff: ignore[undocumented-public-method]
        async def remote() -> None:
            await self.client.delete(self._k(key))

        await self._run("delete", remote, lambda: self.local.delete(key))

    async def ping(self) -> bool:  # ruff: ignore[undocumented-public-method]
        try:
            ok = bool(await self.client.ping())
        except Exception as e:
            self._failed("ping", e)
            return False
        self._down_until = 0.0
        self.last_error = None
        return ok

    async def close(self) -> None:  # ruff: ignore[undocumented-public-method]
        try:
            await self.client.aclose()
        except Exception as e:
            log.debug("shared_state_close_failed", extra={"error": type(e).__name__})


def build_shared(url: str | None, prefix: str = "evokai:") -> SharedState:
    """The shared state for the deployment's settings.

    Returns:
        Redis when a URL is set, else this process's memory.
    """
    if url:
        return RedisState(url, prefix=prefix)
    return MemoryState()
