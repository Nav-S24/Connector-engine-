"""Automated validation gate for LLM-generated or repaired connector configs."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import yaml
from pydantic import ValidationError

from connector.auth import AuthError, build_auth
from connector.config import ConnectorConfig, StreamConfig, extract_nested
from connector.destination import DuckDBDestination
from connector.drift import validate_record
from connector.engine import resolve_table_name
from connector.pagination import (
    CursorPagination,
    OffsetPagination,
    build_pagination,
)
from connector.retry import RetryConfig, request_with_retry


@dataclass
class ValidationResult:
    """Structured outcome of validating a YAML connector config."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    config: ConnectorConfig | None = None
    sample_counts: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def format_feedback(self) -> str:
        """Render validation errors and warnings as concise feedback for LLM retries."""
        lines: list[str] = []
        if self.errors:
            lines.append("Validation errors:")
            for idx, err in enumerate(self.errors, start=1):
                lines.append(f"  {idx}. {err}")
        if self.warnings:
            lines.append("Warnings:")
            for warn in self.warnings:
                lines.append(f"  - {warn}")
        return "\n".join(lines) if lines else "Validation succeeded."


def strip_markdown_fences(raw_text: str) -> str:
    """Remove surrounding markdown code fences if present."""
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def validate_yaml_schema(
    raw_yaml: str,
) -> tuple[ConnectorConfig | None, list[str]]:
    """Parse raw YAML text and validate it against ConnectorConfig."""
    cleaned = strip_markdown_fences(raw_yaml)
    if not cleaned:
        return None, ["Generated YAML is empty."]

    try:
        parsed = yaml.safe_load(cleaned)
    except yaml.YAMLError as exc:
        return None, [f"YAML syntax error: {exc}"]

    if not isinstance(parsed, dict):
        return None, [
            f"Top-level YAML must be a mapping/object, got {type(parsed).__name__}."
        ]

    try:
        config = ConnectorConfig.model_validate(parsed)
    except ValidationError as exc:
        errors: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", ()))
            msg = err.get("msg", "Invalid field")
            errors.append(f"Schema error at '{loc}': {msg}")
        return None, errors

    return config, []


async def _probe_stream(
    client: httpx.AsyncClient,
    config: ConnectorConfig,
    stream: StreamConfig,
    retry_config: RetryConfig,
    max_probe_pages: int,
    dest: DuckDBDestination,
) -> tuple[int, list[str], list[str]]:
    """Fetch sample pages for a stream and validate records against its config."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        auth = build_auth(config.auth, client=client)
        initial_headers = await auth.get_headers()
    except AuthError as exc:
        return 0, [f"Authentication setup failed: {exc}"], warnings
    except Exception as exc:
        return 0, [f"Failed to acquire auth credentials: {exc}"], warnings

    pagination = build_pagination(stream.pagination)
    url = f"{config.base_url.rstrip('/')}/{stream.path.lstrip('/')}"
    page_params: dict[str, Any] | None = pagination.initial_params()
    seen_params: list[dict[str, Any]] = []
    sample_records: list[dict[str, Any]] = []
    pages_fetched = 0

    while page_params is not None and pages_fetched < max_probe_pages:
        if page_params in seen_params:
            errors.append(
                f"Stream '{stream.name}': pagination looped with repeated params {page_params}"
            )
            break
        seen_params.append(dict(page_params))

        merged_params = {**stream.params, **page_params}
        try:
            response = await request_with_retry(
                client=client,
                method=stream.method,
                url=url,
                headers=initial_headers,
                params=merged_params,
                retry_config=retry_config,
                on_unauthorized=auth.refresh_on_unauthorized,
            )
        except Exception as exc:
            errors.append(
                f"Stream '{stream.name}': HTTP request failed for {url}: {exc}"
            )
            return 0, errors, warnings

        try:
            payload = response.json()
        except Exception as exc:
            errors.append(
                f"Stream '{stream.name}': response from {url} is not valid JSON: {exc}"
            )
            return 0, errors, warnings

        raw_records = extract_nested(payload, stream.records_path)
        if not isinstance(raw_records, list):
            errors.append(
                f"Stream '{stream.name}': records_path '{stream.records_path}' "
                f"did not resolve to a list (got {type(raw_records).__name__})"
            )
            return 0, errors, warnings

        if pages_fetched == 0:
            if isinstance(pagination, OffsetPagination) and stream.pagination.total_path:
                total_val = extract_nested(payload, stream.pagination.total_path)
                if not isinstance(total_val, (int, float)):
                    errors.append(
                        f"Stream '{stream.name}': total_path '{stream.pagination.total_path}' "
                        f"did not resolve to a number"
                    )
            if isinstance(pagination, CursorPagination):
                if stream.pagination.has_more_path:
                    hm = extract_nested(payload, stream.pagination.has_more_path)
                    if not isinstance(hm, bool):
                        errors.append(
                            f"Stream '{stream.name}': has_more_path "
                            f"'{stream.pagination.has_more_path}' must resolve to a boolean"
                        )
                if stream.pagination.next_cursor_path:
                    nc = extract_nested(payload, stream.pagination.next_cursor_path)
                    if nc is not None and not isinstance(nc, (str, int)):
                        errors.append(
                            f"Stream '{stream.name}': next_cursor_path "
                            f"'{stream.pagination.next_cursor_path}' must be string or int"
                        )

        dict_records = [r for r in raw_records if isinstance(r, dict)]
        if len(dict_records) != len(raw_records):
            errors.append(
                f"Stream '{stream.name}': expected dict records at '{stream.records_path}'"
            )
            return 0, errors, warnings

        for idx, rec in enumerate(dict_records):
            rec_err = validate_record(
                record=rec,
                schema=dict(stream.schema_),
                primary_key=stream.primary_key,
            )
            if rec_err is not None:
                errors.append(
                    f"Stream '{stream.name}' sample record[{idx}] validation failed: {rec_err}"
                )
                break
            if stream.incremental is not None:
                cf = stream.incremental.cursor_field
                if cf not in rec:
                    errors.append(
                        f"Stream '{stream.name}': incremental cursor_field '{cf}' "
                        f"missing from sample record"
                    )
                    break

        sample_records.extend(dict_records)
        pages_fetched += 1
        if errors:
            break

        page_params = pagination.next_params(
            records=dict_records,
            response_payload=payload,
            current_params=page_params,
            response_headers=response.headers,
        )

    if not sample_records and not errors:
        warnings.append(
            f"Stream '{stream.name}' returned 0 records during dry-run probe."
        )

    if sample_records and not errors:
        table = resolve_table_name(config.name, stream.name)
        try:
            dest.ensure_table(table, dict(stream.schema_), stream.primary_key)
            dest.upsert(
                table,
                sample_records,
                stream.primary_key,
                dict(stream.schema_),
            )
        except Exception as exc:
            errors.append(
                f"Stream '{stream.name}': destination dry-run upsert failed: {exc}"
            )

    return len(sample_records), errors, warnings


async def validate_connector_config(
    raw_yaml: str,
    probe_endpoint: bool = True,
    client: httpx.AsyncClient | None = None,
    max_probe_pages: int = 2,
    timeout: float = 15.0,
    retry_config: RetryConfig | None = None,
) -> ValidationResult:
    """Validate YAML syntax, Pydantic schema, and optionally live/mock endpoint probe."""
    started = time.perf_counter()
    config, schema_errors = validate_yaml_schema(raw_yaml)
    if schema_errors or config is None:
        return ValidationResult(
            valid=False,
            errors=schema_errors,
            duration_seconds=round(time.perf_counter() - started, 4),
        )

    if not probe_endpoint:
        return ValidationResult(
            valid=True,
            config=config,
            duration_seconds=round(time.perf_counter() - started, 4),
        )

    all_errors: list[str] = []
    all_warnings: list[str] = []
    sample_counts: dict[str, int] = {}
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=timeout)
    effective_retry = retry_config or RetryConfig(
        max_retries=2, base_delay=0.05, max_delay=0.25, jitter=False
    )

    try:
        with DuckDBDestination(":memory:") as dest:
            for stream in config.streams:
                count, s_errors, s_warnings = await _probe_stream(
                    client=active_client,
                    config=config,
                    stream=stream,
                    retry_config=effective_retry,
                    max_probe_pages=max_probe_pages,
                    dest=dest,
                )
                sample_counts[stream.name] = count
                all_errors.extend(s_errors)
                all_warnings.extend(s_warnings)
    finally:
        if owns_client:
            await active_client.aclose()

    return ValidationResult(
        valid=len(all_errors) == 0,
        errors=all_errors,
        warnings=all_warnings,
        config=config if not all_errors else None,
        sample_counts=sample_counts,
        duration_seconds=round(time.perf_counter() - started, 4),
    )
