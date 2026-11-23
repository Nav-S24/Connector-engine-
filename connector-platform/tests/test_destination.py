"""Unit tests for DuckDB destination, schema evolution, dead-letters, and summaries."""

from __future__ import annotations

import pytest

from connector.destination import DuckDBDestination


def test_duckdb_upsert_is_idempotent() -> None:
    """Upserting the same primary keys updates values with zero duplicates."""
    schema = {
        "id": "string",
        "amount": "integer",
        "currency": "string",
        "active": "boolean",
        "meta": "object",
    }
    with DuckDBDestination(":memory:") as dest:
        dest.ensure_table("charges", schema=schema, primary_key="id")

        first_batch = [
            {
                "id": "ch_1",
                "amount": 100,
                "currency": "usd",
                "active": True,
                "meta": {"tier": "a"},
            },
            {
                "id": "ch_2",
                "amount": 250,
                "currency": "eur",
                "active": False,
                "meta": {"tier": "b"},
            },
        ]
        assert dest.upsert("charges", first_batch, "id", schema) == 2
        assert dest.count("charges") == 2

        second_batch = [
            {
                "id": "ch_1",
                "amount": 150,
                "currency": "usd",
                "active": True,
                "meta": {"tier": "updated"},
            },
            {
                "id": "ch_1",
                "amount": 999,
                "currency": "usd",
                "active": False,
                "meta": {"tier": "latest"},
            },
            {
                "id": "ch_3",
                "amount": 500,
                "currency": "gbp",
                "active": True,
                "meta": None,
            },
        ]
        dest.upsert("charges", second_batch, "id", schema)
        assert dest.count("charges") == 3

        rows = {row["id"]: row for row in dest.fetch_all("charges")}
        assert rows["ch_1"]["amount"] == 999
        assert rows["ch_1"]["active"] is False
        assert rows["ch_3"]["amount"] == 500


def test_duckdb_add_columns_dead_letters_and_summaries() -> None:
    """Destination evolves columns, writes dead letters, and stores run summaries."""
    with DuckDBDestination(":memory:") as dest:
        dest.ensure_table("items", {"id": "string"}, "id")
        dest.add_columns("items", {"note": "string", "score": "integer"})
        cols = dest.get_table_columns("items")
        assert "note" in cols
        assert "score" in cols

        dl_count = dest.write_dead_letters(
            "demo", "items", [({"bad": 1}, "Missing primary key")]
        )
        assert dl_count == 1
        dls = dest.fetch_dead_letters("demo")
        assert len(dls) == 1
        assert dls[0]["error_reason"] == "Missing primary key"

        dest.write_run_summary(
            {
                "run_id": "run-1",
                "connector_name": "demo",
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:00:01Z",
                "duration_seconds": 1.0,
                "status": "success",
                "rows_synced": 10,
                "rows_dead_lettered": 1,
                "retries": 0,
                "drift_events_count": 0,
                "drift_details": [],
            }
        )
        summaries = dest.fetch_run_summaries("demo")
        assert len(summaries) == 1
        assert summaries[0]["rows_synced"] == 10


def test_duckdb_rejects_invalid_identifiers_and_missing_pk() -> None:
    """SQL identifier validation and missing primary key checks raise ValueError."""
    with DuckDBDestination(":memory:") as dest:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            dest.ensure_table("bad-table; DROP TABLE x", {"id": "string"}, "id")

        dest.ensure_table("items", {"id": "string"}, "id")
        with pytest.raises(ValueError, match="primary key"):
            dest.upsert("items", [{"name": "missing"}], "id", {"id": "string"})
