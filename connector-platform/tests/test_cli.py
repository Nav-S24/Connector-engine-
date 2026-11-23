"""Tests for the Typer CLI validate, run, and drift-check commands."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from connector import cli
from connector.destination import DuckDBDestination
from mock_server.app import create_app

runner = CliRunner()


def test_cli_validate_success_and_missing() -> None:
    """connector validate succeeds for paystub and sheetsy, and fails on unknown target."""
    res_ok = runner.invoke(cli.app, ["validate", "paystub"])
    assert res_ok.exit_code == 0
    assert "Valid config 'paystub'" in res_ok.stdout

    res_sheetsy = runner.invoke(cli.app, ["validate", "sheetsy"])
    assert res_sheetsy.exit_code == 0
    assert "auth=oauth2" in res_sheetsy.stdout

    res_bad = runner.invoke(cli.app, ["validate", "does_not_exist"])
    assert res_bad.exit_code == 1


def test_cli_run_paystub_loads_duckdb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """connector run paystub executes end-to-end and populates DuckDB."""
    monkeypatch.setenv("PAYSTUB_API_KEY", "paystub-test-key")
    mock_app = create_app(paystub_count=35, gitlike_count=10, seed=42)
    db_path = tmp_path / "test.duckdb"

    original_async_client = httpx.AsyncClient

    def _mock_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.ASGITransport(app=mock_app)
        return original_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client)

    res = runner.invoke(
        cli.app, ["run", "paystub", "--db", str(db_path)]
    )
    assert res.exit_code == 0, res.output
    assert "total_rows=35" in res.stdout

    with DuckDBDestination(db_path) as dest:
        assert dest.count("paystub_charges") == 35


def test_cli_drift_check_reports_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """connector drift-check outputs JSON drift report and exits 2 with --fail-on-drift."""
    monkeypatch.setenv("PAYSTUB_API_KEY", "paystub-test-key")
    mock_app = create_app(paystub_count=5, gitlike_count=5, seed=42)

    original_async_client = httpx.AsyncClient

    def _mock_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.ASGITransport(app=mock_app)
        return original_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client)

    clean = runner.invoke(cli.app, ["drift-check", "paystub", "--fail-on-drift"])
    assert clean.exit_code == 0, clean.output
    assert json.loads(clean.stdout)["charges"]["added_fields"] == {}

    # Introduce drift on mock server charges
    for row in mock_app.state.charges:
        row["extra_tag"] = "promo"

    drifted = runner.invoke(cli.app, ["drift-check", "paystub", "--fail-on-drift"])
    assert drifted.exit_code == 2
    assert json.loads(drifted.stdout)["charges"]["added_fields"] == {
        "extra_tag": "string"
    }
