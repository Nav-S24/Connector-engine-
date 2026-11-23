"""Pydantic models and YAML loader for declarative connector configs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FieldType = Literal[
    "string",
    "integer",
    "float",
    "number",
    "boolean",
    "object",
    "array",
    "any",
]

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ApiKeyAuthConfig(BaseModel):
    """API-key authentication via a custom request header."""

    type: Literal["api_key"]
    header: str = Field(default="X-API-Key", min_length=1)
    env_var: str = Field(min_length=1)


class BearerAuthConfig(BaseModel):
    """Bearer token authentication via the Authorization header."""

    type: Literal["bearer"]
    env_var: str = Field(min_length=1)


class OAuth2AuthConfig(BaseModel):
    """OAuth2 refresh-token authentication flow configuration."""

    type: Literal["oauth2"]
    token_url: str = Field(min_length=1)
    client_id_env_var: str = Field(min_length=1)
    client_secret_env_var: str = Field(min_length=1)
    refresh_token_env_var: str = Field(min_length=1)
    scope: str | None = None

    @field_validator("token_url")
    @classmethod
    def validate_token_url(cls, value: str) -> str:
        """Ensure token_url starts with http:// or https://."""
        cleaned = value.strip()
        if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            raise ValueError("token_url must start with 'http://' or 'https://'")
        return cleaned


AuthConfig = Annotated[
    Union[ApiKeyAuthConfig, BearerAuthConfig, OAuth2AuthConfig],
    Field(discriminator="type"),
]


class OffsetPaginationConfig(BaseModel):
    """Offset/limit pagination strategy configuration."""

    type: Literal["offset"]
    offset_param: str = Field(default="offset", min_length=1)
    limit_param: str = Field(default="limit", min_length=1)
    page_size: int = Field(default=100, ge=1, le=10000)
    total_path: str | None = None


class CursorPaginationConfig(BaseModel):
    """Cursor-based pagination strategy configuration."""

    type: Literal["cursor"]
    cursor_param: str = Field(default="starting_after", min_length=1)
    cursor_field: str = Field(default="id", min_length=1)
    page_size_param: str = Field(default="limit", min_length=1)
    page_size: int = Field(default=100, ge=1, le=10000)
    has_more_path: str | None = None
    next_cursor_path: str | None = None


class LinkHeaderPaginationConfig(BaseModel):
    """RFC 5988 Link-header pagination strategy configuration."""

    type: Literal["link_header"]
    page_size_param: str = Field(default="limit", min_length=1)
    page_size: int = Field(default=100, ge=1, le=10000)
    rel: str = Field(default="next", min_length=1)


PaginationConfig = Annotated[
    Union[
        OffsetPaginationConfig,
        CursorPaginationConfig,
        LinkHeaderPaginationConfig,
    ],
    Field(discriminator="type"),
]


class IncrementalConfig(BaseModel):
    """Incremental sync bookmark configuration."""

    cursor_field: str = Field(min_length=1)
    request_param: str = Field(min_length=1)


class StreamConfig(BaseModel):
    """Declarative configuration for a single API stream/endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    method: Literal["GET", "POST"] = "GET"
    records_path: str = "data"
    primary_key: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    pagination: PaginationConfig
    incremental: IncrementalConfig | None = None
    schema_: dict[str, FieldType] = Field(alias="schema")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Ensure stream name is a valid SQL-safe identifier."""
        if not _IDENTIFIER_RE.match(value):
            raise ValueError(
                f"Stream name '{value}' must match ^[A-Za-z_][A-Za-z0-9_]*$"
            )
        return value

    @field_validator("schema_")
    @classmethod
    def validate_schema_columns(
        cls, value: dict[str, FieldType]
    ) -> dict[str, FieldType]:
        """Ensure all schema column names are valid identifiers."""
        if not value:
            raise ValueError("Stream schema must define at least one field")
        for col in value:
            if not _IDENTIFIER_RE.match(col):
                raise ValueError(
                    f"Schema field '{col}' must match ^[A-Za-z_][A-Za-z0-9_]*$"
                )
        return value

    @model_validator(mode="after")
    def validate_primary_key_in_schema(self) -> StreamConfig:
        """Ensure the declared primary_key exists in the stream schema."""
        if self.primary_key not in self.schema_:
            raise ValueError(
                f"primary_key '{self.primary_key}' must be defined in stream schema"
            )
        return self


class ConnectorConfig(BaseModel):
    """Top-level declarative connector configuration."""

    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    rate_limit: float | None = Field(default=None, gt=0)
    max_concurrency: int = Field(default=4, ge=1)
    auth: AuthConfig
    streams: list[StreamConfig] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def validate_connector_name(cls, value: str) -> str:
        """Ensure connector name is a valid SQL-safe identifier."""
        if not _IDENTIFIER_RE.match(value):
            raise ValueError(
                f"Connector name '{value}' must match ^[A-Za-z_][A-Za-z0-9_]*$"
            )
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        """Validate that base_url uses http or https."""
        cleaned = value.strip().rstrip("/")
        if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            raise ValueError("base_url must start with 'http://' or 'https://'")
        return cleaned

    @model_validator(mode="after")
    def validate_unique_streams(self) -> ConnectorConfig:
        """Ensure stream names within a connector are unique."""
        names = [stream.name for stream in self.streams]
        if len(names) != len(set(names)):
            raise ValueError("Stream names within a connector must be unique")
        return self


def extract_nested(payload: Any, dotted_path: str) -> Any:
    """Extract a nested value from a dict/list structure using dot notation."""
    if not dotted_path or dotted_path == ".":
        return payload
    current = payload
    for segment in dotted_path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit():
            idx = int(segment)
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            return None
    return current


def load_config_from_yaml(raw_yaml: str) -> ConnectorConfig:
    """Parse and validate a ConnectorConfig from a YAML string."""
    data = yaml.safe_load(raw_yaml)
    if not isinstance(data, dict):
        raise ValueError("Connector config YAML must deserialize to a mapping")
    return ConnectorConfig.model_validate(data)


def load_config(path: str | Path) -> ConnectorConfig:
    """Load and validate a ConnectorConfig from a YAML file path."""
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Connector config not found: {config_path}")
    return load_config_from_yaml(config_path.read_text(encoding="utf-8"))
