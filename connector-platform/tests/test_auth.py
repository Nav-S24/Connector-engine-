"""Unit tests for API-key, bearer, and OAuth2 refresh authentication strategies."""

from __future__ import annotations

import httpx
import pytest
import respx

from connector.auth import (
    ApiKeyAuth,
    AuthError,
    BearerAuth,
    OAuth2RefreshAuth,
    build_auth,
)
from connector.config import ApiKeyAuthConfig, BearerAuthConfig, OAuth2AuthConfig


async def test_api_key_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """ApiKeyAuth reads the secret from env and returns the configured header."""
    monkeypatch.setenv("TEST_API_KEY", "secret-123")
    auth = build_auth(
        ApiKeyAuthConfig(type="api_key", header="X-Custom-Key", env_var="TEST_API_KEY")
    )
    assert isinstance(auth, ApiKeyAuth)
    assert await auth.get_headers() == {"X-Custom-Key": "secret-123"}


async def test_api_key_auth_missing_env_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ApiKeyAuth raises AuthError when env var is missing or blank."""
    monkeypatch.delenv("MISSING_KEY", raising=False)
    auth = ApiKeyAuth(header="X-API-Key", env_var="MISSING_KEY")
    with pytest.raises(AuthError, match="MISSING_KEY"):
        await auth.get_headers()


async def test_bearer_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """BearerAuth reads the token from env and formats Authorization Bearer."""
    monkeypatch.setenv("TEST_BEARER", "tok-xyz")
    auth = build_auth(BearerAuthConfig(type="bearer", env_var="TEST_BEARER"))
    assert isinstance(auth, BearerAuth)
    assert await auth.get_headers() == {"Authorization": "Bearer tok-xyz"}


async def test_bearer_auth_empty_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """BearerAuth raises AuthError when the token env var is whitespace."""
    monkeypatch.setenv("EMPTY_BEARER", "   ")
    auth = BearerAuth(env_var="EMPTY_BEARER")
    with pytest.raises(AuthError, match="EMPTY_BEARER"):
        await auth.get_headers()


@respx.mock
async def test_oauth2_refresh_on_expiry_and_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OAuth2RefreshAuth refreshes when expired and on 401."""
    monkeypatch.setenv("OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("OAUTH_REFRESH_TOKEN", "rtok")

    now = [100.0]
    token_route = respx.post("https://auth.example.com/oauth/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "acc_1", "expires_in": 30}),
            httpx.Response(200, json={"access_token": "acc_2", "expires_in": 60}),
            httpx.Response(200, json={"access_token": "acc_3", "expires_in": 60}),
        ]
    )

    cfg = OAuth2AuthConfig(
        type="oauth2",
        token_url="https://auth.example.com/oauth/token",
        client_id_env_var="OAUTH_CLIENT_ID",
        client_secret_env_var="OAUTH_CLIENT_SECRET",
        refresh_token_env_var="OAUTH_REFRESH_TOKEN",
    )
    async with httpx.AsyncClient() as client:
        auth = build_auth(cfg, client=client)
        assert isinstance(auth, OAuth2RefreshAuth)
        auth._clock = lambda: now[0]

        assert await auth.get_headers() == {"Authorization": "Bearer acc_1"}
        # Still valid at t=110 (expires at 130, buffer 5s)
        now[0] = 110.0
        assert await auth.get_headers() == {"Authorization": "Bearer acc_1"}

        # Near expiry at t=126 -> refreshes automatically
        now[0] = 126.0
        assert await auth.get_headers() == {"Authorization": "Bearer acc_2"}

        # Reactive refresh after 401
        refreshed = await auth.refresh_on_unauthorized()
        assert refreshed == {"Authorization": "Bearer acc_3"}

    assert token_route.call_count == 3


async def test_oauth2_missing_credentials_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OAuth2RefreshAuth raises AuthError when credentials are missing."""
    monkeypatch.delenv("OAUTH_CLIENT_ID", raising=False)
    auth = OAuth2RefreshAuth(
        token_url="https://auth.example.com/oauth/token",
        client_id_env_var="OAUTH_CLIENT_ID",
        client_secret_env_var="OAUTH_CLIENT_SECRET",
        refresh_token_env_var="OAUTH_REFRESH_TOKEN",
    )
    with pytest.raises(AuthError, match="OAUTH_CLIENT_ID"):
        await auth.get_headers()
