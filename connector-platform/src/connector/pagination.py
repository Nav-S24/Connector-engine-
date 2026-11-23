"""Pagination strategies behind a common interface."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlparse

from connector.config import (
    CursorPaginationConfig,
    LinkHeaderPaginationConfig,
    OffsetPaginationConfig,
    PaginationConfig,
    extract_nested,
)

_LINK_PART_RE = re.compile(r'<([^>]+)>\s*(?:;\s*(.+))?')
_REL_RE = re.compile(r'rel="?([^"\s;]+)"?')


class PaginationError(RuntimeError):
    """Raised when pagination encounters an invalid or non-advancing state."""


def parse_link_header(header_value: str | None) -> dict[str, str]:
    """Parse an RFC 5988 Link header into a mapping of rel -> URL."""
    if not header_value:
        return {}
    links: dict[str, str] = {}
    for segment in header_value.split(","):
        match = _LINK_PART_RE.match(segment.strip())
        if not match:
            continue
        url, params_part = match.group(1), match.group(2) or ""
        rel_match = _REL_RE.search(params_part)
        if rel_match:
            links[rel_match.group(1)] = url
    return links


class PaginationStrategy(ABC):
    """Abstract base class for stream pagination strategies."""

    @abstractmethod
    def initial_params(self) -> dict[str, Any]:
        """Return query parameters for the first page."""

    @abstractmethod
    def next_params(
        self,
        records: list[dict[str, Any]],
        response_payload: Any,
        current_params: dict[str, Any],
        response_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Return query parameters for the next page, or None when finished."""


class OffsetPagination(PaginationStrategy):
    """Offset/limit pagination strategy."""

    def __init__(self, config: OffsetPaginationConfig) -> None:
        self.config = config

    def initial_params(self) -> dict[str, Any]:
        """Start at offset 0 with the configured page size."""
        return {
            self.config.offset_param: 0,
            self.config.limit_param: self.config.page_size,
        }

    def next_params(
        self,
        records: list[dict[str, Any]],
        response_payload: Any,
        current_params: dict[str, Any],
        response_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Advance offset by the number of returned records until done."""
        if not records or len(records) < self.config.page_size:
            return None

        current_offset = int(current_params.get(self.config.offset_param, 0))
        next_offset = current_offset + len(records)

        if self.config.total_path:
            total = extract_nested(response_payload, self.config.total_path)
            if isinstance(total, (int, float)) and next_offset >= int(total):
                return None

        return {
            self.config.offset_param: next_offset,
            self.config.limit_param: self.config.page_size,
        }


class CursorPagination(PaginationStrategy):
    """Cursor-based pagination strategy."""

    def __init__(self, config: CursorPaginationConfig) -> None:
        self.config = config

    def initial_params(self) -> dict[str, Any]:
        """Request the first page with the configured page size."""
        return {self.config.page_size_param: self.config.page_size}

    def next_params(
        self,
        records: list[dict[str, Any]],
        response_payload: Any,
        current_params: dict[str, Any],
        response_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Advance cursor from payload or last record until pagination terminates."""
        if not records:
            return None

        if self.config.has_more_path:
            has_more = extract_nested(response_payload, self.config.has_more_path)
            if not bool(has_more):
                return None
        elif len(records) < self.config.page_size:
            return None

        if self.config.next_cursor_path:
            next_cursor = extract_nested(
                response_payload, self.config.next_cursor_path
            )
        else:
            next_cursor = records[-1].get(self.config.cursor_field)

        if next_cursor is None or next_cursor == "":
            return None

        previous_cursor = current_params.get(self.config.cursor_param)
        if previous_cursor is not None and next_cursor == previous_cursor:
            raise PaginationError(
                f"Cursor did not advance (stuck at '{next_cursor}')"
            )

        return {
            self.config.cursor_param: next_cursor,
            self.config.page_size_param: self.config.page_size,
        }


class LinkHeaderPagination(PaginationStrategy):
    """RFC 5988 Link header pagination strategy."""

    def __init__(self, config: LinkHeaderPaginationConfig) -> None:
        self.config = config

    def initial_params(self) -> dict[str, Any]:
        """Request the first page with the configured page size."""
        return {self.config.page_size_param: self.config.page_size}

    def next_params(
        self,
        records: list[dict[str, Any]],
        response_payload: Any,
        current_params: dict[str, Any],
        response_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Extract query params from the Link header's rel='next' URL."""
        if not records or not response_headers:
            return None

        link_header = None
        for key, value in response_headers.items():
            if key.lower() == "link":
                link_header = value
                break

        links = parse_link_header(link_header)
        next_url = links.get(self.config.rel)
        if not next_url:
            return None

        parsed = urlparse(next_url)
        query_dict = parse_qs(parsed.query)
        next_query: dict[str, Any] = {
            k: vals[0] if len(vals) == 1 else vals
            for k, vals in query_dict.items()
        }
        if not next_query:
            return None
        return next_query


def build_pagination(config: PaginationConfig) -> PaginationStrategy:
    """Instantiate a PaginationStrategy from a validated PaginationConfig."""
    if isinstance(config, OffsetPaginationConfig):
        return OffsetPagination(config)
    if isinstance(config, CursorPaginationConfig):
        return CursorPagination(config)
    if isinstance(config, LinkHeaderPaginationConfig):
        return LinkHeaderPagination(config)
    raise PaginationError(f"Unsupported pagination config: {config}")
