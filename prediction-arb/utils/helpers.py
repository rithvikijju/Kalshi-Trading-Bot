"""Async retry + rate limit helpers for low-latency client code."""
from __future__ import annotations
import asyncio
import functools
import time
from typing import Callable, Any


def retry_async(max_retries: int = 3, base_delay: float = 0.2,
                max_delay: float = 5.0, exceptions: tuple = (Exception,)):
    """Exponential backoff decorator for async functions."""
    def decorator(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    await asyncio.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


class AsyncRateLimiter:
    """Token-bucket-style rate limiter for low-latency async calls."""
    def __init__(self, max_calls_per_sec: float):
        self.interval = 1.0 / max_calls_per_sec
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            wait = self._last + self.interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
