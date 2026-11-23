"""Tests for Phase 5 mock mutations, AI audit log, repair loop, CLI, and evaluation."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from benchmarks.eval_ai_layer import evaluate_scenarios
from connector.ai.audit import AuditEntry, AuditLogger
from connector.ai.generator import FakeLLMClient
from connector.ai.repair import compute_yaml_diff, repair_connector_config
from connector.cli import app as cli_app
from connector.destination import DuckDBDestination
from connector.engine import ConnectorEngine
from mock_server.app import create_app


async def test_mock_server_admin_mutate_and_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/admin/mutate transforms response fields/envelopes/params and resets cleanly."""
    monkeypatch.setenv("PAYSTUB_API_KEY", "paystub-test-key")
    mock_app = create_app(paystub_count=12, gitlike_count=10, sheetsy_count=8)
    transport = httpx.ASGITransport(app=mock_app)

    async with httpx.AsyncClient(transport=transport) as client:
        mut_resp = await client.post(
            "http://mockserver/admin/mutate",
            json={
                "service": "paystub",
                "rename_fields": {"amount": "amount_cents"},
                "add_fields": {"fee_cents": 25},
                "remove_fields": ["currency"],
                "records_path": "payload.charges",
                "cursor_param": "after",
                "page_size_param": "per_page",
            },
        )
        assert mut_resp.status_code == 200
        assert mut_resp.json()["active"] is True

        r1 = await client.get(
            "http://mockserver/paystub/v1/charges",
            headers={"X-API-Key": "paystub-test-key"},
            params={"per_page": 5},
        )
        body = r1.json()
        assert "payload" in body
        items = body["payload"]["charges"]
        assert len(items) == 5
        assert "amount_cents" in items[0]
        assert "fee_cents" in items[0]
        assert "currency" not in items[0]

        reset_resp = await client.post(
            "http://mockserver/admin/mutate",
            json={"service": "paystub", "reset": True},
        )
        assert reset_resp.status_code == 200
        assert reset_resp.json()["active"] is False

        r2 = await client.get(
            "http://mockserver/paystub/v1/charges",
            headers={"X-API-Key": "paystub-test-key"},
            params={"limit": 3},
        )
        assert len(r2.json()["data"]) == 3


def test_audit_logger_jsonl_and_duckdb(tmp_path: Path) -> None:
    """AuditLogger writes entries to both JSONL and DuckDB _ai_audit table."""
    audit_file = tmp_path / "ai_audit.jsonl"
    with DuckDBDestination(":memory:") as dest:
        logger = AuditLogger(log_path=audit_file, duckdb_conn=dest.conn)
        e1 = AuditEntry.create(
            action="generate",
            connector_name="paystub",
            attempts=1,
            accepted=True,
            applied=False,
            reason="Generated from spec",
            after_yaml="name: paystub",
        )
        e2 = AuditEntry.create(
            action="repair",
            connector_name="gitlike",
            attempts=2,
            accepted=False,
            applied=False,
            reason="Drift detected",
            validation_errors=["Schema error at 'streams.0.primary_key'"],
        )
        logger.record(e1)
        logger.record(e2)

        all_entries = logger.read_all()
        assert len(all_entries) == 2
        assert len(logger.read_all(connector_name="paystub")) == 1
        stats = logger.summary()
        assert stats["total"] == 2
        assert stats["accepted"] == 1
        assert stats["rejected"] == 1
        assert stats["by_action"] == {"generate": 1, "repair": 1}

        rows = dest.conn.execute("SELECT COUNT(*) FROM _ai_audit").fetchone()
        assert rows is not None and rows[0] == 2


async def test_repair_loop_heals_mutated_api_with_gate_and_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Self-healing repair inspects live drift, retries rejected YAML, diffs, audits, and syncs."""
    monkeypatch.setenv("PAYSTUB_API_KEY", "paystub-test-key")
    mock_app = create_app(paystub_count=30)
    transport = httpx.ASGITransport(app=mock_app)

    original_yaml = """
name: paystub
base_url: http://mockserver/paystub
auth:
  type: api_key
  header: X-API-Key
  env_var: PAYSTUB_API_KEY
streams:
  - name: charges
    path: /v1/charges
    method: GET
    records_path: data
    primary_key: id
    pagination:
      type: cursor
      cursor_param: starting_after
      cursor_field: id
      page_size_param: limit
      page_size: 15
      has_more_path: has_more
    schema:
      id: string
      amount: integer
      currency: string
      created: integer
"""

    # Attempt 1 still uses old records_path -> must be rejected by validation gate
    bad_repair = original_yaml.replace("amount: integer", "amount_cents: integer")
    # Attempt 2 updates records_path + renames amount -> amount_cents -> passes gate
    good_repair = """
name: paystub
base_url: http://mockserver/paystub
auth:
  type: api_key
  header: X-API-Key
  env_var: PAYSTUB_API_KEY
streams:
  - name: charges
    path: /v1/charges
    method: GET
    records_path: payload.charges
    primary_key: id
    pagination:
      type: cursor
      cursor_param: starting_after
      cursor_field: id
      page_size_param: limit
      page_size: 15
      has_more_path: has_more
    schema:
      id: string
      amount_cents: integer
      currency: string
      created: integer
"""

    config_file = tmp_path / "paystub.yaml"
    config_file.write_text(original_yaml.strip() + "\n", encoding="utf-8")
    auditor = AuditLogger(log_path=tmp_path / "audit.jsonl")
    fake_llm = FakeLLMClient([bad_repair, good_repair])

    async with httpx.AsyncClient(transport=transport) as client:
        await client.post(
            "http://mockserver/admin/mutate",
            json={
                "service": "paystub",
                "rename_fields": {"amount": "amount_cents"},
                "records_path": "payload.charges",
            },
        )

        # Dry-run proposal does not overwrite config_file
        res_proposed = await repair_connector_config(
            config_yaml=original_yaml,
            llm_client=fake_llm,
            probe_endpoint=True,
            max_retries=3,
            auto_apply=False,
            output_path=config_file,
            http_client=client,
            audit_logger=auditor,
        )
        assert res_proposed.repaired is True
        assert res_proposed.applied is False
        assert res_proposed.attempts == 2
        assert "payload.charges" in res_proposed.diff
        assert "records_path: data" in config_file.read_text(encoding="utf-8")
        assert "payload" in str(fake_llm.calls[0]["prompt"])
        assert "records_path" in str(fake_llm.calls[1]["prompt"])

        # Auto-apply persists validated repair and allows full sync
        fake_llm_2 = FakeLLMClient([good_repair])
        res_applied = await repair_connector_config(
            config_yaml=original_yaml,
            llm_client=fake_llm_2,
            probe_endpoint=True,
            max_retries=2,
            auto_apply=True,
            output_path=config_file,
            http_client=client,
            audit_logger=auditor,
        )
        assert res_applied.repaired is True
        assert res_applied.applied is True
        assert "payload.charges" in config_file.read_text(encoding="utf-8")
        assert len(auditor.read_all()) == 2

        with DuckDBDestination(":memory:") as dest:
            engine = ConnectorEngine(destination=dest, client=client)
            sync_res = await engine.run(res_applied.config)  # type: ignore[arg-type]
            assert sync_res.total_rows == 30
            assert dest.count("paystub_charges") == 30


def test_cli_repair_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """connector repair validates repaired YAML, prints diff, and updates file with --auto-apply."""
    cfg_file = tmp_path / "gitlike.yaml"
    cfg_file.write_text(
        """
name: gitlike
base_url: http://localhost:8000/gitlike
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
    schema:
      id: integer
      stars: integer
""",
        encoding="utf-8",
    )
    repaired_yaml = cfg_file.read_text(encoding="utf-8").replace(
        "stars: integer", "stargazers_count: integer"
    )

    monkeypatch.setattr(
        "connector.cli._create_llm_client",
        lambda: FakeLLMClient([repaired_yaml]),
    )
    runner = CliRunner()
    audit_path = tmp_path / "audit.jsonl"

    res = runner.invoke(
        cli_app,
        [
            "repair",
            str(cfg_file),
            "--no-probe",
            "--auto-apply",
            "--audit-log",
            str(audit_path),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "stargazers_count" in cfg_file.read_text(encoding="utf-8")
    assert audit_path.is_file()
    assert compute_yaml_diff("a: 1", "a: 1") == ""


async def test_eval_ai_layer_24_scenarios(tmp_path: Path) -> None:
    """All 24 generation and drift-repair scenarios pass and write results.md."""
    results_file = tmp_path / "results.md"
    summary = await evaluate_scenarios(
        scenarios_path=Path("/app/applet/connector-platform/benchmarks/scenarios/scenarios.yaml"),
        results_path=results_file,
        live_llm=False,
    )
    assert summary["total_scenarios"] == 24
    assert summary["all_passed"] is True
    assert summary["gate_catch_rate"] == 100.0
    assert summary["repair_success_rate"] == 100.0
    assert results_file.is_file()
    assert "AI Layer Evaluation Results" in results_file.read_text(encoding="utf-8")
