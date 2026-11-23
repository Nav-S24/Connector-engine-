"""Typer CLI for validating, running, and checking drift on declarative connectors."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

from connector.ai.audit import AuditLogger
from connector.ai.generator import (
    GeminiLLMClient,
    LLMClient,
    generate_connector_config,
)
from connector.ai.repair import repair_connector_config
from connector.api import run_control_server
from connector.config import ConnectorConfig, load_config
from connector.destination import DuckDBDestination
from connector.engine import ConnectorEngine

app = typer.Typer(
    name="connector",
    help="Declarative SaaS data-connector CLI.",
    no_args_is_help=True,
)


def resolve_config_path(name_or_path: str) -> Path:
    """Resolve a connector name (e.g. 'paystub') or YAML file path."""
    candidate = Path(name_or_path)
    if candidate.is_file():
        return candidate

    search_roots = [
        Path.cwd() / "connectors",
        Path(__file__).resolve().parents[2] / "connectors",
    ]
    for root in search_roots:
        for ext in (".yaml", ".yml"):
            p = root / f"{name_or_path}{ext}"
            if p.is_file():
                return p

    raise FileNotFoundError(
        f"Could not find connector config for '{name_or_path}'"
    )


@app.command()
def validate(
    target: str = typer.Argument(
        ..., help="Connector name (e.g. paystub) or path to YAML config."
    ),
) -> None:
    """Validate a connector YAML config against the Pydantic schema."""
    try:
        config_path = resolve_config_path(target)
        config: ConnectorConfig = load_config(config_path)
    except Exception as exc:
        typer.echo(f"Validation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    stream_names = ", ".join(s.name for s in config.streams)
    typer.echo(
        f"Valid config '{config.name}' ({config_path}): "
        f"auth={config.auth.type}, streams=[{stream_names}]"
    )


@app.command()
def run(
    target: str = typer.Argument(
        ..., help="Connector name (e.g. paystub) or path to YAML config."
    ),
    db: Path = typer.Option(
        Path("connector.duckdb"),
        "--db",
        help="Path to the DuckDB destination file.",
    ),
    stream: Optional[list[str]] = typer.Option(
        None,
        "--stream",
        help="Optional stream name(s) to sync.",
    ),
    concurrency: Optional[int] = typer.Option(
        None,
        "--concurrency",
        min=1,
        help="Override max concurrent requests.",
    ),
    batch_size: int = typer.Option(
        1000,
        "--batch-size",
        min=1,
        help="Number of records to buffer per destination upsert batch.",
    ),
) -> None:
    """Run a connector and load records into DuckDB."""
    try:
        config_path = resolve_config_path(target)
        config = load_config(config_path)
        with DuckDBDestination(db_path=db) as destination:
            engine = ConnectorEngine(
                destination=destination,
                max_concurrency=concurrency,
                batch_size=batch_size,
            )
            result = asyncio.run(engine.run(config, stream_names=stream))
    except Exception as exc:
        typer.echo(f"Run failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for sr in result.streams.values():
        typer.echo(
            f"Stream '{sr.stream_name}' -> table '{sr.table_name}': "
            f"rows={sr.rows_synced}, dead_lettered={sr.rows_dead_lettered}, "
            f"pages={sr.pages_fetched}, retries={sr.retries}, "
            f"drift_events={len(sr.drift_events)}, "
            f"duration={sr.duration_seconds:.3f}s"
        )
    typer.echo(
        f"Completed '{result.connector_name}': total_rows={result.total_rows}, "
        f"dead_lettered={result.total_dead_lettered} in {result.duration_seconds:.3f}s"
    )


@app.command("drift-check")
def drift_check(
    target: str = typer.Argument(
        ..., help="Connector name (e.g. paystub) or path to YAML config."
    ),
    stream: Optional[list[str]] = typer.Option(
        None,
        "--stream",
        help="Optional stream name(s) to inspect.",
    ),
    fail_on_drift: bool = typer.Option(
        False,
        "--fail-on-drift",
        help="Exit with code 2 if schema drift is detected.",
    ),
) -> None:
    """Check source API responses against declared stream schemas for drift."""
    try:
        config_path = resolve_config_path(target)
        config = load_config(config_path)
        with DuckDBDestination(":memory:") as destination:
            engine = ConnectorEngine(destination=destination)
            reports = asyncio.run(
                engine.check_drift(config, stream_names=stream)
            )
    except Exception as exc:
        typer.echo(f"Drift check failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    any_drift = any(r.has_drift for r in reports.values())
    payload = {name: report.to_dict() for name, report in reports.items()}
    typer.echo(json.dumps(payload, indent=2))

    if fail_on_drift and any_drift:
        raise typer.Exit(code=2)


def _create_llm_client() -> LLMClient:
    """Instantiate the configured Gemini LLM client from environment variables."""
    return GeminiLLMClient()


@app.command()
def generate(
    spec: Path = typer.Argument(
        ..., help="Path to API specification (OpenAPI JSON/YAML or Markdown/text)."
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        help="Target connector name override.",
    ),
    base_url: Optional[str] = typer.Option(
        None,
        "--base-url",
        help="Target API base URL override.",
    ),
    stream: Optional[list[str]] = typer.Option(
        None,
        "--stream",
        help="Stream or resource hint(s) to focus retrieval and generation.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Optional file path to write the validated YAML config.",
    ),
    probe: bool = typer.Option(
        True,
        "--probe/--no-probe",
        help="Dry-run probe the API endpoint during validation gating.",
    ),
    max_retries: int = typer.Option(
        3,
        "--max-retries",
        min=0,
        help="Maximum validation-repair retry attempts.",
    ),
    audit_log: Optional[Path] = typer.Option(
        Path("ai_audit.jsonl"),
        "--audit-log",
        help="Path to the JSONL AI audit log.",
    ),
) -> None:
    """Generate and validate a connector YAML config from an API specification."""
    if not spec.is_file():
        typer.echo(f"Spec file not found: {spec}", err=True)
        raise typer.Exit(code=1)

    try:
        spec_text = spec.read_text(encoding="utf-8")
        llm_client = _create_llm_client()
        auditor = AuditLogger(log_path=audit_log) if audit_log else None
        result = asyncio.run(
            generate_connector_config(
                spec_text=spec_text,
                llm_client=llm_client,
                connector_name=name,
                base_url=base_url,
                stream_hints=stream,
                max_retries=max_retries,
                probe_endpoint=probe,
                audit_logger=auditor,
            )
        )
    except Exception as exc:
        typer.echo(f"Generation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not result.validation.valid:
        typer.echo(
            f"Validation gate rejected generated config after {result.attempts} attempt(s):\n"
            f"{result.validation.format_feedback()}",
            err=True,
        )
        raise typer.Exit(code=1)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result.yaml_text + "\n", encoding="utf-8")
        typer.echo(
            f"Generated and validated config '{result.config.name}' "
            f"in {result.attempts} attempt(s) -> {output}"
        )
    else:
        typer.echo(result.yaml_text)


@app.command()
def repair(
    target: str = typer.Argument(
        ..., help="Connector name (e.g. paystub) or path to YAML config."
    ),
    stream: Optional[list[str]] = typer.Option(
        None,
        "--stream",
        help="Optional stream name(s) to inspect and repair.",
    ),
    error: Optional[str] = typer.Option(
        None,
        "--error",
        help="Observed error message or drift note to include in repair prompt.",
    ),
    auto_apply: bool = typer.Option(
        False,
        "--auto-apply",
        help="Write repaired YAML back to the target config file if validation passes.",
    ),
    probe: bool = typer.Option(
        True,
        "--probe/--no-probe",
        help="Probe live API responses and validate repaired YAML against endpoint.",
    ),
    max_retries: int = typer.Option(
        3,
        "--max-retries",
        min=0,
        help="Maximum validation-repair retry attempts.",
    ),
    audit_log: Path = typer.Option(
        Path("ai_audit.jsonl"),
        "--audit-log",
        help="Path to the JSONL AI audit log.",
    ),
) -> None:
    """Repair a drifted or broken connector config through the validation gate."""
    try:
        config_path = resolve_config_path(target)
        config_yaml = config_path.read_text(encoding="utf-8")
        llm_client = _create_llm_client()
        auditor = AuditLogger(log_path=audit_log)
        result = asyncio.run(
            repair_connector_config(
                config_yaml=config_yaml,
                llm_client=llm_client,
                error_message=error,
                stream_names=stream,
                probe_endpoint=probe,
                max_retries=max_retries,
                auto_apply=auto_apply,
                output_path=config_path,
                audit_logger=auditor,
            )
        )
    except Exception as exc:
        typer.echo(f"Repair failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not result.repaired:
        typer.echo(
            f"Validation gate rejected repair after {result.attempts} attempt(s):\n"
            f"{result.validation.format_feedback()}",
            err=True,
        )
        raise typer.Exit(code=1)

    if result.diff:
        typer.echo(result.diff)
    else:
        typer.echo("No YAML changes required.")
    status_label = "applied" if result.applied else "proposed (use --auto-apply to write)"
    typer.echo(
        f"Repair validated in {result.attempts} attempt(s) ({status_label})."
    )


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Host interface to bind the control API server.",
    ),
    port: int = typer.Option(
        8100,
        "--port",
        min=1,
        max=65535,
        help="TCP port to run the control API server on.",
    ),
    db: Path = typer.Option(
        Path("connector.duckdb"),
        "--db",
        help="Path to the DuckDB warehouse file.",
    ),
    connectors_dir: Optional[Path] = typer.Option(
        None,
        "--connectors-dir",
        help="Directory containing connector YAML configuration files.",
    ),
    audit_log: Path = typer.Option(
        Path("ai_audit.jsonl"),
        "--audit-log",
        help="Path to the JSONL AI audit log.",
    ),
) -> None:
    """Start the FastAPI control API server with uvicorn (default port 8100)."""
    run_control_server(
        host=host,
        port=port,
        db_path=db,
        connectors_dir=connectors_dir,
        audit_log_path=audit_log,
    )


if __name__ == "__main__":
    app()
