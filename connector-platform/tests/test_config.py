"""Unit tests for connector YAML config loading and Pydantic validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from connector.config import extract_nested, load_config, load_config_from_yaml


def test_load_sample_configs() -> None:
    """Both paystub.yaml and gitlike.yaml pass schema validation."""
    root = Path(__file__).resolve().parents[1] / "connectors"
    paystub = load_config(root / "paystub.yaml")
    gitlike = load_config(root / "gitlike.yaml")

    assert paystub.name == "paystub"
    assert paystub.auth.type == "api_key"
    assert paystub.streams[0].pagination.type == "cursor"
    assert paystub.streams[0].incremental is not None

    assert gitlike.name == "gitlike"
    assert gitlike.auth.type == "bearer"
    assert gitlike.streams[0].pagination.type == "offset"
    assert gitlike.streams[0].records_path == "result.items"


def test_primary_key_must_exist_in_schema() -> None:
    """Validation rejects a stream whose primary_key is missing from schema."""
    bad_yaml = """
name: demo
base_url: https://api.example.com
auth:
  type: bearer
  env_var: DEMO_TOKEN
streams:
  - name: items
    path: /items
    primary_key: item_id
    pagination:
      type: offset
    schema:
      id: string
"""
    with pytest.raises(ValidationError, match="primary_key"):
        load_config_from_yaml(bad_yaml)


def test_duplicate_stream_names_rejected() -> None:
    """Validation rejects duplicate stream names within one connector."""
    bad_yaml = """
name: demo
base_url: https://api.example.com
auth:
  type: bearer
  env_var: DEMO_TOKEN
streams:
  - name: items
    path: /items1
    primary_key: id
    pagination:
      type: offset
    schema:
      id: string
  - name: items
    path: /items2
    primary_key: id
    pagination:
      type: offset
    schema:
      id: string
"""
    with pytest.raises(ValidationError, match="unique"):
        load_config_from_yaml(bad_yaml)


def test_invalid_base_url_rejected() -> None:
    """Validation rejects non-http(s) base URLs."""
    bad_yaml = """
name: demo
base_url: ftp://api.example.com
auth:
  type: bearer
  env_var: DEMO_TOKEN
streams:
  - name: items
    path: /items
    primary_key: id
    pagination:
      type: offset
    schema:
      id: string
"""
    with pytest.raises(ValidationError, match="base_url"):
        load_config_from_yaml(bad_yaml)


def test_extract_nested_paths() -> None:
    """extract_nested resolves root, dotted keys, list indices, and missing paths."""
    payload = {"result": {"items": [{"id": 1}, {"id": 2}]}, "meta": {"total": 2}}
    assert extract_nested(payload, "") == payload
    assert extract_nested(payload, "result.items") == [{"id": 1}, {"id": 2}]
    assert extract_nested(payload, "result.items.1.id") == 2
    assert extract_nested(payload, "result.missing") is None
    assert extract_nested(payload, "result.items.99") is None
