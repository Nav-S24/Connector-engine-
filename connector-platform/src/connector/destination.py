"""Destination interface and DuckDB warehouse implementation."""

from __future__ import annotations

import json
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

import duckdb

_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DUCKDB_TYPES: dict[str, str] = {
    "string": "VARCHAR",
    "integer": "BIGINT",
    "float": "DOUBLE",
    "number": "DOUBLE",
    "boolean": "BOOLEAN",
    "object": "VARCHAR",
    "array": "VARCHAR",
    "any": "VARCHAR",
}


def _validate_identifier(name: str) -> str:
    """Validate SQL table and column identifiers to prevent injection."""
    if not _SQL_IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: '{name}'")
    return name


def _serialize_value(value: Any, field_type: str) -> Any:
    """Normalize Python values for DuckDB parameter binding."""
    if value is None:
        return None
    if field_type in ("object", "array", "any") and isinstance(
        value, (dict, list)
    ):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


class Destination(ABC):
    """Abstract destination interface so additional warehouses can be plugged in."""

    @abstractmethod
    def ensure_table(
        self, table_name: str, schema: dict[str, str], primary_key: str
    ) -> None:
        """Create the destination table if it does not already exist."""

    @abstractmethod
    def add_columns(
        self, table_name: str, new_columns: dict[str, str]
    ) -> None:
        """Add new columns to an existing destination table."""

    @abstractmethod
    def get_table_columns(self, table_name: str) -> dict[str, str]:
        """Return existing column names and SQL types for table_name."""

    @abstractmethod
    def upsert(
        self,
        table_name: str,
        records: list[dict[str, Any]],
        primary_key: str,
        schema: dict[str, str],
    ) -> int:
        """Upsert records by primary_key and return the number of rows processed."""

    @abstractmethod
    def write_dead_letters(
        self,
        connector_name: str,
        stream_name: str,
        items: list[tuple[Any, str]],
    ) -> int:
        """Persist failed records and error reasons to the dead-letter store."""

    @abstractmethod
    def fetch_dead_letters(
        self, connector_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve dead-letter records, optionally filtered by connector."""

    @abstractmethod
    def write_run_summary(self, summary: dict[str, Any]) -> None:
        """Persist a connector run summary record."""

    @abstractmethod
    def fetch_run_summaries(
        self, connector_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve run summary records, optionally filtered by connector."""

    @abstractmethod
    def count(self, table_name: str) -> int:
        """Return total rows currently stored in table_name."""

    @abstractmethod
    def fetch_all(self, table_name: str) -> list[dict[str, Any]]:
        """Return all rows from table_name as dictionaries."""

    @abstractmethod
    def close(self) -> None:
        """Release underlying connection resources."""


class DuckDBDestination(Destination):
    """DuckDB destination supporting upserts, schema evolution, dead-letters, and summaries."""

    def __init__(self, db_path: str | Path = "connector.duckdb") -> None:
        self.db_path = str(db_path)
        self.conn = duckdb.connect(self.db_path)
        self._ensure_system_tables()

    def _ensure_system_tables(self) -> None:
        """Create internal metadata tables for dead-letter records and run summaries."""
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _dead_letter (
                id VARCHAR PRIMARY KEY,
                connector_name VARCHAR NOT NULL,
                stream_name VARCHAR NOT NULL,
                error_reason VARCHAR NOT NULL,
                raw_record VARCHAR NOT NULL,
                recorded_at VARCHAR NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _run_summary (
                run_id VARCHAR PRIMARY KEY,
                connector_name VARCHAR NOT NULL,
                started_at VARCHAR NOT NULL,
                finished_at VARCHAR NOT NULL,
                duration_seconds DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                rows_synced BIGINT NOT NULL,
                rows_dead_lettered BIGINT NOT NULL,
                retries BIGINT NOT NULL,
                drift_events_count BIGINT NOT NULL,
                drift_details VARCHAR NOT NULL
            )
            """
        )

    def __enter__(self) -> DuckDBDestination:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def ensure_table(
        self, table_name: str, schema: dict[str, str], primary_key: str
    ) -> None:
        """Create the destination table in DuckDB with a primary key constraint."""
        safe_table = _validate_identifier(table_name)
        safe_pk = _validate_identifier(primary_key)
        if safe_pk not in schema:
            raise ValueError(
                f"Primary key '{safe_pk}' must exist in schema for table '{safe_table}'"
            )

        col_defs: list[str] = []
        for col, field_type in schema.items():
            safe_col = _validate_identifier(col)
            sql_type = _DUCKDB_TYPES.get(field_type, "VARCHAR")
            if safe_col == safe_pk:
                col_defs.append(f'"{safe_col}" {sql_type} PRIMARY KEY')
            else:
                col_defs.append(f'"{safe_col}" {sql_type}')

        ddl = f'CREATE TABLE IF NOT EXISTS "{safe_table}" ({", ".join(col_defs)})'
        self.conn.execute(ddl)

    def add_columns(
        self, table_name: str, new_columns: dict[str, str]
    ) -> None:
        """Add new columns to an existing DuckDB table."""
        if not new_columns:
            return
        safe_table = _validate_identifier(table_name)
        for col, field_type in new_columns.items():
            safe_col = _validate_identifier(col)
            sql_type = _DUCKDB_TYPES.get(field_type, "VARCHAR")
            self.conn.execute(
                f'ALTER TABLE "{safe_table}" ADD COLUMN IF NOT EXISTS "{safe_col}" {sql_type}'
            )

    def get_table_columns(self, table_name: str) -> dict[str, str]:
        """Return existing column names and SQL types for table_name."""
        safe_table = _validate_identifier(table_name)
        rows = self.conn.execute(f'DESCRIBE "{safe_table}"').fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    def upsert(
        self,
        table_name: str,
        records: list[dict[str, Any]],
        primary_key: str,
        schema: dict[str, str],
    ) -> int:
        """Idempotently upsert records by primary key into DuckDB."""
        if not records:
            return 0

        safe_table = _validate_identifier(table_name)
        safe_pk = _validate_identifier(primary_key)
        columns = [_validate_identifier(col) for col in schema]

        deduped: dict[Any, dict[str, Any]] = {}
        for record in records:
            if safe_pk not in record or record[safe_pk] is None:
                raise ValueError(
                    f"Record missing required primary key '{safe_pk}'"
                )
            deduped[record[safe_pk]] = record

        unique_records = list(deduped.values())
        col_list = ", ".join(f'"{col}"' for col in columns)
        select_exprs = ", ".join(
            f"UNNEST(?::{_DUCKDB_TYPES.get(schema[col], 'VARCHAR')}[])"
            for col in columns
        )

        non_pk_cols = [col for col in columns if col != safe_pk]
        if non_pk_cols:
            updates = ", ".join(
                f'"{col}" = excluded."{col}"' for col in non_pk_cols
            )
            conflict_clause = (
                f'ON CONFLICT ("{safe_pk}") DO UPDATE SET {updates}'
            )
        else:
            conflict_clause = f'ON CONFLICT ("{safe_pk}") DO NOTHING'

        sql = (
            f'INSERT INTO "{safe_table}" ({col_list}) '
            f"SELECT {select_exprs} {conflict_clause}"
        )

        col_vectors = [
            [
                _serialize_value(rec.get(col), schema[col])
                for rec in unique_records
            ]
            for col in columns
        ]
        self.conn.execute(sql, col_vectors)
        return len(unique_records)

    def write_dead_letters(
        self,
        connector_name: str,
        stream_name: str,
        items: list[tuple[Any, str]],
    ) -> int:
        """Persist failed records and error reasons into _dead_letter."""
        if not items:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            [
                str(uuid.uuid4()),
                connector_name,
                stream_name,
                reason,
                json.dumps(record, default=str),
                now,
            ]
            for record, reason in items
        ]
        self.conn.executemany(
            """
            INSERT INTO _dead_letter (
                id, connector_name, stream_name, error_reason, raw_record, recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return len(rows)

    def fetch_dead_letters(
        self, connector_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve dead-letter rows from _dead_letter."""
        if connector_name is not None:
            cursor = self.conn.execute(
                "SELECT * FROM _dead_letter WHERE connector_name = ? ORDER BY recorded_at",
                [connector_name],
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM _dead_letter ORDER BY recorded_at"
            )
        col_names = [desc[0] for desc in cursor.description]
        return [dict(zip(col_names, row)) for row in cursor.fetchall()]

    def write_run_summary(self, summary: dict[str, Any]) -> None:
        """Insert a run summary row into _run_summary."""
        self.conn.execute(
            """
            INSERT INTO _run_summary (
                run_id, connector_name, started_at, finished_at, duration_seconds,
                status, rows_synced, rows_dead_lettered, retries,
                drift_events_count, drift_details
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                summary["run_id"],
                summary["connector_name"],
                summary["started_at"],
                summary["finished_at"],
                summary["duration_seconds"],
                summary["status"],
                summary["rows_synced"],
                summary["rows_dead_lettered"],
                summary["retries"],
                summary["drift_events_count"],
                json.dumps(summary["drift_details"]),
            ],
        )

    def fetch_run_summaries(
        self, connector_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve run summary rows from _run_summary."""
        if connector_name is not None:
            cursor = self.conn.execute(
                "SELECT * FROM _run_summary WHERE connector_name = ? ORDER BY started_at",
                [connector_name],
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM _run_summary ORDER BY started_at"
            )
        col_names = [desc[0] for desc in cursor.description]
        return [dict(zip(col_names, row)) for row in cursor.fetchall()]

    def count(self, table_name: str) -> int:
        """Return the number of rows in table_name."""
        safe_table = _validate_identifier(table_name)
        result = self.conn.execute(
            f'SELECT COUNT(*) FROM "{safe_table}"'
        ).fetchone()
        return int(result[0]) if result else 0

    def fetch_all(self, table_name: str) -> list[dict[str, Any]]:
        """Fetch all rows from table_name as a list of dictionaries."""
        safe_table = _validate_identifier(table_name)
        cursor = self.conn.execute(f'SELECT * FROM "{safe_table}"')
        col_names = [desc[0] for desc in cursor.description]
        return [dict(zip(col_names, row)) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the DuckDB connection."""
        self.conn.close()
