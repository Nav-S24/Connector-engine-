"""Tests for the Phase 4 automated connector validation gate."""

from __future__ import annotations

import httpx
import pytest

from connector.ai.validator import (
    strip_markdown_fences,
    validate_connector_config,
    validate_yaml_schema,
)
from mock_server.app import create_app


def test_validate_yaml_schema_catches_syntax_and_pydantic_errors() -> None:
    """Invalid YAML syntax, non-mapping docs, and bad schema fields fail cleanly."""
    cfg, errs = validate_yaml_schema("```yaml\n  bad: [\n```")
    assert cfg is None
    assert errs and "syntax" in errs[0].lower()

    cfg2, errs2 = validate_yaml_schema("- just\n- a\n- list")
    assert cfg2 is None
    assert "mapping" in errs2[0].lower()

    bad_pk_yaml = """
name: broken
base_url: http://localhost:8000/api
auth:
  type: bearer
  env_var: API_TOKEN
streams:
  - name: items
    path: /v1/items
    records_path: data
    primary_key: missing_id
    pagination:
      type: offset
    schema:
      name: string
"""
    cfg3, errs3 = validate_yaml_schema(bad_pk_yaml)
    assert cfg3 is None
    assert any("primary_key" in err for err in errs3)


def test_strip_markdown_fences() -> None:
    """strip_markdown_fences unwraps fenced YAML blocks."""
    fenced = "```yaml\nname: test\n```"
    assert strip_markdown_fences(fenced) == "name: test"


async def test_validator_live_probe_succeeds_for_paystub_and_sheetsy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation gate succeeds end-to-end against API-key and OAuth2 mock endpoints."""
    monkeypatch.setenv("PAYSTUB_API_KEY", "paystub-test-key")
    monkeypatch.setenv("SHEETSY_CLIENT_ID", "sheetsy-client-id")
    monkeypatch.setenv("SHEETSY_CLIENT_SECRET", "sheetsy-client-secret")
    monkeypatch.setenv("SHEETSY_REFRESH_TOKEN", "sheetsy-refresh-token")

    mock_app = create_app(paystub_count=35, sheetsy_count=30)
    transport = httpx.ASGITransport(app=mock_app)

    paystub_yaml = """
name: paystub
base_url: http://mockserver/paystub
auth:
  type: api_key
  header: X-API-Key
  env_var: PAYSTUB_API_KEY
streams:
  - name: charges
    path: /v1/charges
    method: GET
    records_path: data
    primary_key: id
    pagination:
      type: cursor
      cursor_param: starting_after
      cursor_field: id
      page_size_param: limit
      page_size: 20
      has_more_path: has_more
    incremental:
      cursor_field: created
      request_param: created_gte
    schema:
      id: string
      amount: integer
      currency: string
      created: integer
"""

    sheetsy_yaml = """
name: sheetsy
base_url: http://mockserver/sheetsy
auth:
  type: oauth2
  token_url: http://mockserver/sheetsy/oauth/token
  client_id_env_var: SHEETSY_CLIENT_ID
  client_secret_env_var: SHEETSY_CLIENT_SECRET
  refresh_token_env_var: SHEETSY_REFRESH_TOKEN
streams:
  - name: sheets
    path: /v1/sheets
    method: GET
    records_path: rows
    primary_key: id
    pagination:
      type: link_header
      page_size_param: limit
      page_size: 15
    schema:
      id: string
      title: string
      row_count: integer
      updated_at: string
"""

    async with httpx.AsyncClient(transport=transport) as client:
        res1 = await validate_connector_config(
            paystub_yaml, probe_endpoint=True, client=client
        )
        assert res1.valid is True
        assert res1.sample_counts["charges"] == 35
        assert res1.config is not None

        res2 = await validate_connector_config(
            sheetsy_yaml, probe_endpoint=True, client=client
        )
        assert res2.valid is True
        assert res2.sample_counts["sheets"] == 30


async def test_validator_live_probe_rejects_bad_records_path_type_and_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation gate reports clear diagnostics for probe failures."""
    monkeypatch.setenv("GITLIKE_TOKEN", "gitlike-test-token")
    mock_app = create_app(gitlike_count=20)
    transport = httpx.ASGITransport(app=mock_app)

    wrong_records_path = """
name: gitlike
base_url: http://mockserver/gitlike
auth:
  type: bearer
  env_var: GITLIKE_TOKEN
streams:
  - name: repos
    path: /v1/repos
    records_path: wrong.items
    primary_key: id
    pagination:
      type: offset
      page_size: 10
    schema:
      id: integer
      name: string
"""

    wrong_field_type = """
name: gitlike
base_url: http://mockserver/gitlike
auth:
  type: bearer
  env_var: GITLIKE_TOKEN
streams:
  - name: repos
    path: /v1/repos
    records_path: result.items
    primary_key: id
    pagination:
      type: offset
      page_size: 10
      total_path: meta.nonexistent
    schema:
      id: integer
      stars: boolean
"""

    missing_env_auth = """
name: gitlike
base_url: http://mockserver/gitlike
auth:
  type: bearer
  env_var: MISSING_ENV_VAR_XYZ
streams:
  - name: repos
    path: /v1/repos
    records_path: result.items
    primary_key: id
    pagination:
      type: offset
      page_size: 10
    schema:
      id: integer
"""

    async with httpx.AsyncClient(transport=transport) as client:
        r1 = await validate_connector_config(
            wrong_records_path, probe_endpoint=True, client=client
        )
        assert r1.valid is False
        assert any("records_path" in e for e in r1.errors)

        r2 = await validate_connector_config(
            wrong_field_type, probe_endpoint=True, client=client
        )
        assert r2.valid is False
        assert any("total_path" in e for e in r2.errors)
        assert any("expected 'boolean'" in e for e in r2.errors)

        r3 = await validate_connector_config(
            missing_env_auth, probe_endpoint=True, client=client
        )
        assert r3.valid is False
        assert any("Authentication setup failed" in e for e in r3.errors)
