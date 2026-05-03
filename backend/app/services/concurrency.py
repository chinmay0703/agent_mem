"""Per-key asyncio locks and a simple in-process token-bucket rate limiter.

Both are process-local — fine for a single Uvicorn worker / dev. For multi-
worker production swap to Redis. Kept intentionally small and dependency-free.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional


class KeyedLock:
    """Async lock-per-key registry. Used to serialize /chat for the same
    (user, thread) pair so concurrent requests don't corrupt the rolling
    summary or double-write triples."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._guard = asyncio.Lock()

    async def acquire(self, key: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.setdefault(key, asyncio.Lock())
        return lock


class TokenBucket:
    """Per-key token bucket. `capacity` is tokens; `refill_per_sec` is rate.
    `take(key)` returns False if rate-limited."""

    def __init__(self, capacity: int, refill_per_sec: float) -> None:
        self.capacity = capacity
        self.refill = refill_per_sec
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    async def take(self, key: str, n: int = 1) -> bool:
        if self.capacity <= 0:
            return True  # disabled
        now = time.monotonic()
        async with self._lock:
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill)
            if tokens < n:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - n, now)
            return True


_chat_lock: Optional[KeyedLock] = None
_chat_bucket: Optional[TokenBucket] = None


def get_chat_lock() -> KeyedLock:
    global _chat_lock
    if _chat_lock is None:
        _chat_lock = KeyedLock()
    return _chat_lock


def get_chat_rate_limiter(per_min: int) -> TokenBucket:
    global _chat_bucket
    if _chat_bucket is None or _chat_bucket.capacity != per_min:
        _chat_bucket = TokenBucket(capacity=per_min, refill_per_sec=per_min / 60.0)
    return _chat_bucket
