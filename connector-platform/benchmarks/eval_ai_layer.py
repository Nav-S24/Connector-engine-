"""Evaluation harness for the AI connector generation, repair, and validation gate."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from connector.ai.audit import AuditLogger
from connector.ai.generator import (
    FakeLLMClient,
    GeminiLLMClient,
    LLMClient,
    generate_connector_config,
)
from connector.ai.repair import repair_connector_config
from connector.destination import DuckDBDestination
from connector.drift import infer_field_type
from connector.engine import ConnectorEngine, resolve_table_name
from mock_server.app import create_app


@dataclass
class ScenarioOutcome:
    """Measured outcome of evaluating one AI generation or repair scenario."""

    scenario_id: str
    kind: str
    service: str
    description: str
    expected_accepted: bool
    accepted: bool
    gate_correct: bool
    attempts: int
    post_repair_rows: int
    expected_records: int
    latency_ms: float
    errors: list[str]


_BASE_CONFIGS: dict[str, dict[str, Any]] = {
    "paystub": {
        "name": "paystub",
        "base_url": "http://mockserver/paystub",
        "rate_limit": 50,
        "max_concurrency": 4,
        "auth": {
            "type": "api_key",
            "header": "X-API-Key",
            "env_var": "PAYSTUB_API_KEY",
        },
        "streams": [
            {
                "name": "charges",
                "path": "/v1/charges",
                "method": "GET",
                "records_path": "data",
                "primary_key": "id",
                "pagination": {
                    "type": "cursor",
                    "cursor_param": "starting_after",
                    "cursor_field": "id",
                    "page_size_param": "limit",
                    "page_size": 15,
                    "has_more_path": "has_more",
                },
                "incremental": {
                    "cursor_field": "created",
                    "request_param": "created_gte",
                },
                "schema": {
                    "id": "string",
                    "amount": "integer",
                    "currency": "string",
                    "created": "integer",
                },
            }
        ],
    },
    "gitlike": {
        "name": "gitlike",
        "base_url": "http://mockserver/gitlike",
        "rate_limit": 50,
        "max_concurrency": 4,
        "auth": {"type": "bearer", "env_var": "GITLIKE_TOKEN"},
        "streams": [
            {
                "name": "repos",
                "path": "/v1/repos",
                "method": "GET",
                "records_path": "result.items",
                "primary_key": "id",
                "pagination": {
                    "type": "offset",
                    "offset_param": "offset",
                    "limit_param": "limit",
                    "page_size": 15,
                    "total_path": "meta.total",
                },
                "schema": {
                    "id": "integer",
                    "name": "string",
                    "stars": "integer",
                    "private": "boolean",
                    "updated_at": "string",
                },
            }
        ],
    },
    "sheetsy": {
        "name": "sheetsy",
        "base_url": "http://mockserver/sheetsy",
        "rate_limit": 50,
        "max_concurrency": 4,
        "auth": {
            "type": "oauth2",
            "token_url": "http://mockserver/sheetsy/oauth/token",
            "client_id_env_var": "SHEETSY_CLIENT_ID",
            "client_secret_env_var": "SHEETSY_CLIENT_SECRET",
            "refresh_token_env_var": "SHEETSY_REFRESH_TOKEN",
        },
        "streams": [
            {
                "name": "sheets",
                "path": "/v1/sheets",
                "method": "GET",
                "records_path": "rows",
                "primary_key": "id",
                "pagination": {
                    "type": "link_header",
                    "page_size_param": "limit",
                    "page_size": 15,
                },
                "incremental": {
                    "cursor_field": "updated_at",
                    "request_param": "updated_after",
                },
                "schema": {
                    "id": "string",
                    "title": "string",
                    "row_count": "integer",
                    "updated_at": "string",
                },
            }
        ],
    },
}


def _find_records_path_and_list(
    node: Any, prefix: str = ""
) -> tuple[str | None, list[dict[str, Any]]]:
    """Locate the list of record dicts inside a live JSON payload."""
    if isinstance(node, list) and (not node or isinstance(node[0], dict)):
        return prefix, [item for item in node if isinstance(item, dict)]
    if isinstance(node, dict):
        for key, val in node.items():
            sub_path = f"{prefix}.{key}" if prefix else key
            found_path, found_list = _find_records_path_and_list(val, sub_path)
            if found_path is not None and (found_list or isinstance(val, list)):
                return found_path, found_list
    return None, []


def _build_spec_text(service: str, spec_type: str) -> str:
    """Build an OpenAPI or Markdown API specification string for generation scenarios."""
    if spec_type == "openapi":
        cfg = _BASE_CONFIGS[service]
        stream = cfg["streams"][0]
        props = {
            col: {"type": "boolean" if ftype == "boolean" else ("integer" if ftype == "integer" else "string")}
            for col, ftype in stream["schema"].items()
        }
        paths: dict[str, Any] = {
            f"/v1/unrelated_{i}": {
                "get": {
                    "operationId": f"listUnrelated{i}",
                    "summary": f"Unrelated filler endpoint {i} " + ("padding " * 12),
                }
            }
            for i in range(30)
        }
        paths[stream["path"]] = {
            "get": {
                "operationId": f"list_{stream['name']}",
                "summary": f"List {service} {stream['name']} records",
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "records_path": stream["records_path"],
                                        "items": {"type": "object", "properties": props},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        }
        return json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": f"{service} API", "version": "1.0.0"},
                "servers": [{"url": cfg["base_url"]}],
                "paths": paths,
            },
            indent=2,
        )

    cfg = _BASE_CONFIGS[service]
    stream = cfg["streams"][0]
    return (
        f"# {service.title()} REST API\n"
        f"Base URL: {cfg['base_url']}\n"
        f"Auth: {yaml.safe_dump(cfg['auth'], sort_keys=False)}\n"
        f"## GET {stream['path']}\n"
        f"Records live at `{stream['records_path']}`. Primary key is `{stream['primary_key']}`.\n"
        f"Pagination: {yaml.safe_dump(stream['pagination'], sort_keys=False)}\n"
        f"Schema: {yaml.safe_dump(stream['schema'], sort_keys=False)}\n"
    )


def _derive_repaired_config_from_mutation(
    service: str, mutation: dict[str, Any]
) -> dict[str, Any]:
    """Compute the ground-truth repaired config matching a server mutation."""
    cfg = copy.deepcopy(_BASE_CONFIGS[service])
    stream = cfg["streams"][0]
    schema = dict(stream["schema"])

    for rem in mutation.get("remove_fields", []):
        schema.pop(rem, None)
    for old_f, new_f in mutation.get("rename_fields", {}).items():
        if old_f in schema:
            schema[new_f] = schema.pop(old_f)
    for field_name, target_type in mutation.get("retype_fields", {}).items():
        if field_name in schema:
            schema[field_name] = target_type
    for add_name, add_val in mutation.get("add_fields", {}).items():
        schema[add_name] = infer_field_type(add_val) or "string"

    stream["schema"] = schema
    if mutation.get("records_path"):
        stream["records_path"] = mutation["records_path"]
    if mutation.get("cursor_param") and stream["pagination"]["type"] == "cursor":
        stream["pagination"]["cursor_param"] = mutation["cursor_param"]
    if mutation.get("offset_param") and stream["pagination"]["type"] == "offset":
        stream["pagination"]["offset_param"] = mutation["offset_param"]
    if mutation.get("page_size_param"):
        if stream["pagination"]["type"] in ("cursor", "link_header"):
            stream["pagination"]["page_size_param"] = mutation["page_size_param"]
        elif stream["pagination"]["type"] == "offset":
            stream["pagination"]["limit_param"] = mutation["page_size_param"]

    return cfg


def _build_eval_llm(scenario: dict[str, Any]) -> LLMClient:
    """Create a realistic simulated LLM client (or GeminiLLMClient if configured)."""
    service = scenario["service"]
    kind = scenario["kind"]

    if scenario.get("force_invalid"):
        bad = copy.deepcopy(_BASE_CONFIGS[service])
        bad["auth"] = {"type": "bearer", "env_var": "NONEXISTENT_EVAL_SECRET"}
        bad_yaml = yaml.safe_dump(bad, sort_keys=False)
        return FakeLLMClient([bad_yaml, bad_yaml, bad_yaml, bad_yaml])

    if kind == "generate":
        target_yaml = yaml.safe_dump(_BASE_CONFIGS[service], sort_keys=False)
        if scenario.get("simulate_attempt1_error"):
            wrong = copy.deepcopy(_BASE_CONFIGS[service])
            wrong["streams"][0]["records_path"] = "wrong_path"
            return FakeLLMClient(
                [yaml.safe_dump(wrong, sort_keys=False), target_yaml]
            )
        return FakeLLMClient([target_yaml])

    mutation = scenario.get("mutation", {})
    repaired_dict = _derive_repaired_config_from_mutation(service, mutation)
    repaired_yaml = yaml.safe_dump(repaired_dict, sort_keys=False)

    if scenario.get("simulate_attempt1_error"):
        partial = copy.deepcopy(_BASE_CONFIGS[service])
        partial_yaml = yaml.safe_dump(partial, sort_keys=False)
        return FakeLLMClient([partial_yaml, repaired_yaml])

    return FakeLLMClient([repaired_yaml])


async def evaluate_scenarios(
    scenarios_path: Path = Path("benchmarks/scenarios/scenarios.yaml"),
    results_path: Path = Path("benchmarks/results.md"),
    live_llm: bool = False,
) -> dict[str, Any]:
    """Run all 24 AI evaluation scenarios against the mock server and save results.md."""
    os.environ["PAYSTUB_API_KEY"] = "paystub-test-key"
    os.environ["GITLIKE_TOKEN"] = "gitlike-test-token"
    os.environ["SHEETSY_CLIENT_ID"] = "sheetsy-client-id"
    os.environ["SHEETSY_CLIENT_SECRET"] = "sheetsy-client-secret"
    os.environ["SHEETSY_REFRESH_TOKEN"] = "sheetsy-refresh-token"

    raw_doc = yaml.safe_load(scenarios_path.read_text(encoding="utf-8"))
    scenarios: list[dict[str, Any]] = raw_doc["scenarios"]

    mock_app = create_app(paystub_count=40, gitlike_count=35, sheetsy_count=30, seed=42)
    transport = httpx.ASGITransport(app=mock_app)
    auditor = AuditLogger(log_path=None)
    outcomes: list[ScenarioOutcome] = []

    async with httpx.AsyncClient(transport=transport) as client:
        for sc in scenarios:
            for svc in ("paystub", "gitlike", "sheetsy"):
                await client.post(
                    "http://mockserver/admin/mutate",
                    json={"service": svc, "reset": True},
                )

            llm: LLMClient = (
                GeminiLLMClient() if live_llm else _build_eval_llm(sc)
            )
            t0 = time.perf_counter()
            post_rows = 0
            errors: list[str] = []
            accepted = False
            attempts = 0

            if sc["kind"] == "generate":
                spec_text = _build_spec_text(sc["service"], sc.get("spec_type", "markdown"))
                gen_res = await generate_connector_config(
                    spec_text=spec_text,
                    llm_client=llm,
                    connector_name=sc["connector_name"],
                    base_url=sc["base_url"],
                    stream_hints=sc.get("stream_hints"),
                    max_retries=3,
                    probe_endpoint=True,
                    http_client=client,
                    audit_logger=auditor,
                )
                accepted = gen_res.validation.valid
                attempts = gen_res.attempts
                errors = list(gen_res.validation.errors)
                if accepted and gen_res.config is not None:
                    with DuckDBDestination(":memory:") as dest:
                        eng = ConnectorEngine(destination=dest, client=client)
                        run_res = await eng.run(gen_res.config)
                        table = resolve_table_name(
                            gen_res.config.name, gen_res.config.streams[0].name
                        )
                        post_rows = dest.count(table) if run_res.status == "success" else 0
            else:
                await client.post(
                    "http://mockserver/admin/mutate",
                    json={"service": sc["service"], **sc.get("mutation", {})},
                )
                orig_yaml = yaml.safe_dump(
                    _BASE_CONFIGS[sc["service"]], sort_keys=False
                )
                rep_res = await repair_connector_config(
                    config_yaml=orig_yaml,
                    llm_client=llm,
                    error_message=sc.get("description"),
                    probe_endpoint=True,
                    max_retries=3,
                    http_client=client,
                    audit_logger=auditor,
                )
                accepted = rep_res.repaired
                attempts = rep_res.attempts
                errors = list(rep_res.validation.errors)
                if accepted and rep_res.config is not None:
                    with DuckDBDestination(":memory:") as dest:
                        eng = ConnectorEngine(destination=dest, client=client)
                        run_res = await eng.run(rep_res.config)
                        table = resolve_table_name(
                            rep_res.config.name, rep_res.config.streams[0].name
                        )
                        post_rows = dest.count(table) if run_res.status == "success" else 0

            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            expected_acc = bool(sc["expected_accepted"])
            gate_correct = accepted == expected_acc
            if expected_acc and accepted and sc["expected_records"] > 0:
                gate_correct = gate_correct and (post_rows == sc["expected_records"])

            outcomes.append(
                ScenarioOutcome(
                    scenario_id=sc["id"],
                    kind=sc["kind"],
                    service=sc["service"],
                    description=sc.get("description", sc.get("spec_type", "")),
                    expected_accepted=expected_acc,
                    accepted=accepted,
                    gate_correct=gate_correct,
                    attempts=attempts,
                    post_repair_rows=post_rows,
                    expected_records=sc["expected_records"],
                    latency_ms=elapsed_ms,
                    errors=errors,
                )
            )

    gen_pos = [o for o in outcomes if o.kind == "generate" and o.expected_accepted]
    rep_pos = [o for o in outcomes if o.kind == "repair" and o.expected_accepted]
    adversarial = [o for o in outcomes if not o.expected_accepted]

    gen_pass1 = sum(1 for o in gen_pos if o.accepted and o.attempts == 1)
    gen_pass3 = sum(1 for o in gen_pos if o.accepted and o.attempts <= 4)
    rep_pass = sum(1 for o in rep_pos if o.accepted and o.gate_correct)
    gate_caught = sum(1 for o in adversarial if not o.accepted)

    summary = {
        "total_scenarios": len(outcomes),
        "generation_total": len(gen_pos),
        "generation_pass_at_1": round(gen_pass1 / max(1, len(gen_pos)) * 100, 1),
        "generation_pass_at_3": round(gen_pass3 / max(1, len(gen_pos)) * 100, 1),
        "repair_total": len(rep_pos),
        "repair_success_rate": round(rep_pass / max(1, len(rep_pos)) * 100, 1),
        "adversarial_total": len(adversarial),
        "gate_catch_rate": round(gate_caught / max(1, len(adversarial)) * 100, 1),
        "mean_attempts": round(
            sum(o.attempts for o in outcomes) / max(1, len(outcomes)), 2
        ),
        "mean_latency_ms": round(
            sum(o.latency_ms for o in outcomes) / max(1, len(outcomes)), 2
        ),
        "all_passed": all(o.gate_correct for o in outcomes),
        "outcomes": outcomes,
        "audit_summary": auditor.summary(),
    }

    _write_results_md(summary, results_path)
    return summary


def _write_results_md(summary: dict[str, Any], results_path: Path) -> None:
    """Write formatted Markdown evaluation report to benchmarks/results.md."""
    results_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# AI Layer Evaluation Results",
        "",
        "## Summary Metrics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Total Scenarios | {summary['total_scenarios']} |",
        f"| Generation First-Attempt Pass Rate | {summary['generation_pass_at_1']}% ({summary['generation_total']} positive specs) |",
        f"| Generation Pass@3 Rate | {summary['generation_pass_at_3']}% |",
        f"| Repair Success Rate (within 3 retries + full sync) | {summary['repair_success_rate']}% ({summary['repair_total']} drift scenarios) |",
        f"| Validation Gate Catch Rate (adversarial/broken configs) | {summary['gate_catch_rate']}% ({summary['adversarial_total']} cases) |",
        f"| Mean Attempts per Scenario | {summary['mean_attempts']} |",
        f"| Mean Latency per Scenario | {summary['mean_latency_ms']} ms |",
        "",
        "## Scenario Breakdown",
        "",
        "| ID | Kind | Service | Description | Expected | Accepted | Attempts | Synced Rows | Latency (ms) | Status |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for o in summary["outcomes"]:
        status = "PASS" if o.gate_correct else "FAIL"
        lines.append(
            f"| `{o.scenario_id}` | {o.kind} | {o.service} | {o.description} | "
            f"{o.expected_accepted} | {o.accepted} | {o.attempts} | "
            f"{o.post_repair_rows}/{o.expected_records} | {o.latency_ms:.2f} | {status} |"
        )
    lines.append("")
    results_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """CLI entrypoint for running AI layer evaluation scenarios."""
    parser = argparse.ArgumentParser(description="Evaluate AI generation and repair layer.")
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=Path("benchmarks/scenarios/scenarios.yaml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/results.md"),
    )
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Use GeminiLLMClient (requires GEMINI_API_KEY) instead of simulated evaluator.",
    )
    args = parser.parse_args()

    summary = asyncio.run(
        evaluate_scenarios(
            scenarios_path=args.scenarios,
            results_path=args.output,
            live_llm=args.live_llm,
        )
    )
    print(
        f"Evaluated {summary['total_scenarios']} scenarios: "
        f"gen_pass@1={summary['generation_pass_at_1']}%, "
        f"gen_pass@3={summary['generation_pass_at_3']}%, "
        f"repair_success={summary['repair_success_rate']}%, "
        f"gate_catch={summary['gate_catch_rate']}%, "
        f"mean_latency={summary['mean_latency_ms']}ms -> {args.output}"
    )


if __name__ == "__main__":
    main()
