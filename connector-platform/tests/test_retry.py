"""Unit tests for retry, exponential backoff, Retry-After, and TokenBucket."""

from __future__ import annotations

import random

import httpx
import pytest
import respx

from connector.retry import (
    RetryConfig,
    TokenBucket,
    compute_backoff,
    parse_retry_after,
    request_with_retry,
)


def test_parse_retry_after_numeric_and_invalid() -> None:
    """parse_retry_after parses numeric seconds and ignores blank/invalid strings."""
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after("0.5") == 0.5
    assert parse_retry_after("-2") == 0.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("not-a-date") is None


def test_compute_backoff_exponential_and_retry_after() -> None:
    """compute_backoff doubles without jitter and honours Retry-After."""
    assert compute_backoff(0, base_delay=0.5, max_delay=10.0, jitter=False) == 0.5
    assert compute_backoff(2, base_delay=0.5, max_delay=10.0, jitter=False) == 2.0
    assert compute_backoff(10, base_delay=0.5, max_delay=5.0, jitter=False) == 5.0
    assert (
        compute_backoff(
            0, base_delay=0.5, max_delay=10.0, jitter=True, retry_after=4.2
        )
        == 4.2
    )

    rng = random.Random(1)
    jittered = compute_backoff(1, base_delay=1.0, max_delay=10.0, jitter=True, rng=rng)
    assert 1.0 <= jittered <= 2.0


@respx.mock
async def test_request_with_retry_honours_retry_after_and_5xx() -> None:
    """429 with Retry-After and 503 are retried with expected delays until 200."""
    route = respx.get("https://api.example.com/items").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1.5"}),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    cfg = RetryConfig(max_retries=3, base_delay=0.2, max_delay=10.0, jitter=False)
    async with httpx.AsyncClient() as client:
        resp = await request_with_retry(
            client,
            "GET",
            "https://api.example.com/items",
            retry_config=cfg,
            sleep_fn=fake_sleep,
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert route.call_count == 3
    assert sleeps == [1.5, 0.4]


@respx.mock
async def test_request_with_retry_exhaustion_raises() -> None:
    """Persistent 500 responses raise HTTPStatusError after max_retries."""
    respx.get("https://api.example.com/fail").mock(
        return_value=httpx.Response(500)
    )

    async def fake_sleep(_delay: float) -> None:
        return None

    cfg = RetryConfig(max_retries=2, base_delay=0.1, jitter=False)
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await request_with_retry(
                client,
                "GET",
                "https://api.example.com/fail",
                retry_config=cfg,
                sleep_fn=fake_sleep,
            )


def test_parse_retry_after_http_date() -> None:
    """parse_retry_after parses HTTP-date strings into non-negative seconds."""
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    parsed = parse_retry_after(format_datetime(future, usegmt=True))
    assert parsed is not None
    assert 5.0 <= parsed <= 11.0


@respx.mock
async def test_request_with_retry_handles_transport_error() -> None:
    """TransportError triggers backoff retries and raises after max_retries."""
    route = respx.get("https://api.example.com/flaky").mock(
        side_effect=[
            httpx.ConnectTimeout("timeout"),
            httpx.Response(200, json={"recovered": True}),
        ]
    )
    retries_seen: list[int] = []

    async def fake_sleep(_delay: float) -> None:
        return None

    cfg = RetryConfig(max_retries=2, base_delay=0.1, jitter=False)
    async with httpx.AsyncClient() as client:
        resp = await request_with_retry(
            client,
            "GET",
            "https://api.example.com/flaky",
            retry_config=cfg,
            sleep_fn=fake_sleep,
            on_retry=lambda attempt, _d, _s: retries_seen.append(attempt),
        )
    assert resp.json() == {"recovered": True}
    assert route.call_count == 2
    assert retries_seen == [1]


async def test_token_bucket_rate_limiting() -> None:
    """TokenBucket enforces rate limit by sleeping when bucket is empty."""
    now = [0.0]
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        now[0] += delay

    bucket = TokenBucket(
        rate=4.0,
        capacity=2.0,
        clock=lambda: now[0],
        sleep_fn=fake_sleep,
    )
    # First two requests use initial capacity without sleeping
    assert await bucket.acquire() == 0.0
    assert await bucket.acquire() == 0.0
    # Third request must wait 0.25s for 1 token at 4 req/sec
    slept = await bucket.acquire()
    assert slept == pytest.approx(0.25)
    assert sleeps == [pytest.approx(0.25)]


@respx.mock
async def test_request_with_retry_refreshes_on_401() -> None:
    """request_with_retry invokes on_unauthorized once on 401 and retries."""
    route = respx.get("https://api.example.com/secure").mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    async def refresh() -> dict[str, str]:
        return {"Authorization": "Bearer fresh-token"}

    async with httpx.AsyncClient() as client:
        resp = await request_with_retry(
            client,
            "GET",
            "https://api.example.com/secure",
            headers={"Authorization": "Bearer expired"},
            on_unauthorized=refresh,
        )
    assert resp.status_code == 200
    assert route.call_count == 2
    assert route.calls[1].request.headers["Authorization"] == "Bearer fresh-token"
