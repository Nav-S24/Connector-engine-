"""Tests for Phase 3 concurrency, batching, fault injection, and benchmarks."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from benchmarks.bench_throughput import run_benchmark
from connector.config import load_config_from_yaml
from connector.destination import DuckDBDestination
from connector.engine import ConnectorEngine
from connector.retry import RetryConfig
from mock_server.app import create_app
from mock_server.faults import FaultConfig


async def test_concurrent_streams_and_offset_without_total_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine runs multiple streams concurrently and handles offset without total_path."""
    monkeypatch.setenv("GITLIKE_TOKEN", "gitlike-test-token")
    mock_app = create_app(
        paystub_count=10,
        gitlike_count=135,
        fault_config=FaultConfig(latency_ms=10.0),
    )
    config = load_config_from_yaml(
        """
name: multi_gitlike
base_url: http://mockserver/gitlike
max_concurrency: 4
auth:
  type: bearer
  env_var: GITLIKE_TOKEN
streams:
  - name: repos_with_total
    path: /v1/repos
    records_path: result.items
    primary_key: id
    pagination:
      type: offset
      page_size: 25
      total_path: meta.total
    schema:
      id: integer
      name: string
      stars: integer
      private: boolean
      updated_at: string
  - name: repos_windowed
    path: /v1/repos
    records_path: result.items
    primary_key: id
    pagination:
      type: offset
      page_size: 30
    schema:
      id: integer
      name: string
      stars: integer
      private: boolean
      updated_at: string
"""
    )

    transport = httpx.ASGITransport(app=mock_app)
    with DuckDBDestination(":memory:") as dest:
        async with httpx.AsyncClient(transport=transport) as client:
            engine = ConnectorEngine(
                destination=dest,
                client=client,
                max_concurrency=4,
                batch_size=50,
            )
            result = await engine.run(config)

            assert result.status == "success"
            assert dest.count("multi_gitlike_repos_with_total") == 135
            assert dest.count("multi_gitlike_repos_windowed") == 135
            assert result.streams["repos_with_total"].rows_synced == 135
            assert result.streams["repos_windowed"].rows_synced == 135


async def test_fault_injection_zero_data_loss_and_admin_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine recovers from injected 429s and 500s with zero data loss or duplicates."""
    monkeypatch.setenv("GITLIKE_TOKEN", "gitlike-test-token")
    mock_app = create_app(
        gitlike_count=300,
        seed=42,
        fault_config=FaultConfig(
            latency_ms=2.0,
            rate_429=0.15,
            rate_500=0.10,
            retry_after="0.005",
        ),
    )
    config = load_config_from_yaml(
        """
name: fault_gitlike
base_url: http://mockserver/gitlike
max_concurrency: 6
auth:
  type: bearer
  env_var: GITLIKE_TOKEN
streams:
  - name: repos
    path: /v1/repos
    records_path: result.items
    primary_key: id
    pagination:
      type: offset
      page_size: 20
      total_path: meta.total
    schema:
      id: integer
      name: string
      stars: integer
      private: boolean
      updated_at: string
"""
    )

    transport = httpx.ASGITransport(app=mock_app)
    with DuckDBDestination(":memory:") as dest:
        async with httpx.AsyncClient(transport=transport) as client:
            # Verify /admin/faults endpoint updates configuration cleanly
            admin_resp = await client.post(
                "http://mockserver/admin/faults",
                json={"rate_429": 0.12, "rate_500": 0.08, "seed": 99},
            )
            assert admin_resp.status_code == 200
            assert admin_resp.json()["rate_429"] == 0.12

            engine = ConnectorEngine(
                destination=dest,
                client=client,
                retry_config=RetryConfig(
                    max_retries=8, base_delay=0.005, max_delay=0.05, jitter=False
                ),
                batch_size=75,
            )
            result = await engine.run(config)
            assert result.status == "success"
            assert result.total_retries > 0
            assert dest.count("fault_gitlike_repos") == 300


async def test_benchmark_harness_smoke(tmp_path: Path) -> None:
    """run_benchmark produces CSV, PNG charts, and measurable speedup."""
    summary = await run_benchmark(
        record_count=600,
        page_size=100,
        latency_ms=15.0,
        concurrency_levels=(1, 2, 4),
        batch_sizes=(100, 500),
        output_dir=tmp_path,
    )
    assert summary["fault_loaded"] == 600
    assert summary["speedup"] > 1.5
    assert Path(summary["csv_path"]).is_file()
    assert Path(summary["concurrency_chart"]).is_file()
    assert Path(summary["batch_chart"]).is_file()
