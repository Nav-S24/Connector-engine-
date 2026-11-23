"""FastAPI control plane API exposing connectors, runs, drift, repairs, audit, and benchmarks."""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import httpx
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from connector.ai.audit import AuditEntry, AuditLogger
from connector.ai.generator import LLMClient
from connector.ai.validator import validate_connector_config
from connector.config import ConnectorConfig, load_config
from connector.destination import DuckDBDestination
from connector.engine import ConnectorEngine, resolve_table_name

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_connectors_dir() -> Path:
    """Resolve the default directory containing declarative connector YAML files."""
    env_dir = os.environ.get("CONNECTOR_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    cwd_connectors = Path.cwd() / "connectors"
    if cwd_connectors.is_dir():
        return cwd_connectors
    return _DEFAULT_REPO_ROOT / "connectors"


def _default_benchmarks_dir() -> Path:
    """Resolve the default directory containing benchmark and AI eval results."""
    env_dir = os.environ.get("CONNECTOR_BENCHMARKS_DIR")
    if env_dir:
        return Path(env_dir)
    cwd_bench = Path.cwd() / "benchmarks"
    if cwd_bench.is_dir():
        return cwd_bench
    return _DEFAULT_REPO_ROOT / "benchmarks"


class _ControlStore:
    """Thread-safe DuckDB metadata and destination manager for the control API."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._persistent = self.db_path == ":memory:"
        self._dest: DuckDBDestination | None = None
        self._refs = 0
        self._tables_ready = False

    @contextmanager
    def acquire(self) -> Iterator[DuckDBDestination]:
        """Acquire a DuckDBDestination reference, keeping the file unlocked when idle."""
        with self._lock:
            if self._dest is None:
                self._dest = DuckDBDestination(self.db_path)
            if not self._tables_ready:
                self._ensure_control_tables(self._dest)
                self._tables_ready = True
            self._refs += 1
            dest = self._dest
        try:
            yield dest
        finally:
            with self._lock:
                self._refs -= 1
                if self._refs == 0 and not self._persistent and self._dest is not None:
                    self._dest.close()
                    self._dest = None
                    self._tables_ready = False

    def close(self) -> None:
        """Close any lingering DuckDB connection on app shutdown."""
        with self._lock:
            if self._dest is not None:
                self._dest.close()
                self._dest = None
                self._refs = 0

    def _ensure_control_tables(self, dest: DuckDBDestination) -> None:
        """Create control-plane tables for API run metadata and repair proposals."""
        dest.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _api_runs (
                run_id VARCHAR PRIMARY KEY,
                engine_run_id VARCHAR,
                connector_name VARCHAR NOT NULL,
                started_at VARCHAR NOT NULL,
                finished_at VARCHAR,
                status VARCHAR NOT NULL,
                rows_synced BIGINT NOT NULL,
                rows_dead_lettered BIGINT NOT NULL,
                retries BIGINT NOT NULL,
                duration_ms DOUBLE NOT NULL,
                drift_events VARCHAR NOT NULL,
                streams_json VARCHAR NOT NULL,
                error_message VARCHAR
            )
            """
        )
        dest.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _api_repairs (
                id VARCHAR PRIMARY KEY,
                connector VARCHAR NOT NULL,
                trigger VARCHAR NOT NULL,
                diff VARCHAR NOT NULL,
                before_yaml VARCHAR NOT NULL,
                proposed_yaml VARCHAR NOT NULL,
                validation_results VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                attempts BIGINT NOT NULL,
                created_at VARCHAR NOT NULL,
                updated_at VARCHAR NOT NULL
            )
            """
        )

    def create_run_record(self, run_id: str, connector_name: str, started_at: str) -> None:
        """Insert an initial 'running' tracking row for an async background sync."""
        with self._lock, self.acquire() as dest:
            self._ensure_control_tables(dest)
            dest.conn.execute(
                """
                INSERT INTO _api_runs (
                    run_id, engine_run_id, connector_name, started_at, finished_at,
                    status, rows_synced, rows_dead_lettered, retries, duration_ms,
                    drift_events, streams_json, error_message
                )
                VALUES (?, NULL, ?, ?, NULL, 'running', 0, 0, 0, 0.0, '[]', '{}', NULL)
                """,
                [run_id, connector_name, started_at],
            )

    def complete_run_record(
        self,
        run_id: str,
        engine_run_id: str | None,
        finished_at: str,
        status: str,
        rows_synced: int,
        rows_dead_lettered: int,
        retries: int,
        duration_ms: float,
        drift_events: list[dict[str, Any]],
        streams: dict[str, dict[str, Any]],
        error_message: str | None = None,
    ) -> None:
        """Update a background sync row upon completion or failure."""
        with self._lock, self.acquire() as dest:
            self._ensure_control_tables(dest)
            dest.conn.execute(
                """
                UPDATE _api_runs
                SET engine_run_id = ?,
                    finished_at = ?,
                    status = ?,
                    rows_synced = ?,
                    rows_dead_lettered = ?,
                    retries = ?,
                    duration_ms = ?,
                    drift_events = ?,
                    streams_json = ?,
                    error_message = ?
                WHERE run_id = ?
                """,
                [
                    engine_run_id,
                    finished_at,
                    status,
                    rows_synced,
                    rows_dead_lettered,
                    retries,
                    duration_ms,
                    json.dumps(drift_events),
                    json.dumps(streams),
                    error_message,
                    run_id,
                ],
            )

    def list_api_runs(self, connector: str | None = None) -> list[dict[str, Any]]:
        """Fetch control-plane run metadata rows."""
        with self._lock, self.acquire() as dest:
            self._ensure_control_tables(dest)
            if connector:
                cur = dest.conn.execute(
                    "SELECT * FROM _api_runs WHERE connector_name = ? ORDER BY started_at DESC",
                    [connector],
                )
            else:
                cur = dest.conn.execute(
                    "SELECT * FROM _api_runs ORDER BY started_at DESC"
                )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_api_run(self, run_id: str) -> dict[str, Any] | None:
        """Lookup a control-plane run by either control run_id or engine_run_id."""
        with self._lock, self.acquire() as dest:
            self._ensure_control_tables(dest)
            cur = dest.conn.execute(
                "SELECT * FROM _api_runs WHERE run_id = ? OR engine_run_id = ? LIMIT 1",
                [run_id, run_id],
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    def upsert_repair(
        self,
        repair_id: str,
        connector: str,
        trigger: str,
        diff: str,
        before_yaml: str,
        proposed_yaml: str,
        validation_results: dict[str, Any],
        status: str,
        attempts: int,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Insert or ignore an existing repair proposal idempotently."""
        now = created_at or datetime.now(timezone.utc).isoformat()
        with self._lock, self.acquire() as dest:
            self._ensure_control_tables(dest)
            existing = dest.conn.execute(
                "SELECT id FROM _api_repairs WHERE id = ?", [repair_id]
            ).fetchone()
            if existing is None:
                dest.conn.execute(
                    """
                    INSERT INTO _api_repairs (
                        id, connector, trigger, diff, before_yaml, proposed_yaml,
                        validation_results, status, attempts, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        repair_id,
                        connector,
                        trigger,
                        diff,
                        before_yaml,
                        proposed_yaml,
                        json.dumps(validation_results),
                        status,
                        attempts,
                        now,
                        now,
                    ],
                )
        found = self.get_repair(repair_id)
        assert found is not None
        return found

    def get_repair(self, repair_id: str) -> dict[str, Any] | None:
        """Retrieve a repair proposal by ID."""
        with self._lock, self.acquire() as dest:
            self._ensure_control_tables(dest)
            cur = dest.conn.execute(
                "SELECT * FROM _api_repairs WHERE id = ? LIMIT 1",
                [repair_id],
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
            return self._decode_repair_row(dict(zip(cols, row)))

    def list_repairs(self) -> list[dict[str, Any]]:
        """List all repair proposals ordered newest first."""
        with self._lock, self.acquire() as dest:
            self._ensure_control_tables(dest)
            cur = dest.conn.execute(
                "SELECT * FROM _api_repairs ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [
                self._decode_repair_row(dict(zip(cols, row)))
                for row in cur.fetchall()
            ]

    def update_repair_status(
        self,
        repair_id: str,
        status: str,
        validation_results: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update repair proposal status and optionally validation_results."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self.acquire() as dest:
            self._ensure_control_tables(dest)
            if validation_results is not None:
                dest.conn.execute(
                    """
                    UPDATE _api_repairs
                    SET status = ?, validation_results = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    [status, json.dumps(validation_results), now, repair_id],
                )
            else:
                dest.conn.execute(
                    """
                    UPDATE _api_repairs
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    [status, now, repair_id],
                )
        return self.get_repair(repair_id)

    @staticmethod
    def _decode_repair_row(row: dict[str, Any]) -> dict[str, Any]:
        """Deserialize JSON columns on a repair row for API responses."""
        try:
            validation_results = json.loads(row.get("validation_results") or "{}")
        except json.JSONDecodeError:
            validation_results = {}
        return {
            "id": row["id"],
            "connector": row["connector"],
            "trigger": row["trigger"],
            "diff": row["diff"],
            "before_yaml": row["before_yaml"],
            "proposed_yaml": row["proposed_yaml"],
            "validation_results": validation_results,
            "status": row["status"],
            "attempts": int(row["attempts"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def _discover_connectors(connectors_dir: Path) -> dict[str, tuple[Path, ConnectorConfig]]:
    """Load valid connector YAML configs from connectors_dir keyed by config name."""
    discovered: dict[str, tuple[Path, ConnectorConfig]] = {}
    if not connectors_dir.is_dir():
        return discovered
    for path in sorted(connectors_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (".yaml", ".yml"):
            continue
        try:
            cfg = load_config(path)
        except Exception:
            continue
        discovered[cfg.name] = (path, cfg)
    return discovered


def _parse_json_safe(raw: Any, fallback: Any) -> Any:
    """Safely parse a JSON string, returning fallback if parsing fails."""
    if not isinstance(raw, str):
        return raw if raw is not None else fallback
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _sync_audit_repairs_to_store(
    audit_logger: AuditLogger, store: _ControlStore
) -> None:
    """Import any CLI-recorded repair entries from ai_audit.jsonl into _api_repairs."""
    for entry in audit_logger.read_all():
        if entry.action != "repair":
            continue
        if entry.applied:
            status = "applied"
        elif entry.accepted:
            status = "proposed"
        else:
            status = "escalated"

        validation_results = {
            "valid": entry.accepted,
            "errors": list(entry.validation_errors),
            "sample_counts": entry.metadata.get("sample_counts", {}),
        }
        store.upsert_repair(
            repair_id=entry.entry_id,
            connector=entry.connector_name,
            trigger=entry.reason,
            diff=entry.diff_text,
            before_yaml=entry.before_yaml or "",
            proposed_yaml=entry.after_yaml or entry.before_yaml or "",
            validation_results=validation_results,
            status=status,
            attempts=entry.attempts,
            created_at=entry.timestamp,
        )


def _parse_benchmarks(benchmarks_dir: Path) -> dict[str, Any]:
    """Read throughput CSV and AI evaluation Markdown report into JSON."""
    throughput_rows: list[dict[str, Any]] = []
    csv_path = benchmarks_dir / "throughput_results.csv"
    if csv_path.is_file():
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                throughput_rows.append(
                    {
                        "scenario": row["scenario"],
                        "concurrency": int(row["concurrency"]),
                        "batch_size": int(row["batch_size"]),
                        "use_pooling": row["use_pooling"].strip().lower() == "true",
                        "latency_ms": float(row["latency_ms"]),
                        "rate_429": float(row["rate_429"]),
                        "rate_500": float(row["rate_500"]),
                        "records_target": int(row["records_target"]),
                        "records_loaded": int(row["records_loaded"]),
                        "retries": int(row["retries"]),
                        "duration_seconds": float(row["duration_seconds"]),
                        "records_per_second": float(row["records_per_second"]),
                    }
                )

    baseline = next(
        (r for r in throughput_rows if r["scenario"] == "baseline_sequential"),
        None,
    )
    clean_rows = [
        r for r in throughput_rows if r["rate_429"] == 0.0 and r["rate_500"] == 0.0
    ]
    optimized = (
        max(clean_rows, key=lambda r: r["records_per_second"]) if clean_rows else None
    )
    speedup = (
        round(
            optimized["records_per_second"] / max(1e-6, baseline["records_per_second"]),
            2,
        )
        if baseline and optimized
        else None
    )

    ai_metrics: dict[str, Any] = {}
    ai_scenarios: list[dict[str, Any]] = []
    raw_markdown = ""
    results_md = benchmarks_dir / "results.md"
    if results_md.is_file():
        raw_markdown = results_md.read_text(encoding="utf-8")
        section = "none"
        for line in raw_markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith("## Summary Metrics"):
                section = "metrics"
                continue
            if stripped.startswith("## Scenario Breakdown"):
                section = "scenarios"
                continue
            if not stripped.startswith("|") or stripped.startswith("| ---"):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if section == "metrics" and len(cells) == 2 and cells[0] != "Metric":
                key = re.sub(r"[^a-z0-9]+", "_", cells[0].lower()).strip("_")
                ai_metrics[key] = cells[1]
            elif section == "scenarios" and len(cells) == 10 and cells[0] != "ID":
                ai_scenarios.append(
                    {
                        "id": cells[0].strip("`"),
                        "kind": cells[1],
                        "service": cells[2],
                        "description": cells[3],
                        "expected": cells[4].lower() == "true",
                        "accepted": cells[5].lower() == "true",
                        "attempts": int(cells[6]),
                        "synced_rows": cells[7],
                        "latency_ms": float(cells[8]),
                        "status": cells[9],
                    }
                )

    return {
        "throughput": {
            "rows": throughput_rows,
            "baseline_rps": baseline["records_per_second"] if baseline else None,
            "optimized_rps": optimized["records_per_second"] if optimized else None,
            "speedup": speedup,
        },
        "ai_eval": {
            "metrics": ai_metrics,
            "scenarios": ai_scenarios,
            "raw_markdown": raw_markdown,
        },
    }


def _format_run_summary_row(
    row: dict[str, Any], api_row: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Normalize a _run_summary row into the JSON summary payload."""
    drift_events = _parse_json_safe(row.get("drift_details"), [])
    duration_seconds = float(row.get("duration_seconds", 0.0))
    return {
        "run_id": api_row["run_id"] if api_row is not None else row["run_id"],
        "engine_run_id": row["run_id"],
        "connector_name": row["connector_name"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_seconds": duration_seconds,
        "duration_ms": round(duration_seconds * 1000.0, 2),
        "status": row["status"],
        "rows_synced": int(row["rows_synced"]),
        "rows_dead_lettered": int(row["rows_dead_lettered"]),
        "retries": int(row["retries"]),
        "drift_events_count": int(row.get("drift_events_count", len(drift_events))),
        "drift_events": drift_events,
    }


def create_control_app(
    db_path: str | Path | None = None,
    connectors_dir: str | Path | None = None,
    audit_log_path: str | Path | None = None,
    benchmarks_dir: str | Path | None = None,
    http_client: httpx.AsyncClient | None = None,
    llm_client: LLMClient | None = None,
) -> FastAPI:
    """Create the FastAPI control API application."""
    resolved_db = (
        db_path
        if db_path is not None
        else Path(os.environ.get("CONNECTOR_DB_PATH", "connector.duckdb"))
    )
    resolved_connectors_dir = (
        Path(connectors_dir) if connectors_dir is not None else _default_connectors_dir()
    )
    resolved_audit_path = (
        Path(audit_log_path)
        if audit_log_path is not None
        else Path(os.environ.get("CONNECTOR_AUDIT_LOG", "ai_audit.jsonl"))
    )
    resolved_benchmarks_dir = (
        Path(benchmarks_dir) if benchmarks_dir is not None else _default_benchmarks_dir()
    )

    store = _ControlStore(resolved_db)
    auditor = AuditLogger(log_path=resolved_audit_path)

    background_tasks: set[asyncio.Task[Any]] = set()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            for task in list(background_tasks):
                if not task.done():
                    await task
            store.close()

    app = FastAPI(
        title="Connector Platform Control API",
        version="0.1.0",
        description="Control plane API for declarative SaaS connectors, runs, drift, repairs, and audits.",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.store = store
    app.state.auditor = auditor
    app.state.connectors_dir = resolved_connectors_dir
    app.state.benchmarks_dir = resolved_benchmarks_dir
    app.state.http_client = http_client
    app.state.llm_client = llm_client
    app.state.background_tasks = background_tasks

    async def _execute_background_sync(
        control_run_id: str, config: ConnectorConfig, started_at: str
    ) -> None:
        engine_run_id: str | None = None
        status = "failed"
        rows_synced = 0
        rows_dead_lettered = 0
        retries = 0
        duration_ms = 0.0
        drift_events: list[dict[str, Any]] = []
        streams_payload: dict[str, dict[str, Any]] = {}
        error_msg: str | None = None

        with store.acquire() as dest:
            engine = ConnectorEngine(destination=dest, client=app.state.http_client)
            try:
                res = await engine.run(config)
                engine_run_id = res.run_id
                status = res.status
                rows_synced = res.total_rows
                rows_dead_lettered = res.total_dead_lettered
                retries = res.total_retries
                duration_ms = round(res.duration_seconds * 1000.0, 2)
                drift_events = [d.to_dict() for d in res.drift_events]
                streams_payload = {
                    s_name: {
                        "stream_name": sr.stream_name,
                        "table_name": sr.table_name,
                        "rows_synced": sr.rows_synced,
                        "rows_dead_lettered": sr.rows_dead_lettered,
                        "pages_fetched": sr.pages_fetched,
                        "retries": sr.retries,
                        "drift_events": [d.to_dict() for d in sr.drift_events],
                        "bookmark": sr.bookmark,
                        "duration_ms": round(sr.duration_seconds * 1000.0, 2),
                    }
                    for s_name, sr in res.streams.items()
                }
            except Exception as exc:
                error_msg = str(exc)
                with store.acquire() as summary_dest:
                    summaries = summary_dest.fetch_run_summaries(
                        connector_name=config.name
                    )
                    matching = [
                        s for s in summaries if s["started_at"] >= started_at
                    ]
                    if matching:
                        latest = matching[-1]
                        engine_run_id = latest["run_id"]
                        duration_ms = round(
                            float(latest["duration_seconds"]) * 1000.0, 2
                        )

        finished_at = datetime.now(timezone.utc).isoformat()
        store.complete_run_record(
            run_id=control_run_id,
            engine_run_id=engine_run_id,
            finished_at=finished_at,
            status=status,
            rows_synced=rows_synced,
            rows_dead_lettered=rows_dead_lettered,
            retries=retries,
            duration_ms=duration_ms,
            drift_events=drift_events,
            streams=streams_payload,
            error_message=error_msg,
        )

    def _resolve_run_context(run_id: str) -> dict[str, Any]:
        api_row = store.get_api_run(run_id)
        with store.acquire() as dest:
            summaries = dest.fetch_run_summaries()
        summary_by_id = {row["run_id"]: row for row in summaries}

        summary_row: dict[str, Any] | None = None
        if api_row is not None and api_row.get("engine_run_id"):
            summary_row = summary_by_id.get(api_row["engine_run_id"])
        elif run_id in summary_by_id:
            summary_row = summary_by_id[run_id]

        if api_row is None and summary_row is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

        connector_name = (
            api_row["connector_name"]
            if api_row is not None
            else summary_row["connector_name"]  # type: ignore[index]
        )
        started_at = (
            api_row["started_at"]
            if api_row is not None
            else summary_row["started_at"]  # type: ignore[index]
        )
        finished_at = (
            api_row["finished_at"]
            if api_row is not None
            else summary_row["finished_at"]  # type: ignore[index]
        )

        if api_row is not None:
            streams = _parse_json_safe(api_row.get("streams_json"), {})
            drift_events = _parse_json_safe(api_row.get("drift_events"), [])
            if not drift_events and summary_row is not None:
                drift_events = _parse_json_safe(summary_row.get("drift_details"), [])
            return {
                "run_id": api_row["run_id"],
                "engine_run_id": api_row.get("engine_run_id"),
                "connector_name": connector_name,
                "started_at": started_at,
                "finished_at": finished_at,
                "status": api_row["status"],
                "rows_synced": int(api_row["rows_synced"]),
                "rows_dead_lettered": int(api_row["rows_dead_lettered"]),
                "retries": int(api_row["retries"]),
                "drift_events": drift_events,
                "duration_ms": float(api_row["duration_ms"]),
                "streams": streams,
                "error_message": api_row.get("error_message"),
            }

        assert summary_row is not None
        drift_events = _parse_json_safe(summary_row.get("drift_details"), [])
        discovered = _discover_connectors(app.state.connectors_dir)
        streams_breakdown: dict[str, dict[str, Any]] = {}
        if connector_name in discovered:
            _, cfg = discovered[connector_name]
            with store.acquire() as dest:
                dl_rows = dest.fetch_dead_letters(connector_name=connector_name)
                run_dls = [
                    dl
                    for dl in dl_rows
                    if started_at <= dl["recorded_at"] <= (finished_at or dl["recorded_at"])
                ]
                for stream in cfg.streams:
                    table = resolve_table_name(connector_name, stream.name)
                    try:
                        table_rows = dest.count(table)
                    except Exception:
                        table_rows = 0
                    stream_dl = sum(
                        1 for dl in run_dls if dl["stream_name"] == stream.name
                    )
                    stream_drift = [
                        d for d in drift_events if d.get("stream_name") == stream.name
                    ]
                    streams_breakdown[stream.name] = {
                        "stream_name": stream.name,
                        "table_name": table,
                        "rows_synced": (
                            int(summary_row["rows_synced"])
                            if len(cfg.streams) == 1
                            else table_rows
                        ),
                        "rows_dead_lettered": stream_dl,
                        "retries": int(summary_row["retries"]) if len(cfg.streams) == 1 else 0,
                        "drift_events": stream_drift,
                        "duration_ms": round(
                            float(summary_row["duration_seconds"]) * 1000.0, 2
                        ),
                    }

        return {
            "run_id": summary_row["run_id"],
            "engine_run_id": summary_row["run_id"],
            "connector_name": connector_name,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": summary_row["status"],
            "rows_synced": int(summary_row["rows_synced"]),
            "rows_dead_lettered": int(summary_row["rows_dead_lettered"]),
            "retries": int(summary_row["retries"]),
            "drift_events": drift_events,
            "duration_ms": round(float(summary_row["duration_seconds"]) * 1000.0, 2),
            "streams": streams_breakdown,
            "error_message": None,
        }

    @app.get("/api/connectors")
    async def list_connectors() -> list[dict[str, Any]]:
        """List configured connectors along with their latest run status and metrics."""
        discovered = _discover_connectors(app.state.connectors_dir)
        with store.acquire() as dest:
            summaries = dest.fetch_run_summaries()
        api_runs = store.list_api_runs()

        latest_summary: dict[str, dict[str, Any]] = {}
        for row in summaries:
            latest_summary[row["connector_name"]] = row

        latest_running: dict[str, dict[str, Any]] = {}
        for row in api_runs:
            if row["status"] == "running" and row["connector_name"] not in latest_running:
                latest_running[row["connector_name"]] = row

        items: list[dict[str, Any]] = []
        for name in sorted(discovered):
            _, cfg = discovered[name]
            running = latest_running.get(name)
            last = latest_summary.get(name)
            if running is not None and (
                last is None or running["started_at"] >= last["started_at"]
            ):
                last_status = "running"
                last_at = running["started_at"]
                last_rows = int(running["rows_synced"])
            elif last is not None:
                last_status = last["status"]
                last_at = last["finished_at"] or last["started_at"]
                last_rows = int(last["rows_synced"])
            else:
                last_status = None
                last_at = None
                last_rows = None

            items.append(
                {
                    "name": cfg.name,
                    "streams": [s.name for s in cfg.streams],
                    "last_run_status": last_status,
                    "last_run_at": last_at,
                    "last_rows_synced": last_rows,
                }
            )
        return items

    @app.post("/api/connectors/{name}/run")
    async def trigger_connector_run(
        name: str, background_tasks: BackgroundTasks
    ) -> dict[str, str]:
        """Start an async connector sync in the background and return its run_id."""
        discovered = _discover_connectors(app.state.connectors_dir)
        entry = discovered.get(name)
        if entry is None:
            raise HTTPException(
                status_code=404, detail=f"Connector '{name}' not found"
            )
        _, cfg = entry
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        store.create_run_record(
            run_id=run_id,
            connector_name=cfg.name,
            started_at=started_at,
        )
        background_tasks.add_task(
            _execute_background_sync,
            run_id,
            cfg,
            started_at,
        )
        return {"run_id": run_id}

    @app.get("/api/runs")
    async def list_runs(
        connector: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        """List run summaries from _run_summary ordered newest first."""
        connector_filter = connector.strip() if connector else None
        with store.acquire() as dest:
            summaries = dest.fetch_run_summaries(connector_name=connector_filter)

        api_runs = store.list_api_runs(connector=connector_filter)
        api_by_engine = {
            r["engine_run_id"]: r for r in api_runs if r.get("engine_run_id")
        }

        items = [
            _format_run_summary_row(row, api_by_engine.get(row["run_id"]))
            for row in reversed(summaries)
        ]

        # Include in-flight runs that have not yet finalized into _run_summary
        for ar in api_runs:
            if ar["status"] == "running" and not ar.get("engine_run_id"):
                items.insert(
                    0,
                    {
                        "run_id": ar["run_id"],
                        "engine_run_id": None,
                        "connector_name": ar["connector_name"],
                        "started_at": ar["started_at"],
                        "finished_at": ar["finished_at"],
                        "duration_seconds": 0.0,
                        "duration_ms": 0.0,
                        "status": ar["status"],
                        "rows_synced": int(ar["rows_synced"]),
                        "rows_dead_lettered": int(ar["rows_dead_lettered"]),
                        "retries": int(ar["retries"]),
                        "drift_events_count": 0,
                        "drift_events": [],
                    },
                )

        return items[:limit]

    @app.get("/api/runs/{run_id}")
    async def get_run_detail(run_id: str) -> dict[str, Any]:
        """Return full run summary including duration_ms and per-stream breakdown."""
        return _resolve_run_context(run_id)

    @app.get("/api/runs/{run_id}/dead-letters")
    async def get_run_dead_letters(run_id: str) -> list[dict[str, Any]]:
        """Return dead-letter records associated with a specific run."""
        ctx = _resolve_run_context(run_id)
        connector_name = ctx["connector_name"]
        started_at = ctx["started_at"]
        finished_at = ctx["finished_at"]

        with store.acquire() as dest:
            rows = dest.fetch_dead_letters(connector_name=connector_name)

        filtered: list[dict[str, Any]] = []
        for row in rows:
            rec_at = row["recorded_at"]
            if rec_at < started_at:
                continue
            if finished_at is not None and rec_at > finished_at:
                continue
            filtered.append(
                {
                    "id": row["id"],
                    "connector_name": row["connector_name"],
                    "stream_name": row["stream_name"],
                    "error_reason": row["error_reason"],
                    "raw_record": _parse_json_safe(row["raw_record"], row["raw_record"]),
                    "recorded_at": row["recorded_at"],
                }
            )
        return filtered

    @app.get("/api/drift")
    async def list_drift_events(
        connector: str | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        """List normalized schema drift events extracted from run summaries."""
        connector_filter = connector.strip() if connector else None
        with store.acquire() as dest:
            summaries = dest.fetch_run_summaries(connector_name=connector_filter)

        events: list[dict[str, Any]] = []
        for row in reversed(summaries):
            detected_at = row["finished_at"] or row["started_at"]
            details = _parse_json_safe(row.get("drift_details"), [])
            for report in details:
                if not isinstance(report, dict):
                    continue
                stream_name = report.get("stream_name", "")
                for field_name, field_type in report.get("added_fields", {}).items():
                    events.append(
                        {
                            "connector": row["connector_name"],
                            "stream": stream_name,
                            "type": "added",
                            "field": field_name,
                            "details": {"inferred_type": field_type},
                            "detected_at": detected_at,
                            "run_id": row["run_id"],
                        }
                    )
                for field_name in report.get("removed_fields", []):
                    events.append(
                        {
                            "connector": row["connector_name"],
                            "stream": stream_name,
                            "type": "removed",
                            "field": field_name,
                            "details": {},
                            "detected_at": detected_at,
                            "run_id": row["run_id"],
                        }
                    )
                for field_name, pair in report.get("type_changes", {}).items():
                    events.append(
                        {
                            "connector": row["connector_name"],
                            "stream": stream_name,
                            "type": "retyped",
                            "field": field_name,
                            "details": {
                                "expected": pair[0] if len(pair) > 0 else None,
                                "observed": pair[1] if len(pair) > 1 else None,
                            },
                            "detected_at": detected_at,
                            "run_id": row["run_id"],
                        }
                    )
        return events

    @app.get("/api/repairs")
    async def list_repairs() -> list[dict[str, Any]]:
        """List repair proposals from the control store and AI audit log."""
        _sync_audit_repairs_to_store(app.state.auditor, store)
        return store.list_repairs()

    @app.post("/api/repairs/{repair_id}/approve")
    async def approve_repair(repair_id: str) -> dict[str, Any]:
        """Re-run the validation gate and apply a proposed repair patch if valid."""
        _sync_audit_repairs_to_store(app.state.auditor, store)
        proposal = store.get_repair(repair_id)
        if proposal is None:
            raise HTTPException(
                status_code=404, detail=f"Repair proposal '{repair_id}' not found"
            )
        if proposal["status"] in ("applied", "rejected"):
            raise HTTPException(
                status_code=409,
                detail=f"Repair '{repair_id}' cannot be approved from status '{proposal['status']}'",
            )

        validation = await validate_connector_config(
            raw_yaml=proposal["proposed_yaml"],
            probe_endpoint=True,
            client=app.state.http_client,
        )
        validation_payload = {
            "valid": validation.valid,
            "errors": list(validation.errors),
            "warnings": list(validation.warnings),
            "sample_counts": dict(validation.sample_counts),
        }

        if not validation.valid:
            store.update_repair_status(
                repair_id=repair_id,
                status="escalated",
                validation_results=validation_payload,
            )
            app.state.auditor.record(
                AuditEntry.create(
                    action="repair",
                    connector_name=proposal["connector"],
                    attempts=proposal["attempts"],
                    accepted=False,
                    applied=False,
                    reason=f"Approval refused by validation gate ({repair_id})",
                    validation_errors=validation.errors,
                    diff_text=proposal["diff"],
                    before_yaml=proposal["before_yaml"],
                    after_yaml=None,
                    metadata={"repair_id": repair_id},
                )
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Validation gate refused repair approval",
                    "errors": validation.errors,
                    "validation_results": validation_payload,
                },
            )

        discovered = _discover_connectors(app.state.connectors_dir)
        connector_name = proposal["connector"]
        if connector_name not in discovered:
            raise HTTPException(
                status_code=404,
                detail=f"Target connector config '{connector_name}' not found on disk",
            )
        target_path, _ = discovered[connector_name]
        base_dir = app.state.connectors_dir.resolve()
        resolved_target = target_path.resolve()
        if not resolved_target.is_relative_to(base_dir):
            raise HTTPException(
                status_code=400,
                detail="Resolved connector path escapes configured connectors directory",
            )

        resolved_target.write_text(
            proposal["proposed_yaml"].strip() + "\n", encoding="utf-8"
        )
        updated = store.update_repair_status(
            repair_id=repair_id,
            status="applied",
            validation_results=validation_payload,
        )
        app.state.auditor.record(
            AuditEntry.create(
                action="repair",
                connector_name=connector_name,
                attempts=proposal["attempts"],
                accepted=True,
                applied=True,
                reason=f"Repair approved and applied via control API ({repair_id})",
                diff_text=proposal["diff"],
                before_yaml=proposal["before_yaml"],
                after_yaml=proposal["proposed_yaml"],
                metadata={
                    "repair_id": repair_id,
                    "sample_counts": validation.sample_counts,
                },
            )
        )
        assert updated is not None
        return updated

    @app.post("/api/repairs/{repair_id}/reject")
    async def reject_repair(repair_id: str) -> dict[str, Any]:
        """Reject a repair proposal without modifying connector configs."""
        _sync_audit_repairs_to_store(app.state.auditor, store)
        proposal = store.get_repair(repair_id)
        if proposal is None:
            raise HTTPException(
                status_code=404, detail=f"Repair proposal '{repair_id}' not found"
            )
        if proposal["status"] == "applied":
            raise HTTPException(
                status_code=409,
                detail=f"Repair '{repair_id}' has already been applied",
            )

        updated = store.update_repair_status(repair_id=repair_id, status="rejected")
        app.state.auditor.record(
            AuditEntry.create(
                action="repair",
                connector_name=proposal["connector"],
                attempts=proposal["attempts"],
                accepted=False,
                applied=False,
                reason=f"Repair rejected via control API ({repair_id})",
                diff_text=proposal["diff"],
                before_yaml=proposal["before_yaml"],
                after_yaml=None,
                metadata={"repair_id": repair_id},
            )
        )
        assert updated is not None
        return updated

    @app.get("/api/audit")
    async def list_audit_entries(
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        """List AI generation and repair audit entries ordered newest first."""
        entries = list(reversed(app.state.auditor.read_all()))
        return [entry.to_dict() for entry in entries[:limit]]

    @app.get("/api/benchmarks")
    async def get_benchmarks() -> dict[str, Any]:
        """Return throughput benchmark CSV rows and AI evaluation metrics as JSON."""
        return _parse_benchmarks(app.state.benchmarks_dir)

    return app


app = create_control_app()


def run_control_server(
    host: str = "127.0.0.1",
    port: int = 8100,
    db_path: str | Path = Path("connector.duckdb"),
    connectors_dir: Path | None = None,
    audit_log_path: Path = Path("ai_audit.jsonl"),
) -> None:
    """Start the FastAPI control API server via uvicorn."""
    control_app = create_control_app(
        db_path=db_path,
        connectors_dir=connectors_dir,
        audit_log_path=audit_log_path,
    )
    uvicorn.run(control_app, host=host, port=port)
