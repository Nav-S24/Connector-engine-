"""Unit tests for offset, cursor, and link-header pagination strategies."""

from __future__ import annotations

import pytest

from connector.config import (
    CursorPaginationConfig,
    LinkHeaderPaginationConfig,
    OffsetPaginationConfig,
)
from connector.pagination import (
    CursorPagination,
    LinkHeaderPagination,
    OffsetPagination,
    PaginationError,
    build_pagination,
    parse_link_header,
)


def test_offset_pagination_lifecycle() -> None:
    """Offset pagination advances by page_size and stops on partial/empty page."""
    strategy = build_pagination(
        OffsetPaginationConfig(
            type="offset",
            offset_param="offset",
            limit_param="limit",
            page_size=2,
            total_path="meta.total",
        )
    )
    assert isinstance(strategy, OffsetPagination)

    p0 = strategy.initial_params()
    assert p0 == {"offset": 0, "limit": 2}

    p1 = strategy.next_params([{"id": 1}, {"id": 2}], {"meta": {"total": 3}}, p0)
    assert p1 == {"offset": 2, "limit": 2}

    p2 = strategy.next_params([{"id": 3}], {"meta": {"total": 3}}, p1)
    assert p2 is None


def test_offset_pagination_stops_on_empty_or_exact_total() -> None:
    """Offset pagination stops on empty records or when offset reaches total."""
    strategy = OffsetPagination(
        OffsetPaginationConfig(type="offset", page_size=2, total_path="total")
    )
    assert strategy.next_params([], {"total": 2}, {"offset": 0, "limit": 2}) is None
    assert (
        strategy.next_params(
            [{"id": 1}, {"id": 2}], {"total": 2}, {"offset": 0, "limit": 2}
        )
        is None
    )


def test_cursor_pagination_with_has_more() -> None:
    """Cursor pagination uses last record cursor_field and respects has_more_path."""
    strategy = build_pagination(
        CursorPaginationConfig(
            type="cursor",
            cursor_param="starting_after",
            cursor_field="id",
            page_size_param="limit",
            page_size=2,
            has_more_path="has_more",
        )
    )
    assert isinstance(strategy, CursorPagination)

    p0 = strategy.initial_params()
    assert p0 == {"limit": 2}

    p1 = strategy.next_params(
        [{"id": "c1"}, {"id": "c2"}], {"has_more": True}, p0
    )
    assert p1 == {"starting_after": "c2", "limit": 2}

    p2 = strategy.next_params(
        [{"id": "c3"}], {"has_more": False}, p1
    )
    assert p2 is None


def test_cursor_pagination_empty_and_stuck_cursor() -> None:
    """Cursor pagination stops on empty response and errors if cursor is stuck."""
    strategy = CursorPagination(
        CursorPaginationConfig(
            type="cursor",
            cursor_param="after",
            cursor_field="id",
            page_size_param="limit",
            page_size=2,
            has_more_path="has_more",
        )
    )
    assert strategy.next_params([], {"has_more": True}, {"limit": 2}) is None

    with pytest.raises(PaginationError, match="did not advance"):
        strategy.next_params(
            [{"id": "c2"}],
            {"has_more": True},
            {"after": "c2", "limit": 2},
        )


def test_parse_link_header_and_link_pagination() -> None:
    """LinkHeaderPagination parses RFC 5988 rel='next' and stops when absent/empty."""
    header = (
        '<http://localhost:8000/sheetsy/v1/sheets?page_token=40&limit=40>; rel="next", '
        '<http://localhost:8000/sheetsy/v1/sheets?page_token=120&limit=40>; rel="last"'
    )
    parsed = parse_link_header(header)
    assert (
        parsed["next"]
        == "http://localhost:8000/sheetsy/v1/sheets?page_token=40&limit=40"
    )
    assert parse_link_header(None) == {}

    strategy = build_pagination(
        LinkHeaderPaginationConfig(type="link_header", page_size=40)
    )
    assert isinstance(strategy, LinkHeaderPagination)
    p0 = strategy.initial_params()
    assert p0 == {"limit": 40}

    p1 = strategy.next_params(
        [{"id": "s1"}],
        {"rows": [{"id": "s1"}]},
        p0,
        response_headers={"Link": header},
    )
    assert p1 == {"page_token": "40", "limit": "40"}

    # Stops on missing rel="next" or empty records
    assert (
        strategy.next_params(
            [{"id": "s2"}],
            {"rows": [{"id": "s2"}]},
            p1,
            response_headers={"Link": '<http://localhost/x>; rel="prev"'},
        )
        is None
    )
    assert (
        strategy.next_params(
            [], {"rows": []}, p1, response_headers={"Link": header}
        )
        is None
    )
