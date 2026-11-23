"""Unit tests for incremental sync bookmark state stores."""

from __future__ import annotations

from pathlib import Path

from connector.state import DuckDBStateStore, InMemoryStateStore


def test_in_memory_state_store() -> None:
    """InMemoryStateStore stores, updates, and clears bookmarks."""
    store = InMemoryStateStore()
    assert store.get_bookmark("paystub", "charges") is None
    store.set_bookmark("paystub", "charges", "created", 1700000100)
    assert store.get_bookmark("paystub", "charges") == 1700000100
    store.clear_bookmark("paystub", "charges")
    assert store.get_bookmark("paystub", "charges") is None


def test_duckdb_state_store_persistence(tmp_path: Path) -> None:
    """DuckDBStateStore persists typed bookmarks across store instances."""
    db_file = tmp_path / "state.duckdb"
    store1 = DuckDBStateStore(db_path=db_file)
    store1.set_bookmark("paystub", "charges", "created", 1700000999)
    store1.set_bookmark("sheetsy", "sheets", "updated_at", "2026-02-01T00:00:00Z")
    store1.close()

    store2 = DuckDBStateStore(db_path=db_file)
    assert store2.get_bookmark("paystub", "charges") == 1700000999
    assert (
        store2.get_bookmark("sheetsy", "sheets") == "2026-02-01T00:00:00Z"
    )
    store2.clear_bookmark("paystub", "charges")
    assert store2.get_bookmark("paystub", "charges") is None
    store2.close()
