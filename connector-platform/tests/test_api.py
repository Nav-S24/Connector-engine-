"""Tests for the FastAPI control API (src/connector/api.py) and `connector serve`."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from connector.ai.audit import AuditEntry, AuditLogger
from connector.ai.repair import compute_yaml_diff
from connector.api import create_control_app
from connector.cli import app as cli_app
from connector.engine import ConnectorEngine
from mock_server.app import create_app


def _copy_sample_configs(target_dir: Path, base_url_prefix: str = "http://mockserver") -> None:
    """Write paystub, gitlike, and sheetsy YAML configs pointing to mock_server."""
    target_dir.mkdir(parents=True, exist_ok=True)
    src_connectors = Path(__file__).resolve().parents[1] / "connectors"
    for yaml_file in src_connectors.glob("*.yaml"):
        text = yaml_file.read_text(encoding="utf-8")
        text = text.replace("http://localhost:8000", base_url_prefix)
        (target_dir / yaml_file.name).write_text(text, encoding="utf-8")


@pytest.fixture()
def control_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Isolated control API workspace with mock SaaS server, DuckDB, and audit log."""
    monkeypatch.setenv("PAYSTUB_API_KEY", "paystub-test-key")
    monkeypatch.setenv("GITLIKE_TOKEN", "gitlike-test-token")
    monkeypatch.setenv("SHEETSY_CLIENT_ID", "sheetsy-client-id")
    monkeypatch.setenv("SHEETSY_CLIENT_SECRET", "sheetsy-client-secret")
    monkeypatch.setenv("SHEETSY_REFRESH_TOKEN", "sheetsy-refresh-token")

    connectors_dir = tmp_path / "connectors"
    _copy_sample_configs(connectors_dir)

    db_path = tmp_path / "control.duckdb"
    audit_path = tmp_path / "ai_audit.jsonl"
    benchmarks_dir = tmp_path / "benchmarks"
    benchmarks_dir.mkdir(parents=True, exist_ok=True)
    repo_bench = Path(__file__).resolve().parents[1] / "benchmarks"
    shutil.copy(repo_bench / "throughput_results.csv", benchmarks_dir / "throughput_results.csv")
    shutil.copy(repo_bench / "results.md", benchmarks_dir / "results.md")

    mock_app = create_app(paystub_count=12, gitlike_count=10, sheetsy_count=8)
    transport = httpx.ASGITransport(app=mock_app)
    async_client = httpx.AsyncClient(transport=transport)

    app = create_control_app(
        db_path=db_path,
        connectors_dir=connectors_dir,
        audit_log_path=audit_path,
        benchmarks_dir=benchmarks_dir,
        http_client=async_client,
    )
    return {
        "app": app,
        "client": TestClient(app),
        "mock_app": mock_app,
        "async_client": async_client,
        "connectors_dir": connectors_dir,
        "db_path": db_path,
        "audit_path": audit_path,
    }


def test_cors_allows_localhost_5173(control_env: dict[str, Any]) -> None:
    """CORS middleware allows requests from http://localhost:5173."""
    with control_env["client"] as client:
        resp = client.options(
            "/api/connectors",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_connectors_run_runs_list_and_run_detail(control_env: dict[str, Any]) -> None:
    """GET /api/connectors, POST /api/connectors/{name}/run, GET /api/runs, GET /api/runs/{id}."""
    with control_env["client"] as client:
        initial = client.get("/api/connectors")
        assert initial.status_code == 200
        names = [item["name"] for item in initial.json()]
        assert names == ["gitlike", "paystub", "sheetsy"]
        assert all(item["last_run_status"] is None for item in initial.json())

        missing = client.post("/api/connectors/does_not_exist/run")
        assert missing.status_code == 404

        triggered = client.post("/api/connectors/paystub/run")
        assert triggered.status_code == 200
        run_id = triggered.json()["run_id"]
        assert run_id

        after = client.get("/api/connectors")
        assert after.status_code == 200
        paystub_entry = next(i for i in after.json() if i["name"] == "paystub")
        assert paystub_entry["streams"] == ["charges"]
        assert paystub_entry["last_run_status"] == "success"
        assert paystub_entry["last_rows_synced"] == 12
        assert paystub_entry["last_run_at"] is not None

        runs_resp = client.get("/api/runs", params={"connector": "paystub", "limit": 5})
        assert runs_resp.status_code == 200
        runs = runs_resp.json()
        assert len(runs) == 1
        assert runs[0]["run_id"] == run_id
        assert runs[0]["rows_synced"] == 12

        detail_resp = client.get(f"/api/runs/{run_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["status"] == "success"
        assert detail["rows_synced"] == 12
        assert detail["rows_dead_lettered"] == 0
        assert detail["retries"] == 0
        assert detail["duration_ms"] >= 0
        assert "charges" in detail["streams"]
        assert detail["streams"]["charges"]["rows_synced"] == 12

        assert client.get("/api/runs/unknown-run-id").status_code == 404


async def test_dead_letters_and_drift_endpoints(control_env: dict[str, Any]) -> None:
    """GET /api/runs/{id}/dead-letters and GET /api/drift return drift & dead-letter data."""
    mock_app = control_env["mock_app"]
    # Inject a corrupted record + drift fields into paystub charges
    mock_app.state.charges.append(
        {
            "id": "ch_bad_type",
            "amount": "not_an_integer",
            "currency": "usd",
            "created": 1_800_000_000,
            "risk_score": 0.91,
        }
    )

    with control_env["client"] as client:
        res = client.post("/api/connectors/paystub/run")
        assert res.status_code == 200
        run_id = res.json()["run_id"]

        dl_resp = client.get(f"/api/runs/{run_id}/dead-letters")
        assert dl_resp.status_code == 200
        dead_letters = dl_resp.json()
        assert len(dead_letters) == 1
        assert dead_letters[0]["stream_name"] == "charges"
        assert "amount" in dead_letters[0]["error_reason"]
        assert dead_letters[0]["raw_record"]["id"] == "ch_bad_type"

        drift_resp = client.get("/api/drift")
        assert drift_resp.status_code == 200
        events = drift_resp.json()
        assert any(
            e["connector"] == "paystub"
            and e["stream"] == "charges"
            and e["type"] == "added"
            and e["field"] == "risk_score"
            for e in events
        )
        assert any(
            e["connector"] == "paystub"
            and e["stream"] == "charges"
            and e["type"] == "retyped"
            and e["field"] == "amount"
            for e in events
        )

        assert client.get("/api/runs/missing-id/dead-letters").status_code == 404


async def test_repairs_approve_reject_and_audit_endpoints(
    control_env: dict[str, Any],
) -> None:
    """GET /api/repairs, POST approve (gated), POST reject, and GET /api/audit."""
    store = control_env["app"].state.store
    connectors_dir: Path = control_env["connectors_dir"]
    paystub_path = connectors_dir / "paystub.yaml"
    original_yaml = paystub_path.read_text(encoding="utf-8")

    # Mutate paystub mock server so amount -> amount_cents, breaking original schema
    await control_env["async_client"].post(
        "http://mockserver/admin/mutate",
        json={"service": "paystub", "rename_fields": {"amount": "amount_cents"}},
    )

    valid_repaired_yaml = original_yaml.replace("amount: integer", "amount_cents: integer")
    invalid_repaired_yaml = original_yaml.replace("records_path: data", "records_path: nonexistent")

    store.upsert_repair(
        repair_id="rep-valid",
        connector="paystub",
        trigger="Field amount renamed to amount_cents",
        diff=compute_yaml_diff(original_yaml, valid_repaired_yaml, "paystub.yaml"),
        before_yaml=original_yaml,
        proposed_yaml=valid_repaired_yaml,
        validation_results={"valid": True, "errors": []},
        status="proposed",
        attempts=1,
    )
    store.upsert_repair(
        repair_id="rep-bad-gate",
        connector="paystub",
        trigger="Stale records_path guess",
        diff=compute_yaml_diff(original_yaml, invalid_repaired_yaml, "paystub.yaml"),
        before_yaml=original_yaml,
        proposed_yaml=invalid_repaired_yaml,
        validation_results={"valid": True, "errors": []},
        status="proposed",
        attempts=2,
    )
    store.upsert_repair(
        repair_id="rep-to-reject",
        connector="gitlike",
        trigger="Optional rename proposal",
        diff="",
        before_yaml="",
        proposed_yaml="",
        validation_results={"valid": True, "errors": []},
        status="proposed",
        attempts=1,
    )

    with control_env["client"] as client:
        repairs_resp = client.get("/api/repairs")
        assert repairs_resp.status_code == 200
        repairs = {r["id"]: r for r in repairs_resp.json()}
        assert {"rep-valid", "rep-bad-gate", "rep-to-reject"} <= set(repairs)

        # Validation gate MUST refuse invalid patch on approve and leave file untouched
        refused = client.post("/api/repairs/rep-bad-gate/approve")
        assert refused.status_code == 422
        assert "amount: integer" in paystub_path.read_text(encoding="utf-8")

        # Valid patch passes re-validation gate and updates paystub.yaml
        approved = client.post("/api/repairs/rep-valid/approve")
        assert approved.status_code == 200
        assert approved.json()["status"] == "applied"
        assert "amount_cents: integer" in paystub_path.read_text(encoding="utf-8")

        # Re-approving an applied repair returns 409
        assert client.post("/api/repairs/rep-valid/approve").status_code == 409

        # Rejecting updates status to rejected without touching disk
        rejected = client.post("/api/repairs/rep-to-reject/reject")
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"

        assert client.post("/api/repairs/does-not-exist/approve").status_code == 404
        assert client.post("/api/repairs/does-not-exist/reject").status_code == 404

        audit_resp = client.get("/api/audit", params={"limit": 10})
        assert audit_resp.status_code == 200
        audit_items = audit_resp.json()
        assert len(audit_items) == 3
        reasons = [a["reason"] for a in audit_items]
        assert any("refused by validation gate" in r for r in reasons)
        assert any("approved and applied" in r for r in reasons)
        assert any("rejected via control API" in r for r in reasons)


def test_benchmarks_endpoint_and_cli_serve(
    control_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/benchmarks returns throughput + AI eval metrics, and `connector serve` invokes uvicorn."""
    with control_env["client"] as client:
        bench_resp = client.get("/api/benchmarks")
        assert bench_resp.status_code == 200
        data = bench_resp.json()
        assert data["throughput"]["baseline_rps"] == 6407.17
        assert data["throughput"]["optimized_rps"] == 26091.28
        assert data["throughput"]["speedup"] == 4.07
        assert len(data["throughput"]["rows"]) == 12
        assert data["ai_eval"]["metrics"]["total_scenarios"] == "24"
        assert len(data["ai_eval"]["scenarios"]) == 24

    captured: dict[str, Any] = {}

    def _fake_uvicorn_run(app: Any, host: str, port: int) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("connector.api.uvicorn.run", _fake_uvicorn_run)
    runner = CliRunner()
    res = runner.invoke(
        cli_app,
        [
            "serve",
            "--db",
            str(control_env["db_path"]),
            "--connectors-dir",
            str(control_env["connectors_dir"]),
            "--audit-log",
            str(control_env["audit_path"]),
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8100


async def test_cli_written_runs_and_audit_repairs_surface_in_api(
    control_env: dict[str, Any],
) -> None:
    """Runs executed via ConnectorEngine directly and audit repairs surface cleanly in control API."""
    from connector.config import load_config

    cfg = load_config(control_env["connectors_dir"] / "gitlike.yaml")
    store = control_env["app"].state.store
    with store.acquire() as dest:
        engine = ConnectorEngine(destination=dest, client=control_env["async_client"])
        run_res = await engine.run(cfg)

    auditor = AuditLogger(log_path=control_env["audit_path"])
    auditor.record(
        AuditEntry.create(
            action="repair",
            connector_name="gitlike",
            attempts=1,
            accepted=True,
            applied=False,
            reason="CLI repair check",
            diff_text="--- a/gitlike.yaml\n+++ b/gitlike.yaml\n",
            before_yaml=(control_env["connectors_dir"] / "gitlike.yaml").read_text(encoding="utf-8"),
            after_yaml=(control_env["connectors_dir"] / "gitlike.yaml").read_text(encoding="utf-8"),
        )
    )

    with control_env["client"] as client:
        detail = client.get(f"/api/runs/{run_res.run_id}")
        assert detail.status_code == 200
        assert detail.json()["rows_synced"] == 10
        assert "repos" in detail.json()["streams"]

        repairs = client.get("/api/repairs").json()
        assert any(r["connector"] == "gitlike" and r["status"] == "proposed" for r in repairs)
