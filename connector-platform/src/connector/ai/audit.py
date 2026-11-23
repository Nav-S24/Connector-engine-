"""Audit logging for AI-assisted connector generation and self-healing repairs."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import duckdb


@dataclass
class AuditEntry:
    """Single recorded AI generation or drift-repair event."""

    entry_id: str
    timestamp: str
    action: Literal["generate", "repair"]
    connector_name: str
    attempts: int
    accepted: bool
    applied: bool
    reason: str
    validation_errors: list[str] = field(default_factory=list)
    diff_text: str = ""
    before_yaml: str | None = None
    after_yaml: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        action: Literal["generate", "repair"],
        connector_name: str,
        attempts: int,
        accepted: bool,
        applied: bool,
        reason: str,
        validation_errors: list[str] | None = None,
        diff_text: str = "",
        before_yaml: str | None = None,
        after_yaml: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEntry:
        """Construct a new timestamped AuditEntry."""
        return cls(
            entry_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=action,
            connector_name=connector_name,
            attempts=attempts,
            accepted=accepted,
            applied=applied,
            reason=reason,
            validation_errors=list(validation_errors or []),
            diff_text=diff_text,
            before_yaml=before_yaml,
            after_yaml=after_yaml,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize entry to a JSON-compatible dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEntry:
        """Deserialize an AuditEntry from a dictionary."""
        return cls(
            entry_id=str(data["entry_id"]),
            timestamp=str(data["timestamp"]),
            action=data["action"],
            connector_name=str(data["connector_name"]),
            attempts=int(data["attempts"]),
            accepted=bool(data["accepted"]),
            applied=bool(data["applied"]),
            reason=str(data["reason"]),
            validation_errors=list(data.get("validation_errors", [])),
            diff_text=str(data.get("diff_text", "")),
            before_yaml=data.get("before_yaml"),
            after_yaml=data.get("after_yaml"),
            metadata=dict(data.get("metadata", {})),
        )


class AuditLogger:
    """Append-only JSONL and optional DuckDB audit log for AI actions."""

    def __init__(
        self,
        log_path: str | Path | None = Path("ai_audit.jsonl"),
        duckdb_conn: duckdb.DuckDBPyConnection | None = None,
    ) -> None:
        self.log_path = Path(log_path) if log_path is not None else None
        self.conn = duckdb_conn
        self._memory_entries: list[AuditEntry] = []
        if self.conn is not None:
            self._ensure_table()

    def _ensure_table(self) -> None:
        """Create the _ai_audit DuckDB table when a connection is provided."""
        assert self.conn is not None
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _ai_audit (
                entry_id VARCHAR PRIMARY KEY,
                timestamp VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                connector_name VARCHAR NOT NULL,
                attempts BIGINT NOT NULL,
                accepted BOOLEAN NOT NULL,
                applied BOOLEAN NOT NULL,
                reason VARCHAR NOT NULL,
                validation_errors VARCHAR NOT NULL,
                diff_text VARCHAR NOT NULL,
                before_yaml VARCHAR,
                after_yaml VARCHAR,
                metadata VARCHAR NOT NULL
            )
            """
        )

    def record(self, entry: AuditEntry) -> AuditEntry:
        """Persist an AuditEntry to JSONL, optional DuckDB, and memory."""
        self._memory_entries.append(entry)
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.to_dict(), default=str) + "\n")

        if self.conn is not None:
            self.conn.execute(
                """
                INSERT INTO _ai_audit (
                    entry_id, timestamp, action, connector_name, attempts,
                    accepted, applied, reason, validation_errors, diff_text,
                    before_yaml, after_yaml, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    entry.entry_id,
                    entry.timestamp,
                    entry.action,
                    entry.connector_name,
                    entry.attempts,
                    entry.accepted,
                    entry.applied,
                    entry.reason,
                    json.dumps(entry.validation_errors),
                    entry.diff_text,
                    entry.before_yaml,
                    entry.after_yaml,
                    json.dumps(entry.metadata, default=str),
                ],
            )
        return entry

    def read_all(self, connector_name: str | None = None) -> list[AuditEntry]:
        """Load recorded AuditEntry records, filtered optionally by connector_name."""
        if self.log_path is not None and self.log_path.is_file():
            entries: list[AuditEntry] = []
            with self.log_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    entries.append(AuditEntry.from_dict(json.loads(stripped)))
        else:
            entries = list(self._memory_entries)

        if connector_name is not None:
            return [e for e in entries if e.connector_name == connector_name]
        return entries

    def summary(self) -> dict[str, Any]:
        """Compute aggregate counts over all recorded audit entries."""
        entries = self.read_all()
        return {
            "total": len(entries),
            "accepted": sum(1 for e in entries if e.accepted),
            "rejected": sum(1 for e in entries if not e.accepted),
            "applied": sum(1 for e in entries if e.applied),
            "by_action": {
                "generate": sum(1 for e in entries if e.action == "generate"),
                "repair": sum(1 for e in entries if e.action == "repair"),
            },
        }
