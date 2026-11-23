"""Incremental sync bookmark state stores."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


class StateStore(ABC):
    """Abstract bookmark store for incremental stream syncs."""

    @abstractmethod
    def get_bookmark(self, connector_name: str, stream_name: str) -> Any | None:
        """Return the last persisted cursor value for a stream, or None."""

    @abstractmethod
    def set_bookmark(
        self,
        connector_name: str,
        stream_name: str,
        cursor_field: str,
        value: Any,
    ) -> None:
        """Persist the latest cursor value after a successful stream sync."""

    @abstractmethod
    def clear_bookmark(self, connector_name: str, stream_name: str) -> None:
        """Delete any stored bookmark for a stream."""


class InMemoryStateStore(StateStore):
    """In-memory bookmark store useful for unit tests and ephemeral runs."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], Any] = {}

    def get_bookmark(self, connector_name: str, stream_name: str) -> Any | None:
        """Return stored bookmark from memory."""
        return self._data.get((connector_name, stream_name))

    def set_bookmark(
        self,
        connector_name: str,
        stream_name: str,
        cursor_field: str,
        value: Any,
    ) -> None:
        """Save bookmark in memory."""
        self._data[(connector_name, stream_name)] = value

    def clear_bookmark(self, connector_name: str, stream_name: str) -> None:
        """Remove bookmark from memory."""
        self._data.pop((connector_name, stream_name), None)


class DuckDBStateStore(StateStore):
    """DuckDB-backed persistent bookmark store."""

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection | None = None,
        db_path: str | Path = "connector.duckdb",
    ) -> None:
        self._owns_conn = conn is None
        self.conn = conn if conn is not None else duckdb.connect(str(db_path))
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _connector_state (
                connector_name VARCHAR NOT NULL,
                stream_name VARCHAR NOT NULL,
                cursor_field VARCHAR NOT NULL,
                cursor_value VARCHAR NOT NULL,
                updated_at VARCHAR NOT NULL,
                PRIMARY KEY (connector_name, stream_name)
            )
            """
        )

    def get_bookmark(self, connector_name: str, stream_name: str) -> Any | None:
        """Return the stored cursor value decoded from JSON, or None."""
        row = self.conn.execute(
            """
            SELECT cursor_value
            FROM _connector_state
            WHERE connector_name = ? AND stream_name = ?
            """,
            [connector_name, stream_name],
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set_bookmark(
        self,
        connector_name: str,
        stream_name: str,
        cursor_field: str,
        value: Any,
    ) -> None:
        """Upsert the cursor bookmark for a stream."""
        encoded = json.dumps(value)
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO _connector_state (
                connector_name, stream_name, cursor_field, cursor_value, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (connector_name, stream_name) DO UPDATE SET
                cursor_field = excluded.cursor_field,
                cursor_value = excluded.cursor_value,
                updated_at = excluded.updated_at
            """,
            [connector_name, stream_name, cursor_field, encoded, now],
        )

    def clear_bookmark(self, connector_name: str, stream_name: str) -> None:
        """Delete the stored bookmark for a stream."""
        self.conn.execute(
            """
            DELETE FROM _connector_state
            WHERE connector_name = ? AND stream_name = ?
            """,
            [connector_name, stream_name],
        )

    def close(self) -> None:
        """Close the underlying DuckDB connection if owned by this store."""
        if self._owns_conn:
            self.conn.close()
