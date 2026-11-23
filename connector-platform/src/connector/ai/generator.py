"""LLM-assisted connector YAML generator with spec retrieval and validation gate."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

import httpx
import yaml
from google import genai
from google.genai import types

from connector.ai.audit import AuditEntry, AuditLogger
from connector.ai.validator import (
    ValidationResult,
    strip_markdown_fences,
    validate_connector_config,
)
from connector.config import ConnectorConfig

DEFAULT_GEMINI_MODEL = "gemini-4-hydrogen"

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

SYSTEM_INSTRUCTION = """You generate declarative YAML configs for an async SaaS data-connector engine.

Output ONLY valid YAML (no markdown explanation, no prose).
Never hardcode secret keys or tokens; always reference environment variable names via `env_var`, `client_id_env_var`, `client_secret_env_var`, or `refresh_token_env_var`.

Schema rules:
- Top-level keys: `name` (SQL identifier `^[A-Za-z_][A-Za-z0-9_]*$`), `base_url` (`http://...` or `https://...`), optional `rate_limit` (float > 0), optional `max_concurrency` (int >= 1), `auth`, `streams` (non-empty list).
- `auth`:
  - `type: api_key` -> `header` (e.g. `X-API-Key`), `env_var`
  - `type: bearer` -> `env_var`
  - `type: oauth2` -> `token_url`, `client_id_env_var`, `client_secret_env_var`, `refresh_token_env_var`, optional `scope`
- Each item in `streams`:
  - `name` (SQL identifier), `path` (e.g. `/v1/charges`), `method` (`GET` or `POST`), `records_path` (dot-notation path to list of records, e.g. `data` or `result.items`), `primary_key` (must exist in `schema`), optional `params` (dict)
  - `pagination`:
    - `type: offset` -> `offset_param`, `limit_param`, `page_size`, optional `total_path`
    - `type: cursor` -> `cursor_param`, `cursor_field`, `page_size_param`, `page_size`, optional `has_more_path`, optional `next_cursor_path`
    - `type: link_header` -> `page_size_param`, `page_size`, optional `rel` (default `next`)
  - optional `incremental`: `cursor_field`, `request_param`
  - `schema`: mapping of SQL-safe column names to one of: `string`, `integer`, `float`, `number`, `boolean`, `object`, `array`, `any`.

Example 1 (API key + cursor pagination):
name: paystub
base_url: http://localhost:8000/paystub
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
      page_size: 100
      has_more_path: has_more
    incremental:
      cursor_field: created
      request_param: created_gte
    schema:
      id: string
      amount: integer
      currency: string
      created: integer

Example 2 (Bearer + nested offset pagination):
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


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM backends used by connector generation and repair."""

    def generate(
        self, prompt: str, system_instruction: str | None = None
    ) -> str:
        """Return generated text for the given prompt."""


class GeminiLLMClient:
    """Google GenAI SDK wrapper reading GEMINI_API_KEY and GEMINI_MODEL."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
        if not resolved_key and client is None:
            raise ValueError(
                "GEMINI_API_KEY environment variable is required for GeminiLLMClient"
            )
        self.model = (
            model
            or os.environ.get("GEMINI_MODEL", "").strip()
            or DEFAULT_GEMINI_MODEL
        )
        self._client = (
            client if client is not None else genai.Client(api_key=resolved_key)
        )

    def generate(
        self, prompt: str, system_instruction: str | None = None
    ) -> str:
        """Generate text synchronously using google-genai models.generate_content."""
        config = (
            types.GenerateContentConfig(system_instruction=system_instruction)
            if system_instruction
            else None
        )
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        return (response.text or "").strip()


class FakeLLMClient:
    """Deterministic scripted LLM client for offline unit/integration testing."""

    def __init__(
        self,
        responses: list[str] | Callable[[str, int], str],
    ) -> None:
        self._responses = responses
        self.calls: list[dict[str, str | None]] = []

    def generate(
        self, prompt: str, system_instruction: str | None = None
    ) -> str:
        """Return the next scripted response and record the prompt."""
        attempt_idx = len(self.calls)
        self.calls.append(
            {"prompt": prompt, "system_instruction": system_instruction}
        )
        if callable(self._responses):
            return self._responses(prompt, attempt_idx)
        if attempt_idx < len(self._responses):
            return self._responses[attempt_idx]
        return self._responses[-1] if self._responses else ""


@dataclass
class SpecChunk:
    """Indexed section of an API specification used for retrieval."""

    chunk_id: str
    title: str
    content: str
    always_include: bool = False


@dataclass
class GenerationResult:
    """Complete result from connector YAML generation and validation gating."""

    yaml_text: str
    config: ConnectorConfig | None
    validation: ValidationResult
    attempts: int
    retrieved_chunks: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)


def extract_yaml_from_response(text: str) -> str:
    """Extract YAML text from an LLM response, stripping markdown code fences."""
    cleaned = text.strip()
    fence_match = re.search(
        r"```(?:ya?ml)?\s*\n(.*?)```",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        return fence_match.group(1).strip()
    return strip_markdown_fences(cleaned)


def _tokenize(text: str) -> set[str]:
    """Lowercase alphanumeric/underscore tokenization for lightweight retrieval."""
    return set(_TOKEN_RE.findall(text.lower()))


def _resolve_openapi_refs(
    node: Any,
    root: dict[str, Any],
    ref_depth: int = 0,
    seen: set[str] | None = None,
) -> Any:
    """Inline local JSON pointer $refs in an OpenAPI object up to a bounded ref depth."""
    if ref_depth > 6:
        return node
    visited = set() if seen is None else set(seen)
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/") and ref not in visited:
            visited.add(ref)
            target: Any = root
            for part in ref[2:].split("/"):
                if isinstance(target, dict) and part in target:
                    target = target[part]
                else:
                    target = None
                    break
            if target is not None:
                merged = _resolve_openapi_refs(target, root, ref_depth + 1, visited)
                if isinstance(merged, dict):
                    extra = {
                        k: _resolve_openapi_refs(v, root, ref_depth, visited)
                        for k, v in node.items()
                        if k != "$ref"
                    }
                    return {**merged, **extra}
                return merged
        return {
            k: _resolve_openapi_refs(v, root, ref_depth, visited)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [
            _resolve_openapi_refs(item, root, ref_depth, visited)
            for item in node
        ]
    return node


def _try_parse_openapi(spec_text: str) -> dict[str, Any] | None:
    """Parse OpenAPI JSON or YAML if the spec looks like an OpenAPI document."""
    stripped = spec_text.lstrip()
    if not (
        stripped.startswith("{")
        or "openapi:" in spec_text[:500]
        or "swagger:" in spec_text[:500]
        or "paths:" in spec_text[:2000]
    ):
        return None
    try:
        parsed = (
            json.loads(spec_text)
            if stripped.startswith("{")
            else yaml.safe_load(spec_text)
        )
    except Exception:
        return None
    if isinstance(parsed, dict) and "paths" in parsed and isinstance(parsed["paths"], dict):
        return parsed
    return None


def chunk_spec(spec_text: str, max_chunk_chars: int = 2000) -> list[SpecChunk]:
    """Split an OpenAPI or Markdown/text API spec into retrievable chunks."""
    openapi_doc = _try_parse_openapi(spec_text)
    if openapi_doc is not None:
        chunks: list[SpecChunk] = []
        overview: dict[str, Any] = {}
        for key in ("openapi", "swagger", "info", "servers", "host", "basePath", "security"):
            if key in openapi_doc:
                overview[key] = openapi_doc[key]
        components = openapi_doc.get("components")
        if isinstance(components, dict) and "securitySchemes" in components:
            overview["securitySchemes"] = components["securitySchemes"]
        if "securityDefinitions" in openapi_doc:
            overview["securityDefinitions"] = openapi_doc["securityDefinitions"]

        if overview:
            chunks.append(
                SpecChunk(
                    chunk_id="openapi_overview",
                    title="API Overview, Servers, and Auth",
                    content=yaml.safe_dump(overview, sort_keys=False),
                    always_include=True,
                )
            )

        for path_str, path_item in openapi_doc["paths"].items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                resolved_op = _resolve_openapi_refs(operation, openapi_doc)
                entry = {path_str: {method.upper(): resolved_op}}
                op_id = (
                    operation.get("operationId")
                    if isinstance(operation, dict)
                    else ""
                )
                chunks.append(
                    SpecChunk(
                        chunk_id=f"{method.upper()} {path_str}",
                        title=f"{method.upper()} {path_str} {op_id or ''}".strip(),
                        content=yaml.safe_dump(entry, sort_keys=False),
                    )
                )
        if chunks:
            return chunks

    # Fallback: Markdown headings or fixed-size overlapping text windows
    sections = re.split(r"(?m)(?=^#{1,3}\s+)", spec_text.strip())
    chunks = []
    for idx, section in enumerate(sections):
        sec = section.strip()
        if not sec:
            continue
        first_line = sec.splitlines()[0].lstrip("# ").strip()
        if len(sec) <= max_chunk_chars:
            is_header = idx == 0 or any(
                kw in first_line.lower()
                for kw in ("auth", "base url", "overview", "authentication")
            )
            chunks.append(
                SpecChunk(
                    chunk_id=f"section_{idx}",
                    title=first_line[:80],
                    content=sec,
                    always_include=is_header and len(sec) <= 1500,
                )
            )
        else:
            step = max(400, max_chunk_chars - 200)
            for sub_idx, start in enumerate(range(0, len(sec), step)):
                part = sec[start : start + max_chunk_chars]
                chunks.append(
                    SpecChunk(
                        chunk_id=f"section_{idx}_{sub_idx}",
                        title=first_line[:80],
                        content=part,
                    )
                )
    return chunks or [
        SpecChunk(chunk_id="full", title="API Spec", content=spec_text.strip())
    ]


def retrieve_relevant_spec(
    spec_text: str,
    query: str,
    max_context_chars: int = 12_000,
) -> tuple[str, list[str]]:
    """Retrieve the highest-scoring spec chunks within max_context_chars."""
    if len(spec_text) <= max_context_chars:
        return spec_text.strip(), ["full_spec"]

    chunks = chunk_spec(spec_text)
    query_tokens = _tokenize(query) | {
        "auth",
        "authentication",
        "bearer",
        "api_key",
        "oauth2",
        "pagination",
        "cursor",
        "offset",
        "limit",
        "link",
        "next",
        "list",
        "items",
        "data",
        "id",
    }

    sep = "\n\n---\n\n"
    selected: list[SpecChunk] = []
    used_chars = 0

    for chunk in chunks:
        addition = (len(sep) if selected else 0) + len(chunk.content)
        if chunk.always_include and (used_chars + addition) <= max_context_chars:
            selected.append(chunk)
            used_chars += addition

    remaining = [c for c in chunks if c not in selected]
    hint_tokens = _tokenize(query)

    def _score(chunk: SpecChunk) -> tuple[int, int]:
        title_toks = _tokenize(chunk.title)
        body_toks = _tokenize(chunk.content)
        hint_hits = len(hint_tokens & (title_toks | body_toks))
        total_hits = len(query_tokens & (title_toks | body_toks))
        title_boost = len(hint_tokens & title_toks) * 5
        return (hint_hits * 3 + title_boost, total_hits)

    for chunk in sorted(remaining, key=_score, reverse=True):
        addition = (len(sep) if selected else 0) + len(chunk.content)
        if used_chars + addition > max_context_chars:
            continue
        selected.append(chunk)
        used_chars += addition

    if not selected and chunks:
        first = chunks[0]
        selected.append(
            SpecChunk(
                chunk_id=first.chunk_id,
                title=first.title,
                content=first.content[:max_context_chars],
            )
        )

    formatted = sep.join(c.content for c in selected)
    return formatted, [c.chunk_id for c in selected]


def _build_initial_prompt(
    spec_excerpt: str,
    connector_name: str | None = None,
    base_url: str | None = None,
    stream_hints: list[str] | None = None,
) -> str:
    """Build the initial generation prompt from retrieved spec context and hints."""
    parts = [
        "Generate a complete declarative connector YAML config from the API specification below."
    ]
    if connector_name:
        parts.append(f"- Set `name` to `{connector_name}`.")
    if base_url:
        parts.append(f"- Set `base_url` to `{base_url}`.")
    if stream_hints:
        parts.append(
            f"- Include streams matching: {', '.join(stream_hints)}."
        )
    parts.append("\nAPI Specification Context:\n" + spec_excerpt)
    return "\n".join(parts)


def _build_repair_prompt(
    initial_prompt: str,
    previous_yaml: str,
    validation: ValidationResult,
) -> str:
    """Build a follow-up prompt feeding validation errors back to the LLM."""
    return (
        f"{initial_prompt}\n\n"
        "Your previous YAML failed automated validation:\n"
        f"{validation.format_feedback()}\n\n"
        "Previous invalid YAML:\n"
        f"```yaml\n{previous_yaml}\n```\n\n"
        "Fix every error above and output ONLY corrected YAML."
    )


async def generate_connector_config(
    spec_text: str,
    llm_client: LLMClient,
    connector_name: str | None = None,
    base_url: str | None = None,
    stream_hints: list[str] | None = None,
    max_retries: int = 3,
    max_context_chars: int = 12_000,
    probe_endpoint: bool = True,
    http_client: httpx.AsyncClient | None = None,
    audit_logger: AuditLogger | None = None,
) -> GenerationResult:
    """Generate a connector YAML config from a spec and pass it through the validation gate."""
    query_parts = [connector_name or "", " ".join(stream_hints or [])]
    spec_excerpt, retrieved_ids = retrieve_relevant_spec(
        spec_text=spec_text,
        query=" ".join(p for p in query_parts if p).strip() or "list records",
        max_context_chars=max_context_chars,
    )
    initial_prompt = _build_initial_prompt(
        spec_excerpt=spec_excerpt,
        connector_name=connector_name,
        base_url=base_url,
        stream_hints=stream_hints,
    )

    prompt = initial_prompt
    history: list[dict[str, Any]] = []
    last_yaml = ""
    last_validation = ValidationResult(valid=False, errors=["No attempts made."])
    total_attempts = 1 + max(0, max_retries)
    attempts_used = 0

    for attempt in range(1, total_attempts + 1):
        attempts_used = attempt
        raw_response = llm_client.generate(
            prompt=prompt,
            system_instruction=SYSTEM_INSTRUCTION,
        )
        candidate_yaml = extract_yaml_from_response(raw_response)
        validation = await validate_connector_config(
            raw_yaml=candidate_yaml,
            probe_endpoint=probe_endpoint,
            client=http_client,
        )
        history.append(
            {
                "attempt": attempt,
                "valid": validation.valid,
                "errors": list(validation.errors),
                "warnings": list(validation.warnings),
            }
        )
        last_yaml = candidate_yaml
        last_validation = validation

        if validation.valid:
            if audit_logger is not None:
                audit_logger.record(
                    AuditEntry.create(
                        action="generate",
                        connector_name=(
                            validation.config.name
                            if validation.config is not None
                            else (connector_name or "unknown")
                        ),
                        attempts=attempts_used,
                        accepted=True,
                        applied=False,
                        reason=f"Generated from spec ({len(retrieved_ids)} chunk(s))",
                        after_yaml=candidate_yaml,
                        metadata={"retrieved_chunks": retrieved_ids},
                    )
                )
            return GenerationResult(
                yaml_text=candidate_yaml,
                config=validation.config,
                validation=validation,
                attempts=attempt,
                retrieved_chunks=retrieved_ids,
                history=history,
            )

        if attempt < total_attempts:
            prompt = _build_repair_prompt(
                initial_prompt=initial_prompt,
                previous_yaml=candidate_yaml,
                validation=validation,
            )

    if audit_logger is not None:
        audit_logger.record(
            AuditEntry.create(
                action="generate",
                connector_name=connector_name or "unknown",
                attempts=attempts_used,
                accepted=False,
                applied=False,
                reason="Generation failed validation gate",
                validation_errors=last_validation.errors,
                after_yaml=None,
                metadata={"retrieved_chunks": retrieved_ids},
            )
        )

    return GenerationResult(
        yaml_text=last_yaml,
        config=None,
        validation=last_validation,
        attempts=total_attempts,
        retrieved_chunks=retrieved_ids,
        history=history,
    )
