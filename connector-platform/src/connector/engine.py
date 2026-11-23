"""Core connector execution engine with concurrency, pooling, and batched upserts."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from connector.auth import AuthStrategy, build_auth
from connector.config import (
    ConnectorConfig,
    OffsetPaginationConfig,
    StreamConfig,
    extract_nested,
)
from connector.destination import Destination, DuckDBDestination
from connector.drift import DriftReport, detect_drift, validate_record
from connector.logging_utils import get_logger
from connector.pagination import OffsetPagination, build_pagination
from connector.retry import RetryConfig, TokenBucket, request_with_retry
from connector.state import DuckDBStateStore, InMemoryStateStore, StateStore


@dataclass
class StreamRunResult:
    """Execution summary for a single stream."""

    stream_name: str
    table_name: str
    rows_synced: int = 0
    rows_dead_lettered: int = 0
    pages_fetched: int = 0
    retries: int = 0
    drift_events: list[DriftReport] = field(default_factory=list)
    bookmark: Any | None = None
    duration_seconds: float = 0.0


@dataclass
class RunResult:
    """Execution summary for a complete connector run."""

    run_id: str
    connector_name: str
    streams: dict[str, StreamRunResult] = field(default_factory=dict)
    total_rows: int = 0
    total_dead_lettered: int = 0
    total_retries: int = 0
    drift_events: list[DriftReport] = field(default_factory=list)
    duration_seconds: float = 0.0
    status: Literal["success", "failed"] = "success"


def resolve_table_name(connector_name: str, stream_name: str) -> str:
    """Return the canonical destination table name for a connector stream."""
    return f"{connector_name}_{stream_name}"


class ConnectorEngine:
    """Executes declarative ConnectorConfig pipelines against a Destination."""

    def __init__(
        self,
        destination: Destination,
        state_store: StateStore | None = None,
        retry_config: RetryConfig | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        max_pages: int = 10000,
        max_concurrency: int | None = None,
        batch_size: int = 1000,
        use_pooling: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")

        self.destination = destination
        if state_store is not None:
            self.state_store = state_store
        elif isinstance(destination, DuckDBDestination):
            self.state_store = DuckDBStateStore(conn=destination.conn)
        else:
            self.state_store = InMemoryStateStore()
        self.retry_config = retry_config or RetryConfig()
        self._external_client = client
        self.timeout = timeout
        self.max_pages = max_pages
        self.max_concurrency = max_concurrency
        self.batch_size = batch_size
        self.use_pooling = use_pooling
        self.logger = logger or get_logger("connector.engine")
        self._dest_lock = asyncio.Lock()

    def _create_client(self, concurrency: int) -> httpx.AsyncClient:
        """Create an AsyncClient configured with or without connection pooling."""
        if self.use_pooling:
            limits = httpx.Limits(
                max_connections=max(20, concurrency * 2),
                max_keepalive_connections=max(10, concurrency),
            )
        else:
            limits = httpx.Limits(
                max_connections=1,
                max_keepalive_connections=0,
            )
        return httpx.AsyncClient(timeout=self.timeout, limits=limits)

    async def run(
        self,
        config: ConnectorConfig,
        stream_names: list[str] | None = None,
    ) -> RunResult:
        """Run selected streams concurrently and load records into destination."""
        run_id = str(uuid.uuid4())
        started_wall = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()

        selected_streams = self._select_streams(config, stream_names)
        result = RunResult(run_id=run_id, connector_name=config.name)
        concurrency = self.max_concurrency or config.max_concurrency
        semaphore = asyncio.Semaphore(concurrency)

        owns_client = self._external_client is None
        client = (
            self._external_client
            if self._external_client is not None
            else self._create_client(concurrency)
        )
        rate_limiter = (
            TokenBucket(rate=config.rate_limit) if config.rate_limit else None
        )
        auth = build_auth(config.auth, client=client)

        self.logger.info(
            "Starting connector run",
            extra={
                "event": "run_started",
                "run_id": run_id,
                "connector": config.name,
                "streams": [s.name for s in selected_streams],
                "concurrency": concurrency,
                "batch_size": self.batch_size,
            },
        )

        try:
            stream_results = await asyncio.gather(
                *(
                    self._run_stream(
                        client=client,
                        config=config,
                        stream=stream,
                        auth=auth,
                        rate_limiter=rate_limiter,
                        semaphore=semaphore,
                        concurrency=concurrency,
                        run_id=run_id,
                    )
                    for stream in selected_streams
                )
            )
            for stream_result in stream_results:
                result.streams[stream_result.stream_name] = stream_result
                result.total_rows += stream_result.rows_synced
                result.total_dead_lettered += stream_result.rows_dead_lettered
                result.total_retries += stream_result.retries
                result.drift_events.extend(stream_result.drift_events)
        except Exception as exc:
            result.status = "failed"
            self.logger.error(
                "Connector run failed",
                extra={
                    "event": "run_failed",
                    "run_id": run_id,
                    "connector": config.name,
                    "error": str(exc),
                },
            )
            raise
        finally:
            result.duration_seconds = round(time.perf_counter() - started, 4)
            finished_wall = datetime.now(timezone.utc).isoformat()
            async with self._dest_lock:
                self.destination.write_run_summary(
                    {
                        "run_id": run_id,
                        "connector_name": config.name,
                        "started_at": started_wall,
                        "finished_at": finished_wall,
                        "duration_seconds": result.duration_seconds,
                        "status": result.status,
                        "rows_synced": result.total_rows,
                        "rows_dead_lettered": result.total_dead_lettered,
                        "retries": result.total_retries,
                        "drift_events_count": len(result.drift_events),
                        "drift_details": [d.to_dict() for d in result.drift_events],
                    }
                )
            self.logger.info(
                "Finished connector run",
                extra={
                    "event": "run_finished",
                    "run_id": run_id,
                    "connector": config.name,
                    "status": result.status,
                    "rows_synced": result.total_rows,
                    "rows_dead_lettered": result.total_dead_lettered,
                    "retries": result.total_retries,
                    "drift_events_count": len(result.drift_events),
                    "duration_seconds": result.duration_seconds,
                },
            )
            if owns_client:
                await client.aclose()

        return result

    async def check_drift(
        self,
        config: ConnectorConfig,
        stream_names: list[str] | None = None,
    ) -> dict[str, DriftReport]:
        """Fetch a sample page for each stream and return schema drift reports."""
        selected_streams = self._select_streams(config, stream_names)
        concurrency = self.max_concurrency or config.max_concurrency
        owns_client = self._external_client is None
        client = (
            self._external_client
            if self._external_client is not None
            else self._create_client(concurrency)
        )
        rate_limiter = (
            TokenBucket(rate=config.rate_limit) if config.rate_limit else None
        )
        auth = build_auth(config.auth, client=client)
        reports: dict[str, DriftReport] = {}

        try:
            for stream in selected_streams:
                pagination = build_pagination(stream.pagination)
                url = f"{config.base_url.rstrip('/')}/{stream.path.lstrip('/')}"
                merged_params = {**stream.params, **pagination.initial_params()}
                response = await request_with_retry(
                    client=client,
                    method=stream.method,
                    url=url,
                    headers=await auth.get_headers(),
                    params=merged_params,
                    retry_config=self.retry_config,
                    rate_limiter=rate_limiter,
                    on_unauthorized=auth.refresh_on_unauthorized,
                )
                payload = response.json()
                raw_records = extract_nested(payload, stream.records_path)
                if not isinstance(raw_records, list):
                    raise ValueError(
                        f"Expected list at records_path '{stream.records_path}' "
                        f"for stream '{stream.name}'"
                    )
                dict_records = [r for r in raw_records if isinstance(r, dict)]
                reports[stream.name] = detect_drift(
                    stream_name=stream.name,
                    expected_schema=dict(stream.schema_),
                    records=dict_records,
                )
        finally:
            if owns_client:
                await client.aclose()

        return reports

    def _select_streams(
        self, config: ConnectorConfig, stream_names: list[str] | None
    ) -> list[StreamConfig]:
        """Filter streams when stream_names is specified."""
        if not stream_names:
            return config.streams
        wanted = set(stream_names)
        selected = [s for s in config.streams if s.name in wanted]
        missing = wanted - {s.name for s in selected}
        if missing:
            raise ValueError(
                f"Unknown stream(s) for connector '{config.name}': {sorted(missing)}"
            )
        return selected

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        stream: StreamConfig,
        url: str,
        auth: AuthStrategy,
        params: dict[str, Any],
        rate_limiter: TokenBucket | None,
        semaphore: asyncio.Semaphore,
        stream_result: StreamRunResult,
    ) -> tuple[httpx.Response, Any, list[Any], list[dict[str, Any]]]:
        """Fetch one page bounded by semaphore and extract records."""

        def _track_retry(_attempt: int, _delay: float, _status: int | None) -> None:
            stream_result.retries += 1

        async with semaphore:
            response = await request_with_retry(
                client=client,
                method=stream.method,
                url=url,
                headers=await auth.get_headers(),
                params=params,
                retry_config=self.retry_config,
                on_retry=_track_retry,
                rate_limiter=rate_limiter,
                on_unauthorized=auth.refresh_on_unauthorized,
            )

        payload = response.json()
        raw_records = extract_nested(payload, stream.records_path)
        if not isinstance(raw_records, list):
            raise ValueError(
                f"Expected list at records_path '{stream.records_path}' "
                f"for stream '{stream.name}', got {type(raw_records).__name__}"
            )
        dict_records = [r for r in raw_records if isinstance(r, dict)]
        return response, payload, raw_records, dict_records

    async def _process_page_records(
        self,
        config: ConnectorConfig,
        stream: StreamConfig,
        table: str,
        raw_records: list[Any],
        dict_records: list[dict[str, Any]],
        effective_schema: dict[str, str],
        record_buffer: list[dict[str, Any]],
        stream_result: StreamRunResult,
        run_id: str,
        max_cursor_box: list[Any],
    ) -> None:
        """Detect drift, route dead letters, buffer valid records, and flush batches."""
        async with self._dest_lock:
            drift = detect_drift(
                stream_name=stream.name,
                expected_schema=effective_schema,
                records=dict_records,
            )
            if drift.has_drift:
                if drift.added_fields:
                    self.destination.add_columns(table, drift.added_fields)
                    effective_schema.update(drift.added_fields)
                    self.logger.info(
                        "Schema drift: added new fields to destination",
                        extra={
                            "event": "schema_drift_added",
                            "run_id": run_id,
                            "connector": config.name,
                            "stream": stream.name,
                            "added_fields": drift.added_fields,
                        },
                    )
                if drift.has_breaking_drift:
                    self.logger.warning(
                        "Breaking schema drift detected",
                        extra={
                            "event": "schema_drift_breaking",
                            "run_id": run_id,
                            "connector": config.name,
                            "stream": stream.name,
                            "removed_fields": drift.removed_fields,
                            "type_changes": drift.type_changes,
                        },
                    )
                stream_result.drift_events.append(drift)

            dead_letters: list[tuple[Any, str]] = []
            for rec in raw_records:
                err = validate_record(
                    record=rec,
                    schema=effective_schema,
                    primary_key=stream.primary_key,
                )
                if err is not None:
                    dead_letters.append((rec, err))
                else:
                    record_buffer.append(rec)
                    if stream.incremental is not None:
                        candidate = rec.get(stream.incremental.cursor_field)
                        if candidate is not None and (
                            max_cursor_box[0] is None
                            or candidate > max_cursor_box[0]
                        ):
                            max_cursor_box[0] = candidate

            if dead_letters:
                dl_count = self.destination.write_dead_letters(
                    connector_name=config.name,
                    stream_name=stream.name,
                    items=dead_letters,
                )
                stream_result.rows_dead_lettered += dl_count
                self.logger.warning(
                    "Dead-lettered invalid records",
                    extra={
                        "event": "records_dead_lettered",
                        "run_id": run_id,
                        "connector": config.name,
                        "stream": stream.name,
                        "count": dl_count,
                    },
                )

            while len(record_buffer) >= self.batch_size:
                chunk = record_buffer[: self.batch_size]
                del record_buffer[: self.batch_size]
                synced = self.destination.upsert(
                    table_name=table,
                    records=chunk,
                    primary_key=stream.primary_key,
                    schema=effective_schema,
                )
                stream_result.rows_synced += synced

    async def _run_stream(
        self,
        client: httpx.AsyncClient,
        config: ConnectorConfig,
        stream: StreamConfig,
        auth: AuthStrategy,
        rate_limiter: TokenBucket | None,
        semaphore: asyncio.Semaphore,
        concurrency: int,
        run_id: str,
    ) -> StreamRunResult:
        """Paginate through a stream (concurrently for offset) and flush batches."""
        started = time.perf_counter()
        table = resolve_table_name(config.name, stream.name)
        effective_schema: dict[str, str] = dict(stream.schema_)

        async with self._dest_lock:
            self.destination.ensure_table(
                table_name=table,
                schema=effective_schema,
                primary_key=stream.primary_key,
            )
            existing_bookmark: Any | None = None
            if stream.incremental is not None:
                existing_bookmark = self.state_store.get_bookmark(
                    config.name, stream.name
                )

        incremental_params: dict[str, Any] = {}
        if stream.incremental is not None and existing_bookmark is not None:
            incremental_params[stream.incremental.request_param] = (
                existing_bookmark
            )

        url = f"{config.base_url.rstrip('/')}/{stream.path.lstrip('/')}"
        pagination = build_pagination(stream.pagination)
        stream_result = StreamRunResult(stream_name=stream.name, table_name=table)
        record_buffer: list[dict[str, Any]] = []
        max_cursor_box: list[Any] = [existing_bookmark]

        if isinstance(pagination, OffsetPagination) and concurrency > 1:
            await self._run_offset_concurrent(
                client=client,
                config=config,
                stream=stream,
                pagination=pagination,
                pag_cfg=stream.pagination,
                url=url,
                auth=auth,
                rate_limiter=rate_limiter,
                semaphore=semaphore,
                concurrency=concurrency,
                table=table,
                incremental_params=incremental_params,
                effective_schema=effective_schema,
                record_buffer=record_buffer,
                stream_result=stream_result,
                run_id=run_id,
                max_cursor_box=max_cursor_box,
            )
        else:
            page_params: dict[str, Any] | None = pagination.initial_params()
            while page_params is not None:
                if stream_result.pages_fetched >= self.max_pages:
                    raise RuntimeError(
                        f"Stream '{stream.name}' exceeded max_pages={self.max_pages}"
                    )
                merged_params = {
                    **stream.params,
                    **incremental_params,
                    **page_params,
                }
                response, payload, raw_records, dict_records = (
                    await self._fetch_page(
                        client=client,
                        stream=stream,
                        url=url,
                        auth=auth,
                        params=merged_params,
                        rate_limiter=rate_limiter,
                        semaphore=semaphore,
                        stream_result=stream_result,
                    )
                )
                await self._process_page_records(
                    config=config,
                    stream=stream,
                    table=table,
                    raw_records=raw_records,
                    dict_records=dict_records,
                    effective_schema=effective_schema,
                    record_buffer=record_buffer,
                    stream_result=stream_result,
                    run_id=run_id,
                    max_cursor_box=max_cursor_box,
                )
                stream_result.pages_fetched += 1
                page_params = pagination.next_params(
                    records=dict_records,
                    response_payload=payload,
                    current_params=page_params,
                    response_headers=response.headers,
                )

        async with self._dest_lock:
            if record_buffer:
                synced = self.destination.upsert(
                    table_name=table,
                    records=record_buffer,
                    primary_key=stream.primary_key,
                    schema=effective_schema,
                )
                stream_result.rows_synced += synced
                record_buffer.clear()

            if stream.incremental is not None and max_cursor_box[0] is not None:
                self.state_store.set_bookmark(
                    connector_name=config.name,
                    stream_name=stream.name,
                    cursor_field=stream.incremental.cursor_field,
                    value=max_cursor_box[0],
                )
                stream_result.bookmark = max_cursor_box[0]

        stream_result.duration_seconds = round(time.perf_counter() - started, 4)
        return stream_result

    async def _run_offset_concurrent(
        self,
        client: httpx.AsyncClient,
        config: ConnectorConfig,
        stream: StreamConfig,
        pagination: OffsetPagination,
        pag_cfg: OffsetPaginationConfig | Any,
        url: str,
        auth: AuthStrategy,
        rate_limiter: TokenBucket | None,
        semaphore: asyncio.Semaphore,
        concurrency: int,
        table: str,
        incremental_params: dict[str, Any],
        effective_schema: dict[str, str],
        record_buffer: list[dict[str, Any]],
        stream_result: StreamRunResult,
        run_id: str,
        max_cursor_box: list[Any],
    ) -> None:
        """Fetch offset-paginated pages concurrently after inspecting page 0."""
        assert isinstance(pag_cfg, OffsetPaginationConfig)
        first_page_params = pagination.initial_params()
        merged_first = {**stream.params, **incremental_params, **first_page_params}
        _, payload0, raw0, dicts0 = await self._fetch_page(
            client=client,
            stream=stream,
            url=url,
            auth=auth,
            params=merged_first,
            rate_limiter=rate_limiter,
            semaphore=semaphore,
            stream_result=stream_result,
        )
        await self._process_page_records(
            config=config,
            stream=stream,
            table=table,
            raw_records=raw0,
            dict_records=dicts0,
            effective_schema=effective_schema,
            record_buffer=record_buffer,
            stream_result=stream_result,
            run_id=run_id,
            max_cursor_box=max_cursor_box,
        )
        stream_result.pages_fetched += 1

        next_after_0 = pagination.next_params(dicts0, payload0, first_page_params)
        if next_after_0 is None:
            return

        page_size = pag_cfg.page_size
        total = (
            extract_nested(payload0, pag_cfg.total_path)
            if pag_cfg.total_path
            else None
        )

        if isinstance(total, (int, float)):
            total_count = int(total)
            remaining_offsets = list(range(page_size, total_count, page_size))
            if 1 + len(remaining_offsets) > self.max_pages:
                raise RuntimeError(
                    f"Stream '{stream.name}' exceeded max_pages={self.max_pages}"
                )

            async def _fetch_and_ingest(offset_val: int) -> None:
                params = {
                    **stream.params,
                    **incremental_params,
                    pag_cfg.offset_param: offset_val,
                    pag_cfg.limit_param: page_size,
                }
                _, _, raw_recs, dict_recs = await self._fetch_page(
                    client=client,
                    stream=stream,
                    url=url,
                    auth=auth,
                    params=params,
                    rate_limiter=rate_limiter,
                    semaphore=semaphore,
                    stream_result=stream_result,
                )
                await self._process_page_records(
                    config=config,
                    stream=stream,
                    table=table,
                    raw_records=raw_recs,
                    dict_records=dict_recs,
                    effective_schema=effective_schema,
                    record_buffer=record_buffer,
                    stream_result=stream_result,
                    run_id=run_id,
                    max_cursor_box=max_cursor_box,
                )
                stream_result.pages_fetched += 1

            await asyncio.gather(
                *(_fetch_and_ingest(off) for off in remaining_offsets)
            )
            return

        # Sliding speculative window when total_path is unavailable
        next_offset = page_size
        while True:
            if stream_result.pages_fetched + concurrency > self.max_pages:
                raise RuntimeError(
                    f"Stream '{stream.name}' exceeded max_pages={self.max_pages}"
                )
            window_offsets = [
                next_offset + i * page_size for i in range(concurrency)
            ]

            async def _fetch_window_page(
                offset_val: int,
            ) -> tuple[list[Any], list[dict[str, Any]]]:
                params = {
                    **stream.params,
                    **incremental_params,
                    pag_cfg.offset_param: offset_val,
                    pag_cfg.limit_param: page_size,
                }
                _, _, raw_recs, dict_recs = await self._fetch_page(
                    client=client,
                    stream=stream,
                    url=url,
                    auth=auth,
                    params=params,
                    rate_limiter=rate_limiter,
                    semaphore=semaphore,
                    stream_result=stream_result,
                )
                return raw_recs, dict_recs

            window_results = await asyncio.gather(
                *(_fetch_window_page(off) for off in window_offsets)
            )
            done = False
            for raw_recs, dict_recs in window_results:
                stream_result.pages_fetched += 1
                if raw_recs:
                    await self._process_page_records(
                        config=config,
                        stream=stream,
                        table=table,
                        raw_records=raw_recs,
                        dict_records=dict_recs,
                        effective_schema=effective_schema,
                        record_buffer=record_buffer,
                        stream_result=stream_result,
                        run_id=run_id,
                        max_cursor_box=max_cursor_box,
                    )
                if len(dict_recs) < page_size:
                    done = True
            if done:
                break
            next_offset += concurrency * page_size
