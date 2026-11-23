"""Tests for Phase 4 LLM connector generator, OpenAPI retrieval, and CLI."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from connector.ai.generator import (
    FakeLLMClient,
    GeminiLLMClient,
    chunk_spec,
    extract_yaml_from_response,
    generate_connector_config,
    retrieve_relevant_spec,
)
from connector.cli import app as cli_app
from mock_server.app import create_app


def _build_large_openapi_spec(num_filler_endpoints: int = 60) -> str:
    """Create a large (>80 KB) OpenAPI JSON spec with filler endpoints plus gitlike repos."""
    paths: dict[str, object] = {}
    for idx in range(num_filler_endpoints):
        paths[f"/v1/filler_resource_{idx}"] = {
            "get": {
                "operationId": f"listFiller{idx}",
                "summary": f"Filler endpoint number {idx} for unrelated billing telemetry",
                "parameters": [
                    {"name": "page", "in": "query", "schema": {"type": "integer"}}
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "notes": {
                                            "type": "string",
                                            "description": "Long filler documentation " * 25,
                                        }
                                    },
                                }
                            }
                        },
                    }
                },
            }
        }

    paths["/v1/repos"] = {
        "get": {
            "operationId": "listRepos",
            "summary": "List gitlike repositories with offset and limit pagination",
            "parameters": [
                {"name": "offset", "in": "query", "schema": {"type": "integer"}},
                {"name": "limit", "in": "query", "schema": {"type": "integer"}},
            ],
            "responses": {
                "200": {
                    "description": "Repository list",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/RepoListResponse"}
                        }
                    },
                }
            },
        }
    }

    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Gitlike Enterprise API", "version": "1.0.0"},
        "servers": [{"url": "http://mockserver/gitlike"}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Use env var GITLIKE_TOKEN",
                }
            },
            "schemas": {
                "Repo": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "stars": {"type": "integer"},
                        "private": {"type": "boolean"},
                        "updated_at": {"type": "string"},
                    },
                },
                "RepoListResponse": {
                    "type": "object",
                    "properties": {
                        "meta": {
                            "type": "object",
                            "properties": {"total": {"type": "integer"}},
                        },
                        "result": {
                            "type": "object",
                            "properties": {
                                "items": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/Repo"},
                                }
                            },
                        },
                    },
                },
            },
        },
        "paths": paths,
    }
    return json.dumps(spec, indent=2)


def test_large_openapi_retrieval_stays_within_budget_and_finds_target() -> None:
    """Retrieval chunks a large OpenAPI spec, keeps auth overview, and ranks target endpoint."""
    large_spec = _build_large_openapi_spec(num_filler_endpoints=60)
    assert len(large_spec) > 60_000

    chunks = chunk_spec(large_spec)
    assert len(chunks) > 50

    excerpt, chunk_ids = retrieve_relevant_spec(
        spec_text=large_spec,
        query="gitlike repos listRepos offset pagination",
        max_context_chars=6_000,
    )
    assert len(excerpt) <= 6_000
    assert "openapi_overview" in chunk_ids
    assert "GET /v1/repos" in chunk_ids
    assert "stars" in excerpt
    assert "updated_at" in excerpt
    assert "bearerAuth" in excerpt


def test_markdown_spec_chunking_and_retrieval() -> None:
    """Long Markdown API documentation chunks by headings/windows within budget."""
    sections = [
        "# Billing API Authentication\nUse X-API-Key header from ENV_KEY.\nBase URL: https://api.example.com",
    ]
    for idx in range(25):
        sections.append(
            f"## Resource {idx}\nUnrelated endpoint /v1/other_{idx}.\n"
            + ("Long filler paragraph describing resource fields.\n" * 20)
        )
    sections.append(
        "## Invoices Stream\nGET /v1/invoices returns `data` list with cursor `starting_after` and primary key `id`."
    )
    md_spec = "\n\n".join(sections)

    excerpt, chunk_ids = retrieve_relevant_spec(
        spec_text=md_spec,
        query="invoices cursor starting_after",
        max_context_chars=2_500,
    )
    assert len(excerpt) <= 2_500
    assert "Billing API Authentication" in excerpt
    assert "/v1/invoices" in excerpt
    assert len(chunk_ids) >= 2


def test_extract_yaml_from_response() -> None:
    """extract_yaml_from_response unwraps fenced YAML blocks or returns clean YAML."""
    wrapped = "Here is the config:\n```yaml\nname: demo\n```\n"
    assert extract_yaml_from_response(wrapped) == "name: demo"


async def test_generate_retry_loop_recovers_from_schema_and_probe_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation gate rejects bad LLM outputs and retries with feedback until valid."""
    monkeypatch.setenv("PAYSTUB_API_KEY", "paystub-test-key")
    mock_app = create_app(paystub_count=40)
    transport = httpx.ASGITransport(app=mock_app)

    # Attempt 1: invalid Pydantic schema (primary_key missing from schema)
    attempt_1 = """
```yaml
name: paystub
base_url: http://mockserver/paystub
auth:
  type: api_key
  header: X-API-Key
  env_var: PAYSTUB_API_KEY
streams:
  - name: charges
    path: /v1/charges
    records_path: data
    primary_key: missing_pk
    pagination:
      type: cursor
    schema:
      amount: integer
```
"""

    # Attempt 2: valid Pydantic schema, but wrong records_path caught by live probe
    attempt_2 = """
name: paystub
base_url: http://mockserver/paystub
auth:
  type: api_key
  header: X-API-Key
  env_var: PAYSTUB_API_KEY
streams:
  - name: charges
    path: /v1/charges
    records_path: items
    primary_key: id
    pagination:
      type: cursor
      cursor_param: starting_after
      cursor_field: id
      page_size_param: limit
      page_size: 20
      has_more_path: has_more
    schema:
      id: string
      amount: integer
      currency: string
      created: integer
"""

    # Attempt 3: fixed records_path, passes schema + live probe
    attempt_3 = """
```yaml
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
```
"""

    fake_llm = FakeLLMClient([attempt_1, attempt_2, attempt_3])
    spec_md = """
# Paystub API
Base URL: http://mockserver/paystub
Auth: X-API-Key header (`PAYSTUB_API_KEY`).

## GET /v1/charges
Returns JSON `{ "data": [...], "has_more": bool }` using cursor `starting_after` and `limit`.
"""

    async with httpx.AsyncClient(transport=transport) as client:
        result = await generate_connector_config(
            spec_text=spec_md,
            llm_client=fake_llm,
            connector_name="paystub",
            base_url="http://mockserver/paystub",
            stream_hints=["charges"],
            max_retries=3,
            probe_endpoint=True,
            http_client=client,
        )

    assert result.validation.valid is True
    assert result.attempts == 3
    assert result.config is not None
    assert result.config.streams[0].records_path == "data"
    assert len(fake_llm.calls) == 3
    # Confirm exact errors from prior attempts were fed back into subsequent prompts
    assert "primary_key" in str(fake_llm.calls[1]["prompt"])
    assert "records_path" in str(fake_llm.calls[2]["prompt"])


async def test_generate_exhausts_retries_and_blocks_invalid_config() -> None:
    """When retries are exhausted, validation gate fails and config remains None."""
    bad_yaml = "not_a_valid_connector: true\n"
    fake_llm = FakeLLMClient([bad_yaml, bad_yaml, bad_yaml])

    result = await generate_connector_config(
        spec_text="# Minimal API\nGET /v1/items",
        llm_client=fake_llm,
        max_retries=2,
        probe_endpoint=False,
    )

    assert result.validation.valid is False
    assert result.config is None
    assert result.attempts == 3
    assert len(result.history) == 3


def test_gemini_llm_client_reads_env_and_uses_injected_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GeminiLLMClient validates GEMINI_API_KEY, reads GEMINI_MODEL, and calls generate_content."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiLLMClient()

    recorded: dict[str, object] = {}

    class _StubModels:
        def generate_content(self, **kwargs: object) -> SimpleNamespace:
            recorded.update(kwargs)
            return SimpleNamespace(text="name: stub")

    stub_sdk = SimpleNamespace(models=_StubModels())
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-4-hydrogen")

    llm = GeminiLLMClient(client=stub_sdk)
    output = llm.generate("Generate config", system_instruction="YAML only")
    assert output == "name: stub"
    assert recorded["model"] == "gemini-4-hydrogen"
    assert recorded["contents"] == "Generate config"


def test_cli_generate_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """connector generate reads a spec, validates YAML via gate, and writes --output."""
    spec_file = tmp_path / "openapi.json"
    spec_file.write_text(_build_large_openapi_spec(num_filler_endpoints=5), encoding="utf-8")
    out_file = tmp_path / "generated_gitlike.yaml"

    valid_yaml = """
name: gitlike
base_url: http://localhost:8000/gitlike
auth:
  type: bearer
  env_var: GITLIKE_TOKEN
streams:
  - name: repos
    path: /v1/repos
    method: GET
    records_path: result.items
    primary_key: id
    pagination:
      type: offset
      offset_param: offset
      limit_param: limit
      page_size: 50
      total_path: meta.total
    schema:
      id: integer
      name: string
      stars: integer
      private: boolean
      updated_at: string
"""

    monkeypatch.setattr(
        "connector.cli._create_llm_client",
        lambda: FakeLLMClient([valid_yaml]),
    )

    runner = CliRunner()
    audit_path = tmp_path / "audit.jsonl"
    res = runner.invoke(
        cli_app,
        [
            "generate",
            str(spec_file),
            "--name",
            "gitlike",
            "--stream",
            "repos",
            "--no-probe",
            "--audit-log",
            str(audit_path),
            "--output",
            str(out_file),
        ],
    )
    assert res.exit_code == 0, res.output
    assert out_file.is_file()
    assert audit_path.is_file()
    assert "gitlike" in out_file.read_text(encoding="utf-8")

    # Missing spec file returns exit code 1
    missing_res = runner.invoke(cli_app, ["generate", str(tmp_path / "missing.yaml")])
    assert missing_res.exit_code == 1

    # Invalid output blocked by validation gate returns exit code 1
    monkeypatch.setattr(
        "connector.cli._create_llm_client",
        lambda: FakeLLMClient(["invalid: true"]),
    )
    bad_res = runner.invoke(
        cli_app,
        [
            "generate",
            str(spec_file),
            "--no-probe",
            "--max-retries",
            "1",
            "--audit-log",
            str(audit_path),
        ],
    )
    assert bad_res.exit_code == 1
