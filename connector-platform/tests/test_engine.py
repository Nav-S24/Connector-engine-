"""Integration tests for ConnectorEngine against the FastAPI mock server."""

from __future__ import annotations

import asyncio
import json
import logging
from io import StringIO
from pathlib import Path

import httpx
import pytest
import respx

from connector.config import load_config, load_config_from_yaml
from connector.destination import DuckDBDestination
from connector.engine import ConnectorEngine
from connector.logging_utils import JsonLogFormatter
from connector.retry import RetryConfig
from mock_server.app import create_app


async def test_engine_runs_paystub_incremental_and_zero_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running paystub twice yields zero duplicates and second run fetches only new data."""
    monkeypatch.setenv("PAYSTUB_API_KEY", "paystub-test-key")
    mock_app = create_app(paystub_count=250, gitlike_count=20, seed=42)
    config = load_config(
        Path(__file__).resolve().parents[1] / "connectors" / "paystub.yaml"
    )

    transport = httpx.ASGITransport(app=mock_app)
    with DuckDBDestination(":memory:") as dest:
        async with httpx.AsyncClient(transport=transport) as client:
            engine = ConnectorEngine(destination=dest, client=client)

            first = await engine.run(config)
            assert first.status == "success"
            assert first.total_rows == 250
            assert first.streams["charges"].pages_fetched == 3
            assert dest.count("paystub_charges") == 250
            first_bookmark = engine.state_store.get_bookmark("paystub", "charges")
            assert first_bookmark == 1_700_000_000 + 249 * 60

            # Append 5 new charges on the source API
            for idx in range(5):
                mock_app.state.charges.append(
                    {
                        "id": f"ch_{251 + idx:06d}",
                        "amount": 1200 + idx,
                        "currency": "usd",
                        "created": first_bookmark + (idx + 1) * 60,
                    }
                )

            second = await engine.run(config)
            assert second.status == "success"
            # Incremental run fetches boundary + 5 new records (6 total, << 255)
            assert second.total_rows == 6
            assert second.streams["charges"].pages_fetched == 1
            assert dest.count("paystub_charges") == 255
            assert (
                engine.state_store.get_bookmark("paystub", "charges")
                == first_bookmark + 5 * 60
            )

            summaries = dest.fetch_run_summaries("paystub")
            assert len(summaries) == 2
            assert all(s["status"] == "success" for s in summaries)


async def test_engine_runs_gitlike_offset_nested_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running gitlike fetches offset-paginated records from nested envelope."""
    monkeypatch.setenv("GITLIKE_TOKEN", "gitlike-test-token")
    mock_app = create_app(paystub_count=10, gitlike_count=120, seed=42)
    config = load_config(
        Path(__file__).resolve().parents[1] / "connectors" / "gitlike.yaml"
    )

    transport = httpx.ASGITransport(app=mock_app)
    with DuckDBDestination(":memory:") as dest:
        async with httpx.AsyncClient(transport=transport) as client:
            engine = ConnectorEngine(destination=dest, client=client)
            result = await engine.run(config)

            assert result.status == "success"
            assert result.total_rows == 120
            assert result.streams["repos"].pages_fetched == 3
            assert dest.count("gitlike_repos") == 120


async def test_engine_runs_sheetsy_oauth2_and_link_header_with_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sheetsy syncs via OAuth2 + Link-header pagination and refreshes on expired token."""
    monkeypatch.setenv("SHEETSY_CLIENT_ID", "sheetsy-client-id")
    monkeypatch.setenv("SHEETSY_CLIENT_SECRET", "sheetsy-client-secret")
    monkeypatch.setenv("SHEETSY_REFRESH_TOKEN", "sheetsy-refresh-token")

    # Very short TTL forces token expiration & 401 refresh mid-sync
    mock_app = create_app(
        paystub_count=10,
        gitlike_count=10,
        sheetsy_count=95,
        sheetsy_token_ttl=0.02,
    )
    config = load_config(
        Path(__file__).resolve().parents[1] / "connectors" / "sheetsy.yaml"
    )

    transport = httpx.ASGITransport(app=mock_app)
    with DuckDBDestination(":memory:") as dest:
        async with httpx.AsyncClient(transport=transport) as client:
            # Slow down slightly via sleep between calls? Wait: 95 rows at limit=40 is 3 pages;
            # inject a tiny delay on client.request or run once, expire token, and run again
            first = await ConnectorEngine(
                destination=dest, client=client
            ).run(config)
            assert first.total_rows == 95
            assert first.streams["sheets"].pages_fetched == 3
            assert dest.count("sheetsy_sheets") == 95

            # Wait past TTL so server rejects old access token with 401 and forces refresh
            await asyncio.sleep(0.05)
            mock_app.state.sheets.append(
                {
                    "id": "sh_00096",
                    "title": "Sheet 96",
                    "row_count": 777,
                    "updated_at": "2026-03-01T00:00:00Z",
                }
            )
            second = await ConnectorEngine(
                destination=dest, client=client
            ).run(config)
            assert second.status == "success"
            assert dest.count("sheetsy_sheets") == 96
            assert mock_app.state.oauth_counter >= 2


@respx.mock
async def test_failed_run_does_not_advance_bookmark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a run fails on page 2, the incremental bookmark must not advance."""
    monkeypatch.setenv("PAYSTUB_API_KEY", "paystub-test-key")
    config = load_config_from_yaml(
        """
name: flaky_paystub
base_url: https://api.paystub.test
auth:
  type: api_key
  header: X-API-Key
  env_var: PAYSTUB_API_KEY
streams:
  - name: charges
    path: /v1/charges
    records_path: data
    primary_key: id
    pagination:
      type: cursor
      cursor_param: starting_after
      cursor_field: id
      page_size_param: limit
      page_size: 2
      has_more_path: has_more
    incremental:
      cursor_field: created
      request_param: created_gte
    schema:
      id: string
      amount: integer
      created: integer
"""
    )

    respx.get("https://api.paystub.test/v1/charges").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "ch_1", "amount": 10, "created": 100},
                        {"id": "ch_2", "amount": 20, "created": 200},
                    ],
                    "has_more": True,
                },
            ),
            httpx.Response(500),
            httpx.Response(500),
        ]
    )

    with DuckDBDestination(":memory:") as dest:
        async with httpx.AsyncClient() as client:
            engine = ConnectorEngine(
                destination=dest,
                client=client,
                retry_config=RetryConfig(max_retries=1, base_delay=0.0, jitter=False),
            )
            engine.state_store.set_bookmark("flaky_paystub", "charges", "created", 50)

            with pytest.raises(httpx.HTTPStatusError):
                await engine.run(config)

            # Bookmark must remain 50, NOT advance to 200
            assert (
                engine.state_store.get_bookmark("flaky_paystub", "charges") == 50
            )
            summaries = dest.fetch_run_summaries("flaky_paystub")
            assert len(summaries) == 1
            assert summaries[0]["status"] == "failed"


@respx.mock
async def test_dead_letter_and_schema_drift_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine auto-adds new fields, dead-letters invalid records, and logs drift."""
    monkeypatch.setenv("PAYSTUB_API_KEY", "paystub-test-key")
    config = load_config_from_yaml(
        """
name: drift_demo
base_url: https://api.drift.test
auth:
  type: api_key
  header: X-API-Key
  env_var: PAYSTUB_API_KEY
streams:
  - name: charges
    path: /v1/charges
    records_path: data
    primary_key: id
    pagination:
      type: cursor
      page_size: 10
      has_more_path: has_more
    schema:
      id: string
      amount: integer
      currency: string
"""
    )

    # Batch introduces 'note' (added), drops 'currency' (removed), and has one bad row
    respx.get("https://api.drift.test/v1/charges").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "ch_1", "amount": 100, "note": "vip"},
                    {"id": "ch_2", "amount": "not_an_int", "note": "bad"},
                    {"amount": 300, "note": "missing_pk"},
                ],
                "has_more": False,
            },
        )
    )

    log_stream = StringIO()
    logger = logging.getLogger("test.drift.engine")
    logger.handlers.clear()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    with DuckDBDestination(":memory:") as dest:
        async with httpx.AsyncClient() as client:
            engine = ConnectorEngine(
                destination=dest, client=client, logger=logger
            )
            result = await engine.run(config)

            assert result.status == "success"
            assert result.total_rows == 1
            assert result.total_dead_lettered == 2
            assert dest.count("drift_demo_charges") == 1
            assert "note" in dest.get_table_columns("drift_demo_charges")

            dead_letters = dest.fetch_dead_letters("drift_demo")
            assert len(dead_letters) == 2

            assert len(result.drift_events) == 1
            drift = result.drift_events[0]
            assert drift.added_fields == {"note": "string"}
            assert drift.removed_fields == ["currency"]
            assert drift.type_changes == {"amount": ("integer", "string")}

            # Structured JSON logs are valid JSON lines
            log_lines = [
                json.loads(line)
                for line in log_stream.getvalue().strip().splitlines()
            ]
            assert any(
                entry.get("event") == "schema_drift_breaking"
                for entry in log_lines
            )


async def test_engine_unauthorized_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine raises HTTPStatusError on 401 unauthorized responses."""
    monkeypatch.setenv("PAYSTUB_API_KEY", "wrong-key")
    mock_app = create_app(
        paystub_count=10,
        gitlike_count=10,
        paystub_api_key="paystub-test-key",
    )
    config = load_config(
        Path(__file__).resolve().parents[1] / "connectors" / "paystub.yaml"
    )

    transport = httpx.ASGITransport(app=mock_app)
    with DuckDBDestination(":memory:") as dest:
        async with httpx.AsyncClient(transport=transport) as client:
            engine = ConnectorEngine(destination=dest, client=client)
            with pytest.raises(httpx.HTTPStatusError):
                await engine.run(config)
