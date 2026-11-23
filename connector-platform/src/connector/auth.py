"""Authentication strategies for connector HTTP requests."""

from __future__ import annotations

import asyncio
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable

import httpx

from connector.config import (
    ApiKeyAuthConfig,
    AuthConfig,
    BearerAuthConfig,
    OAuth2AuthConfig,
)


class AuthError(RuntimeError):
    """Raised when required authentication credentials cannot be resolved."""


class AuthStrategy(ABC):
    """Abstract base class for HTTP request authentication strategies."""

    @abstractmethod
    async def get_headers(self) -> dict[str, str]:
        """Return HTTP headers required to authenticate a request."""

    async def refresh_on_unauthorized(self) -> dict[str, str] | None:
        """Refresh credentials after a 401 response if supported by the strategy."""
        return None


class ApiKeyAuth(AuthStrategy):
    """Authenticate using an API key read from an environment variable."""

    def __init__(self, header: str, env_var: str) -> None:
        self.header = header
        self.env_var = env_var

    async def get_headers(self) -> dict[str, str]:
        """Return the custom API-key header."""
        secret = os.environ.get(self.env_var, "").strip()
        if not secret:
            raise AuthError(
                f"Missing or empty API key in environment variable '{self.env_var}'"
            )
        return {self.header: secret}


class BearerAuth(AuthStrategy):
    """Authenticate using a static Bearer token from an environment variable."""

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var

    async def get_headers(self) -> dict[str, str]:
        """Return the Authorization Bearer header."""
        token = os.environ.get(self.env_var, "").strip()
        if not token:
            raise AuthError(
                f"Missing or empty bearer token in environment variable '{self.env_var}'"
            )
        return {"Authorization": f"Bearer {token}"}


class OAuth2RefreshAuth(AuthStrategy):
    """OAuth2 refresh-token flow with automatic expiry and 401 refresh."""

    def __init__(
        self,
        token_url: str,
        client_id_env_var: str,
        client_secret_env_var: str,
        refresh_token_env_var: str,
        scope: str | None = None,
        client: httpx.AsyncClient | None = None,
        expiry_buffer_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.token_url = token_url
        self.client_id_env_var = client_id_env_var
        self.client_secret_env_var = client_secret_env_var
        self.refresh_token_env_var = refresh_token_env_var
        self.scope = scope
        self._client = client
        self.expiry_buffer_seconds = expiry_buffer_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._access_token: str | None = None
        self._expires_at: float | None = None

    def _is_expired(self) -> bool:
        """Return True if the access token is absent or near/past expiry."""
        if self._access_token is None:
            return True
        if self._expires_at is None:
            return False
        return self._clock() >= (self._expires_at - self.expiry_buffer_seconds)

    def _read_env(self, var_name: str) -> str:
        value = os.environ.get(var_name, "").strip()
        if not value:
            raise AuthError(
                f"Missing or empty OAuth2 credential in environment variable '{var_name}'"
            )
        return value

    async def refresh(self) -> str:
        """Exchange the refresh token for a fresh OAuth2 access token."""
        async with self._lock:
            client_id = self._read_env(self.client_id_env_var)
            client_secret = self._read_env(self.client_secret_env_var)
            refresh_token = self._read_env(self.refresh_token_env_var)

            form: dict[str, str] = {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            }
            if self.scope:
                form["scope"] = self.scope

            owns_client = self._client is None
            http_client = (
                self._client
                if self._client is not None
                else httpx.AsyncClient(timeout=15.0)
            )
            try:
                response = await http_client.post(self.token_url, data=form)
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError as exc:
                raise AuthError(f"OAuth2 token refresh failed: {exc}") from exc
            finally:
                if owns_client:
                    await http_client.aclose()

            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise AuthError("OAuth2 token response missing 'access_token'")

            expires_in = payload.get("expires_in")
            self._access_token = token
            if isinstance(expires_in, (int, float)):
                self._expires_at = self._clock() + float(expires_in)
            else:
                self._expires_at = None
            return token

    async def get_headers(self) -> dict[str, str]:
        """Return Authorization Bearer headers, refreshing if missing or expired."""
        if self._is_expired():
            await self.refresh()
        return {"Authorization": f"Bearer {self._access_token}"}

    async def refresh_on_unauthorized(self) -> dict[str, str] | None:
        """Force a token refresh after an HTTP 401 and return new headers."""
        await self.refresh()
        return {"Authorization": f"Bearer {self._access_token}"}


def build_auth(
    config: AuthConfig,
    client: httpx.AsyncClient | None = None,
) -> AuthStrategy:
    """Instantiate an AuthStrategy from a validated AuthConfig."""
    if isinstance(config, ApiKeyAuthConfig):
        return ApiKeyAuth(header=config.header, env_var=config.env_var)
    if isinstance(config, BearerAuthConfig):
        return BearerAuth(env_var=config.env_var)
    if isinstance(config, OAuth2AuthConfig):
        return OAuth2RefreshAuth(
            token_url=config.token_url,
            client_id_env_var=config.client_id_env_var,
            client_secret_env_var=config.client_secret_env_var,
            refresh_token_env_var=config.refresh_token_env_var,
            scope=config.scope,
            client=client,
        )
    raise AuthError(f"Unsupported auth config: {config}")
