"""Unit tests for schema drift detection and per-record validation."""

from __future__ import annotations

from connector.drift import (
    are_types_compatible,
    detect_drift,
    infer_field_type,
    validate_record,
)


def test_infer_field_type_distinguishes_bool_from_int() -> None:
    """Booleans infer as 'boolean' rather than 'integer'."""
    assert infer_field_type(True) == "boolean"
    assert infer_field_type(42) == "integer"
    assert infer_field_type(3.14) == "float"
    assert infer_field_type("hello") == "string"
    assert infer_field_type({"a": 1}) == "object"
    assert infer_field_type([1, 2]) == "array"
    assert infer_field_type(None) is None
    assert are_types_compatible("float", "integer") is True
    assert are_types_compatible("integer", "boolean") is False


def test_detect_drift_added_removed_and_type_change() -> None:
    """detect_drift reports added fields, removed fields, and type changes."""
    expected = {"id": "string", "amount": "integer", "currency": "string"}
    records = [
        {"id": "ch_1", "amount": "100.50", "note": "vip"},
    ]
    report = detect_drift("charges", expected, records)
    assert report.has_drift is True
    assert report.has_breaking_drift is True
    assert report.added_fields == {"note": "string"}
    assert report.removed_fields == ["currency"]
    assert report.type_changes == {"amount": ("integer", "string")}


def test_detect_drift_empty_batch_no_false_positives() -> None:
    """An empty record batch must not report fields as removed."""
    report = detect_drift("charges", {"id": "string", "amount": "integer"}, [])
    assert report.has_drift is False


def test_validate_record_catches_bad_rows() -> None:
    """validate_record flags non-dicts, missing primary keys, and type mismatches."""
    schema = {"id": "string", "amount": "integer"}
    assert validate_record({"id": "1", "amount": 10}, schema, "id") is None
    assert validate_record({"id": "1", "amount": None}, schema, "id") is None
    assert "Expected dict" in (validate_record("bad", schema, "id") or "")
    assert "primary key" in (validate_record({"amount": 10}, schema, "id") or "")
    assert "expected 'integer'" in (
        validate_record({"id": "1", "amount": "oops"}, schema, "id") or ""
    )
