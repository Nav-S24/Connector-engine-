"""Validation-gated LLM drift repair loop for broken or drifted connectors."""

from __future__ import annotations

import copy
import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from connector.ai.audit import AuditEntry, AuditLogger
from connector.ai.generator import (
    LLMClient,
    SYSTEM_INSTRUCTION,
    extract_yaml_from_response,
)
from connector.ai.validator import (
    ValidationResult,
    validate_connector_config,
    validate_yaml_schema,
)
from connector.auth import AuthError, build_auth
from connector.config import ConnectorConfig, extract_nested
from connector.drift import DriftReport, detect_drift
from connector.pagination import build_pagination
from connector.retry import RetryConfig, request_with_retry


@dataclass
class RepairResult:
    """Outcome of a validation-gated connector repair attempt."""

    repaired: bool
    applied: bool
    original_yaml: str
    repaired_yaml: str
    config: ConnectorConfig | None
    validation: ValidationResult
    diff: str
    attempts: int
    drift_reports: dict[str, DriftReport] = field(default_factory=dict)
    sample_payloads: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    audit_entry: AuditEntry | None = None


def compute_yaml_diff(
    before_yaml: str, after_yaml: str, label: str = "connector.yaml"
) -> str:
    """Return a unified diff comparing before_yaml and after_yaml."""
    before_lines = before_yaml.strip().splitlines(keepends=True)
    after_lines = after_yaml.strip().splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
        )
    )


def _truncate_payload(payload: Any, max_list_items: int = 2) -> Any:
    """Trim lists inside sample JSON payloads so prompts stay concise and clear."""
    if isinstance(payload, list):
        return [_truncate_payload(item, max_list_items) for item in payload[:max_list_items]]
    if isinstance(payload, dict):
        return {
            key: _truncate_payload(val, max_list_items)
            for key, val in payload.items()
        }
    return payload


async def inspect_live_drift_context(
    config: ConnectorConfig,
    stream_names: list[str] | None = None,
    client: httpx.AsyncClient | None = None,
    timeout: float = 15.0,
) -> tuple[dict[str, DriftReport], dict[str, Any], list[str]]:
    """Probe the live API to capture current payload structure and drift reports."""
    wanted = set(stream_names) if stream_names else None
    streams = (
        [s for s in config.streams if s.name in wanted]
        if wanted
        else config.streams
    )

    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=timeout)
    retry_cfg = RetryConfig(max_retries=2, base_delay=0.05, max_delay=0.25, jitter=False)

    drift_reports: dict[str, DriftReport] = {}
    sample_payloads: dict[str, Any] = {}
    probe_errors: list[str] = []

    try:
        auth = build_auth(config.auth, client=active_client)
        headers = await auth.get_headers()
    except (AuthError, Exception) as exc:
        if owns_client:
            await active_client.aclose()
        return drift_reports, sample_payloads, [f"Auth failed during inspection: {exc}"]

    try:
        for stream in streams:
            pagination = build_pagination(stream.pagination)
            url = f"{config.base_url.rstrip('/')}/{stream.path.lstrip('/')}"
            params = {**stream.params, **pagination.initial_params()}
            try:
                response = await request_with_retry(
                    client=active_client,
                    method=stream.method,
                    url=url,
                    headers=headers,
                    params=params,
                    retry_config=retry_cfg,
                    on_unauthorized=auth.refresh_on_unauthorized,
                )
                payload = response.json()
            except Exception as exc:
                probe_errors.append(f"Stream '{stream.name}' probe failed: {exc}")
                continue

            truncated = _truncate_payload(copy.deepcopy(payload), max_list_items=2)
            sample_info: dict[str, Any] = {"payload": truncated}
            if "Link" in response.headers:
                sample_info["link_header"] = response.headers["Link"]
            sample_payloads[stream.name] = sample_info

            raw_records = extract_nested(payload, stream.records_path)
            if isinstance(raw_records, list):
                dict_records = [r for r in raw_records if isinstance(r, dict)]
                report = detect_drift(
                    stream_name=stream.name,
                    expected_schema=dict(stream.schema_),
                    records=dict_records,
                )
                drift_reports[stream.name] = report
                if report.has_drift:
                    probe_errors.append(
                        f"Stream '{stream.name}' drift detected: {report.to_dict()}"
                    )
            else:
                probe_errors.append(
                    f"Stream '{stream.name}': records_path '{stream.records_path}' "
                    f"did not resolve to a list; inspect sample payload structure."
                )
    finally:
        if owns_client:
            await active_client.aclose()

    return drift_reports, sample_payloads, probe_errors


def _build_repair_initial_prompt(
    original_yaml: str,
    error_context: str,
    drift_reports: dict[str, DriftReport],
    sample_payloads: dict[str, Any],
) -> str:
    """Construct the initial repair prompt with config, drift, and live sample JSON."""
    parts = [
        "Repair the declarative connector YAML config below so it works with the updated source API.",
        "Preserve `name`, `base_url`, and environment variable names (`env_var` / `*_env_var`).",
        "Output ONLY corrected YAML.",
        "",
        "Current Connector YAML:",
        f"```yaml\n{original_yaml.strip()}\n```",
    ]
    if error_context.strip():
        parts.extend(["", "Observed Errors / Failure Context:", error_context.strip()])

    if drift_reports:
        drift_dict = {name: rep.to_dict() for name, rep in drift_reports.items()}
        parts.extend(
            ["", "Detected Schema Drift:", json.dumps(drift_dict, indent=2)]
        )

    if sample_payloads:
        parts.extend(
            [
                "",
                "Live Sample API Response(s):",
                json.dumps(sample_payloads, indent=2, default=str)[:8000],
            ]
        )

    return "\n".join(parts)


async def repair_connector_config(
    config_yaml: str,
    llm_client: LLMClient,
    error_message: str | None = None,
    stream_names: list[str] | None = None,
    drift_reports: dict[str, DriftReport] | None = None,
    sample_payloads: dict[str, Any] | None = None,
    probe_endpoint: bool = True,
    max_retries: int = 3,
    auto_apply: bool = False,
    output_path: Path | None = None,
    http_client: httpx.AsyncClient | None = None,
    audit_logger: AuditLogger | None = None,
) -> RepairResult:
    """Repair a connector config using LLM + validation gate and log an audit entry."""
    parsed_cfg, _ = validate_yaml_schema(config_yaml)
    connector_name = parsed_cfg.name if parsed_cfg is not None else "unknown"

    live_drift: dict[str, DriftReport] = dict(drift_reports or {})
    live_samples: dict[str, Any] = dict(sample_payloads or {})
    diagnostic_lines: list[str] = []
    if error_message:
        diagnostic_lines.append(error_message)

    if probe_endpoint and parsed_cfg is not None and not live_samples:
        inspected_drift, inspected_samples, probe_errs = await inspect_live_drift_context(
            config=parsed_cfg,
            stream_names=stream_names,
            client=http_client,
        )
        for k, v in inspected_drift.items():
            live_drift.setdefault(k, v)
        live_samples.update(inspected_samples)
        diagnostic_lines.extend(probe_errs)

    if parsed_cfg is not None and probe_endpoint:
        initial_val = await validate_connector_config(
            raw_yaml=config_yaml,
            probe_endpoint=True,
            client=http_client,
        )
        if not initial_val.valid:
            diagnostic_lines.append(initial_val.format_feedback())

    error_context = "\n".join(line for line in diagnostic_lines if line)
    initial_prompt = _build_repair_initial_prompt(
        original_yaml=config_yaml,
        error_context=error_context,
        drift_reports=live_drift,
        sample_payloads=live_samples,
    )

    prompt = initial_prompt
    history: list[dict[str, Any]] = []
    last_yaml = config_yaml
    last_validation = ValidationResult(valid=False, errors=["No attempts made."])
    total_attempts = 1 + max(0, max_retries)
    attempts_used = 0

    for attempt in range(1, total_attempts + 1):
        attempts_used = attempt
        raw_response = llm_client.generate(
            prompt=prompt,
            system_instruction=SYSTEM_INSTRUCTION,
        )
        candidate_yaml = extract_yaml_from_response(raw_response)
        validation = await validate_connector_config(
            raw_yaml=candidate_yaml,
            probe_endpoint=probe_endpoint,
            client=http_client,
        )
        history.append(
            {
                "attempt": attempt,
                "valid": validation.valid,
                "errors": list(validation.errors),
                "warnings": list(validation.warnings),
            }
        )
        last_yaml = candidate_yaml
        last_validation = validation

        if validation.valid:
            break

        if attempt < total_attempts:
            prompt = (
                f"{initial_prompt}\n\n"
                "Your previous repair attempt failed the validation gate:\n"
                f"{validation.format_feedback()}\n\n"
                "Previous invalid YAML:\n"
                f"```yaml\n{candidate_yaml}\n```\n\n"
                "Fix every validation error and output ONLY valid YAML."
            )

    repaired = last_validation.valid
    applied = False
    if repaired and auto_apply and output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(last_yaml.strip() + "\n", encoding="utf-8")
        applied = True

    diff_text = compute_yaml_diff(
        before_yaml=config_yaml,
        after_yaml=last_yaml,
        label=output_path.name if output_path else f"{connector_name}.yaml",
    )

    audit_entry: AuditEntry | None = None
    if audit_logger is not None:
        reason = error_context[:300] if error_context else "Manual/scheduled drift repair"
        audit_entry = audit_logger.record(
            AuditEntry.create(
                action="repair",
                connector_name=connector_name,
                attempts=attempts_used,
                accepted=repaired,
                applied=applied,
                reason=reason,
                validation_errors=last_validation.errors,
                diff_text=diff_text,
                before_yaml=config_yaml,
                after_yaml=last_yaml if repaired else None,
                metadata={
                    "sample_counts": last_validation.sample_counts,
                    "auto_apply": auto_apply,
                },
            )
        )

    return RepairResult(
        repaired=repaired,
        applied=applied,
        original_yaml=config_yaml,
        repaired_yaml=last_yaml,
        config=last_validation.config if repaired else None,
        validation=last_validation,
        diff=diff_text,
        attempts=attempts_used,
        drift_reports=live_drift,
        sample_payloads=live_samples,
        history=history,
        audit_entry=audit_entry,
    )
