"""FastAPI mock SaaS server simulating paystub, gitlike, and sheetsy APIs."""

from __future__ import annotations

import os
import random
import time
from typing import Any, Literal
from urllib.parse import parse_qs

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from mock_server.faults import (
    FaultConfig,
    FaultInjector,
    ServiceMutation,
    set_nested_path,
)


class FaultUpdateRequest(BaseModel):
    """Payload for updating runtime fault injection settings."""

    latency_ms: float | None = None
    rate_429: float | None = None
    rate_500: float | None = None
    retry_after: str | None = None
    seed: int | None = None


class MutateRequest(BaseModel):
    """Payload for applying or resetting schema and API contract mutations."""

    service: Literal["paystub", "gitlike", "sheetsy"]
    reset: bool = False
    rename_fields: dict[str, str] = Field(default_factory=dict)
    add_fields: dict[str, Any] = Field(default_factory=dict)
    remove_fields: list[str] = Field(default_factory=list)
    retype_fields: dict[str, str] = Field(default_factory=dict)
    records_path: str | None = None
    cursor_param: str | None = None
    offset_param: str | None = None
    page_size_param: str | None = None
    has_more_field: str | None = None
    total_field: str | None = None


def _generate_paystub_charges(count: int, seed: int = 42) -> list[dict[str, Any]]:
    """Deterministically generate Stripe-like charge records."""
    rng = random.Random(seed)
    currencies = ("usd", "eur", "gbp")
    base_ts = 1_700_000_000
    return [
        {
            "id": f"ch_{idx + 1:06d}",
            "amount": rng.randint(100, 50_000),
            "currency": currencies[idx % len(currencies)],
            "created": base_ts + idx * 60,
        }
        for idx in range(count)
    ]


def _generate_gitlike_repos(count: int, seed: int = 42) -> list[dict[str, Any]]:
    """Deterministically generate repository records."""
    rng = random.Random(seed)
    return [
        {
            "id": idx + 1,
            "name": f"repo-{idx + 1:06d}",
            "stars": rng.randint(0, 10_000),
            "private": (idx % 5 == 0),
            "updated_at": f"2026-01-{(idx % 28) + 1:02d}T12:00:00Z",
        }
        for idx in range(count)
    ]


def _generate_sheetsy_sheets(count: int, seed: int = 42) -> list[dict[str, Any]]:
    """Deterministically generate spreadsheet metadata records."""
    rng = random.Random(seed)
    return [
        {
            "id": f"sh_{idx + 1:05d}",
            "title": f"Sheet {idx + 1}",
            "row_count": rng.randint(10, 5000),
            "updated_at": f"2026-02-{(idx % 28) + 1:02d}T{idx % 24:02d}:00:00Z",
        }
        for idx in range(count)
    ]


def _parse_int_param(
    request: Request, name: str, default: int, minimum: int = 0
) -> int:
    """Parse an integer query parameter from request.query_params safely."""
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        val = int(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid integer query param '{name}'"
        ) from exc
    if val < minimum:
        raise HTTPException(
            status_code=422, detail=f"Query param '{name}' must be >= {minimum}"
        )
    return val


def create_app(
    paystub_count: int | None = None,
    gitlike_count: int | None = None,
    sheetsy_count: int | None = None,
    seed: int = 42,
    paystub_api_key: str | None = None,
    gitlike_token: str | None = None,
    sheetsy_client_id: str | None = None,
    sheetsy_client_secret: str | None = None,
    sheetsy_refresh_token: str | None = None,
    sheetsy_token_ttl: float = 300.0,
    fault_config: FaultConfig | None = None,
) -> FastAPI:
    """Create and configure the FastAPI mock server application."""
    p_count = (
        paystub_count
        if paystub_count is not None
        else int(os.environ.get("PAYSTUB_RECORD_COUNT", "250"))
    )
    g_count = (
        gitlike_count
        if gitlike_count is not None
        else int(os.environ.get("GITLIKE_RECORD_COUNT", "120"))
    )
    s_count = (
        sheetsy_count
        if sheetsy_count is not None
        else int(os.environ.get("SHEETSY_RECORD_COUNT", "95"))
    )

    expected_paystub_key = paystub_api_key or os.environ.get(
        "PAYSTUB_API_KEY", "paystub-test-key"
    )
    expected_gitlike_token = gitlike_token or os.environ.get(
        "GITLIKE_TOKEN", "gitlike-test-token"
    )
    expected_client_id = sheetsy_client_id or os.environ.get(
        "SHEETSY_CLIENT_ID", "sheetsy-client-id"
    )
    expected_client_secret = sheetsy_client_secret or os.environ.get(
        "SHEETSY_CLIENT_SECRET", "sheetsy-client-secret"
    )
    expected_refresh_token = sheetsy_refresh_token or os.environ.get(
        "SHEETSY_REFRESH_TOKEN", "sheetsy-refresh-token"
    )

    app = FastAPI(title="Connector Platform Mock SaaS Server", version="0.4.0")
    app.state.charges = _generate_paystub_charges(p_count, seed=seed)
    app.state.repos = _generate_gitlike_repos(g_count, seed=seed)
    app.state.sheets = _generate_sheetsy_sheets(s_count, seed=seed)
    app.state.oauth_tokens: dict[str, float] = {}
    app.state.oauth_counter = 0
    app.state.faults = FaultInjector(config=fault_config, seed=seed)
    app.state.mutations: dict[str, ServiceMutation] = {
        "paystub": ServiceMutation(),
        "gitlike": ServiceMutation(),
        "sheetsy": ServiceMutation(),
    }

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Return server health status."""
        return {"status": "ok"}

    @app.post("/admin/faults")
    async def update_faults(body: FaultUpdateRequest) -> dict[str, Any]:
        """Update runtime fault injection parameters."""
        cfg = app.state.faults.config
        if body.latency_ms is not None:
            cfg.latency_ms = body.latency_ms
        if body.rate_429 is not None:
            cfg.rate_429 = body.rate_429
        if body.rate_500 is not None:
            cfg.rate_500 = body.rate_500
        if body.retry_after is not None:
            cfg.retry_after = body.retry_after
        if body.seed is not None:
            app.state.faults.reset(seed=body.seed)
        return {
            "latency_ms": cfg.latency_ms,
            "rate_429": cfg.rate_429,
            "rate_500": cfg.rate_500,
            "retry_after": cfg.retry_after,
        }

    @app.post("/admin/mutate")
    async def mutate_service(body: MutateRequest) -> dict[str, Any]:
        """Apply or reset runtime schema/API contract mutations for a mock service."""
        if body.reset:
            app.state.mutations[body.service] = ServiceMutation()
        else:
            app.state.mutations[body.service] = ServiceMutation(
                rename_fields=dict(body.rename_fields),
                add_fields=dict(body.add_fields),
                remove_fields=list(body.remove_fields),
                retype_fields=dict(body.retype_fields),
                records_path=body.records_path,
                cursor_param=body.cursor_param,
                offset_param=body.offset_param,
                page_size_param=body.page_size_param,
                has_more_field=body.has_more_field,
                total_field=body.total_field,
            )
        mut = app.state.mutations[body.service]
        return {
            "service": body.service,
            "active": mut.is_active(),
            "rename_fields": mut.rename_fields,
            "add_fields": mut.add_fields,
            "remove_fields": mut.remove_fields,
            "retype_fields": mut.retype_fields,
            "records_path": mut.records_path,
            "cursor_param": mut.cursor_param,
            "offset_param": mut.offset_param,
            "page_size_param": mut.page_size_param,
        }

    @app.get("/paystub/v1/charges")
    async def list_paystub_charges(
        request: Request,
        created_gte: int | None = Query(default=None),
        latency_ms: float | None = Query(default=None),
        rate_429: float | None = Query(default=None),
        rate_500: float | None = Query(default=None),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> dict[str, Any]:
        """Stripe-like cursor-paginated charges endpoint protected by API key."""
        if not x_api_key or x_api_key != expected_paystub_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

        await app.state.faults.maybe_inject(
            latency_ms=latency_ms, rate_429=rate_429, rate_500=rate_500
        )

        mut: ServiceMutation = app.state.mutations["paystub"]
        cursor_name = mut.cursor_param or "starting_after"
        limit_name = mut.page_size_param or "limit"
        starting_after = request.query_params.get(cursor_name)
        limit = min(5000, _parse_int_param(request, limit_name, default=100, minimum=1))

        records = mut.transform_records(app.state.charges)
        if created_gte is not None:
            records = [
                r
                for r in records
                if isinstance(r.get("created"), (int, float))
                and r["created"] >= created_gte
            ]

        pk_field = mut.rename_fields.get("id", "id")
        rec_path = mut.records_path or "data"
        has_more_key = mut.has_more_field or "has_more"

        start_idx = 0
        if starting_after is not None:
            for idx, record in enumerate(records):
                if str(record.get(pk_field)) == starting_after:
                    start_idx = idx + 1
                    break
            else:
                empty_payload: dict[str, Any] = {
                    "object": "list",
                    has_more_key: False,
                }
                return set_nested_path(empty_payload, rec_path, [])

        page = records[start_idx : start_idx + limit]
        has_more = (start_idx + limit) < len(records)
        payload: dict[str, Any] = {"object": "list", has_more_key: has_more}
        return set_nested_path(payload, rec_path, page)

    @app.get("/gitlike/v1/repos")
    async def list_gitlike_repos(
        request: Request,
        latency_ms: float | None = Query(default=None),
        rate_429: float | None = Query(default=None),
        rate_500: float | None = Query(default=None),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        """Offset-paginated repos endpoint with nested envelope and bearer auth."""
        if authorization != f"Bearer {expected_gitlike_token}":
            raise HTTPException(status_code=401, detail="Invalid bearer token")

        await app.state.faults.maybe_inject(
            latency_ms=latency_ms, rate_429=rate_429, rate_500=rate_500
        )

        mut: ServiceMutation = app.state.mutations["gitlike"]
        offset_name = mut.offset_param or "offset"
        limit_name = mut.page_size_param or "limit"
        offset = _parse_int_param(request, offset_name, default=0, minimum=0)
        limit = min(5000, _parse_int_param(request, limit_name, default=50, minimum=1))

        all_repos = mut.transform_records(app.state.repos)
        page = all_repos[offset : offset + limit]
        total_key = mut.total_field or "total"
        rec_path = mut.records_path or "result.items"

        payload: dict[str, Any] = {
            "meta": {
                total_key: len(all_repos),
                offset_name: offset,
                limit_name: limit,
            }
        }
        return set_nested_path(payload, rec_path, page)

    @app.post("/sheetsy/oauth/token")
    async def issue_sheetsy_token(request: Request) -> dict[str, Any]:
        """OAuth2 refresh-token endpoint issuing short-lived access tokens."""
        raw_body = (await request.body()).decode("utf-8")
        parsed = {k: v[0] for k, v in parse_qs(raw_body).items() if v}
        grant_type = parsed.get("grant_type")
        client_id = parsed.get("client_id")
        client_secret = parsed.get("client_secret")
        refresh_token = parsed.get("refresh_token")

        if (
            grant_type != "refresh_token"
            or client_id != expected_client_id
            or client_secret != expected_client_secret
            or refresh_token != expected_refresh_token
        ):
            raise HTTPException(status_code=401, detail="Invalid OAuth2 credentials")

        app.state.oauth_counter += 1
        token = f"sheetsy_access_{app.state.oauth_counter}"
        app.state.oauth_tokens[token] = time.monotonic() + sheetsy_token_ttl
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": sheetsy_token_ttl,
        }

    @app.get("/sheetsy/v1/sheets")
    async def list_sheetsy_sheets(
        request: Request,
        response: Response,
        page_token: int = Query(default=0, ge=0),
        updated_after: str | None = Query(default=None),
        latency_ms: float | None = Query(default=None),
        rate_429: float | None = Query(default=None),
        rate_500: float | None = Query(default=None),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, Any]:
        """OAuth2-protected endpoint with RFC 5988 Link header pagination."""
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = authorization.removeprefix("Bearer ").strip()
        expiry = app.state.oauth_tokens.get(token)
        if expiry is None or time.monotonic() >= expiry:
            raise HTTPException(status_code=401, detail="Expired or invalid token")

        await app.state.faults.maybe_inject(
            latency_ms=latency_ms, rate_429=rate_429, rate_500=rate_500
        )

        mut: ServiceMutation = app.state.mutations["sheetsy"]
        limit_name = mut.page_size_param or "limit"
        limit = min(5000, _parse_int_param(request, limit_name, default=40, minimum=1))

        filtered = mut.transform_records(app.state.sheets)
        if updated_after is not None:
            filtered = [
                r
                for r in filtered
                if isinstance(r.get("updated_at"), str)
                and r["updated_at"] >= updated_after
            ]

        page = filtered[page_token : page_token + limit]
        next_token = page_token + len(page)
        if next_token < len(filtered) and page:
            base = str(request.url).split("?")[0]
            next_params = f"page_token={next_token}&{limit_name}={limit}"
            if updated_after is not None:
                next_params += f"&updated_after={updated_after}"
            response.headers["Link"] = f'<{base}?{next_params}>; rel="next"'

        rec_path = mut.records_path or "rows"
        return set_nested_path({}, rec_path, page)

    return app


app = create_app()
