"""Schema drift detection and per-record validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from connector.config import FieldType


def infer_field_type(value: Any) -> FieldType | None:
    """Infer the declarative schema FieldType for a Python JSON value."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "any"


def are_types_compatible(expected: str, observed: str | None) -> bool:
    """Return True if an observed field type satisfies the expected schema type."""
    if observed is None or expected == "any":
        return True
    if expected == observed:
        return True
    if expected in ("float", "number") and observed in ("integer", "float", "number"):
        return True
    return False


@dataclass
class DriftReport:
    """Summary of schema differences between expected schema and an incoming batch."""

    stream_name: str
    added_fields: dict[str, str] = field(default_factory=dict)
    removed_fields: list[str] = field(default_factory=list)
    type_changes: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def has_drift(self) -> bool:
        """Return True if any added, removed, or retyped fields were detected."""
        return bool(
            self.added_fields or self.removed_fields or self.type_changes
        )

    @property
    def has_breaking_drift(self) -> bool:
        """Return True if fields were removed or changed type."""
        return bool(self.removed_fields or self.type_changes)

    def to_dict(self) -> dict[str, Any]:
        """Serialize drift report to a JSON-friendly dictionary."""
        return {
            "stream_name": self.stream_name,
            "added_fields": dict(self.added_fields),
            "removed_fields": list(self.removed_fields),
            "type_changes": {
                k: list(v) for k, v in self.type_changes.items()
            },
        }


def detect_drift(
    stream_name: str,
    expected_schema: dict[str, str],
    records: list[dict[str, Any]],
) -> DriftReport:
    """Compare a batch of records against expected_schema and report drift."""
    report = DriftReport(stream_name=stream_name)
    if not records:
        return report

    observed_keys: set[str] = set()
    observed_types: dict[str, list[FieldType]] = {}

    for rec in records:
        if not isinstance(rec, dict):
            continue
        for key, value in rec.items():
            observed_keys.add(key)
            inferred = infer_field_type(value)
            if inferred is not None:
                observed_types.setdefault(key, []).append(inferred)

    if not observed_keys:
        return report

    for key in sorted(observed_keys):
        if key not in expected_schema:
            inferred_list = observed_types.get(key, [])
            report.added_fields[key] = (
                inferred_list[0] if inferred_list else "string"
            )

    for expected_col, expected_type in expected_schema.items():
        if expected_col not in observed_keys:
            report.removed_fields.append(expected_col)
            continue
        for observed in observed_types.get(expected_col, []):
            if not are_types_compatible(expected_type, observed):
                report.type_changes[expected_col] = (expected_type, observed)
                break

    return report


def validate_record(
    record: Any,
    schema: dict[str, str],
    primary_key: str,
) -> str | None:
    """Validate one record against schema; return an error reason or None if valid."""
    if not isinstance(record, dict):
        return f"Expected dict record, got {type(record).__name__}"

    if primary_key not in record or record[primary_key] is None:
        return f"Missing or null primary key '{primary_key}'"

    for field_name, expected_type in schema.items():
        if field_name not in record:
            continue
        value = record[field_name]
        observed = infer_field_type(value)
        if not are_types_compatible(expected_type, observed):
            return (
                f"Field '{field_name}' expected '{expected_type}', "
                f"got '{observed}'"
            )

    return None
