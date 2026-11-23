"""Configurable fault injection and schema mutation for the FastAPI mock SaaS server."""

from __future__ import annotations

import asyncio
import copy
import os
import random
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException


@dataclass
class FaultConfig:
    """Runtime configuration for artificial latency and HTTP fault injection."""

    latency_ms: float = 0.0
    rate_429: float = 0.0
    rate_500: float = 0.0
    retry_after: str = "0.05"

    @classmethod
    def from_env(cls) -> FaultConfig:
        """Load default fault injection parameters from environment variables."""
        return cls(
            latency_ms=float(os.environ.get("MOCK_LATENCY_MS", "0")),
            rate_429=float(os.environ.get("MOCK_RATE_429", "0")),
            rate_500=float(os.environ.get("MOCK_RATE_500", "0")),
            retry_after=os.environ.get("MOCK_RETRY_AFTER", "0.05"),
        )


class FaultInjector:
    """Deterministic seeded injector for latency, 429s, and 500s."""

    def __init__(self, config: FaultConfig | None = None, seed: int = 42) -> None:
        self.config = config or FaultConfig.from_env()
        self._seed = seed
        self._rng = random.Random(seed)

    def reset(self, seed: int | None = None) -> None:
        """Reset the RNG state for reproducible fault sequences."""
        self._rng = random.Random(self._seed if seed is None else seed)

    async def maybe_inject(
        self,
        latency_ms: float | None = None,
        rate_429: float | None = None,
        rate_500: float | None = None,
    ) -> None:
        """Sleep for configured latency and probabilistically raise 429 or 500."""
        eff_latency = (
            latency_ms if latency_ms is not None else self.config.latency_ms
        )
        eff_429 = rate_429 if rate_429 is not None else self.config.rate_429
        eff_500 = rate_500 if rate_500 is not None else self.config.rate_500

        if eff_latency > 0:
            await asyncio.sleep(eff_latency / 1000.0)

        roll = self._rng.random()
        if eff_429 > 0 and roll < eff_429:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded (injected)",
                headers={"Retry-After": self.config.retry_after},
            )
        if eff_500 > 0 and roll < (eff_429 + eff_500):
            raise HTTPException(
                status_code=500,
                detail="Internal server error (injected)",
            )


def _coerce_value(value: Any, target_type: str) -> Any:
    """Convert a record field value to target_type for simulated breaking drift."""
    if value is None:
        return None
    if target_type == "string":
        return str(value)
    if target_type == "integer":
        return int(value)
    if target_type in ("float", "number"):
        return float(value)
    if target_type == "boolean":
        return bool(value)
    return value


@dataclass
class ServiceMutation:
    """Active schema and response-shape mutations for one mock SaaS service."""

    rename_fields: dict[str, str] = field(default_factory=dict)
    add_fields: dict[str, Any] = field(default_factory=dict)
    remove_fields: list[str] = field(default_factory=list)
    retype_fields: dict[str, str] = field(default_factory=dict)
    records_path: str | None = None
    cursor_param: str | None = None
    offset_param: str | None = None
    page_size_param: str | None = None
    has_more_field: str | None = None
    total_field: str | None = None

    def is_active(self) -> bool:
        """Return True if any mutation has been configured."""
        return bool(
            self.rename_fields
            or self.add_fields
            or self.remove_fields
            or self.retype_fields
            or self.records_path
            or self.cursor_param
            or self.offset_param
            or self.page_size_param
            or self.has_more_field
            or self.total_field
        )

    def transform_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply field additions, removals, renames, and retypes non-destructively."""
        if not (
            self.rename_fields
            or self.add_fields
            or self.remove_fields
            or self.retype_fields
        ):
            return records

        transformed: list[dict[str, Any]] = []
        for rec in records:
            item = copy.deepcopy(rec)
            for rem in self.remove_fields:
                item.pop(rem, None)
            for old_name, new_name in self.rename_fields.items():
                if old_name in item:
                    item[new_name] = item.pop(old_name)
            for field_name, target_type in self.retype_fields.items():
                if field_name in item:
                    item[field_name] = _coerce_value(item[field_name], target_type)
            for add_name, add_val in self.add_fields.items():
                item[add_name] = copy.deepcopy(add_val)
            transformed.append(item)
        return transformed


def set_nested_path(
    target: dict[str, Any], dotted_path: str, value: Any
) -> dict[str, Any]:
    """Place value inside target dictionary at dotted_path."""
    segments = [seg for seg in dotted_path.split(".") if seg]
    if not segments:
        return target
    cursor: dict[str, Any] = target
    for seg in segments[:-1]:
        nxt = cursor.get(seg)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[seg] = nxt
        cursor = nxt
    cursor[segments[-1]] = value
    return target
