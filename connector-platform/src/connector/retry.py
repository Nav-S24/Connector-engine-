"""Exponential backoff, jitter, Retry-After handling, and TokenBucket rate limiting."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable

import httpx

DEFAULT_RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryConfig:
    """Configuration for HTTP retry and exponential backoff."""

    max_retries: int = 4
    base_delay: float = 0.25
    max_delay: float = 30.0
    jitter: bool = True
    retry_statuses: frozenset[int] = field(
        default_factory=lambda: DEFAULT_RETRY_STATUSES
    )


class TokenBucket:
    """Async token-bucket rate limiter enforcing requests per second."""

    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("TokenBucket rate must be positive")
        self.rate = float(rate)
        self.capacity = float(capacity) if capacity is not None else max(1.0, self.rate)
        self._clock = clock
        self._sleep_fn = sleep_fn
        self._tokens = self.capacity
        self._last_refill = self._clock()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> float:
        """Wait until tokens are available and consume them; return slept seconds."""
        if tokens > self.capacity:
            raise ValueError("Requested tokens exceed bucket capacity")

        total_slept = 0.0
        async with self._lock:
            while True:
                now = self._clock()
                elapsed = max(0.0, now - self._last_refill)
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last_refill = now

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return total_slept

                wait_time = (tokens - self._tokens) / self.rate
                await self._sleep_fn(wait_time)
                total_slept += wait_time


def parse_retry_after(value: str | None) -> float | None:
    """Parse an HTTP Retry-After header (seconds or HTTP-date) into delay seconds."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return max(0.0, float(stripped))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(stripped)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except (TypeError, ValueError):
        return None


def compute_backoff(
    attempt: int,
    base_delay: float,
    max_delay: float,
    jitter: bool = True,
    retry_after: float | None = None,
    rng: random.Random | None = None,
) -> float:
    """Compute delay in seconds for the given retry attempt (0-indexed)."""
    if retry_after is not None:
        return min(max_delay, max(0.0, retry_after))
    raw_delay = min(max_delay, base_delay * (2**attempt))
    if not jitter:
        return raw_delay
    generator = rng if rng is not None else random
    return generator.uniform(0.5 * raw_delay, raw_delay)


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    retry_config: RetryConfig | None = None,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: Callable[[int, float, int | None], None] | None = None,
    rate_limiter: TokenBucket | None = None,
    on_unauthorized: Callable[[], Awaitable[dict[str, str] | None]] | None = None,
) -> httpx.Response:
    """Execute an HTTP request with rate limiting, 401 refresh, and backoff retries."""
    cfg = retry_config or RetryConfig()
    active_headers = dict(headers) if headers else None
    refreshed_auth = False
    last_exc: Exception | None = None

    for attempt in range(cfg.max_retries + 1):
        if rate_limiter is not None:
            await rate_limiter.acquire()

        try:
            response = await client.request(
                method=method,
                url=url,
                headers=active_headers,
                params=params,
            )
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt >= cfg.max_retries:
                raise
            delay = compute_backoff(
                attempt=attempt,
                base_delay=cfg.base_delay,
                max_delay=cfg.max_delay,
                jitter=cfg.jitter,
            )
            if on_retry is not None:
                on_retry(attempt + 1, delay, None)
            await sleep_fn(delay)
            continue

        if (
            response.status_code == 401
            and not refreshed_auth
            and on_unauthorized is not None
        ):
            new_headers = await on_unauthorized()
            refreshed_auth = True
            if new_headers is not None:
                active_headers = {**(active_headers or {}), **new_headers}
                if rate_limiter is not None:
                    await rate_limiter.acquire()
                response = await client.request(
                    method=method,
                    url=url,
                    headers=active_headers,
                    params=params,
                )

        if response.status_code in cfg.retry_statuses:
            if attempt >= cfg.max_retries:
                response.raise_for_status()
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            delay = compute_backoff(
                attempt=attempt,
                base_delay=cfg.base_delay,
                max_delay=cfg.max_delay,
                jitter=cfg.jitter,
                retry_after=retry_after,
            )
            if on_retry is not None:
                on_retry(attempt + 1, delay, response.status_code)
            await sleep_fn(delay)
            continue

        response.raise_for_status()
        return response

    assert last_exc is not None  # pragma: no cover
    raise last_exc
