"""AI-assisted connector generation, drift repair, validation gate, and audit."""

from connector.ai.audit import AuditEntry, AuditLogger
from connector.ai.generator import (
    FakeLLMClient,
    GeminiLLMClient,
    GenerationResult,
    LLMClient,
    SpecChunk,
    chunk_spec,
    extract_yaml_from_response,
    generate_connector_config,
    retrieve_relevant_spec,
)
from connector.ai.repair import (
    RepairResult,
    compute_yaml_diff,
    inspect_live_drift_context,
    repair_connector_config,
)
from connector.ai.validator import (
    ValidationResult,
    validate_connector_config,
    validate_yaml_schema,
)

__all__ = [
    "AuditEntry",
    "AuditLogger",
    "FakeLLMClient",
    "GeminiLLMClient",
    "GenerationResult",
    "LLMClient",
    "RepairResult",
    "SpecChunk",
    "ValidationResult",
    "chunk_spec",
    "compute_yaml_diff",
    "extract_yaml_from_response",
    "generate_connector_config",
    "inspect_live_drift_context",
    "repair_connector_config",
    "retrieve_relevant_spec",
    "validate_connector_config",
    "validate_yaml_schema",
]
