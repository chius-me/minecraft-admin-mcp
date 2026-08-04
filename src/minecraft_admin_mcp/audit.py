import ipaddress
import json
import re
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = frozenset(
    {"authorization", "token", "mcp_token", "password", "rcon_password", "secret"}
)
IPV4_PATTERN = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6_CANDIDATE_PATTERN = re.compile(r"(?<![0-9A-Fa-f:])[0-9A-Fa-f:]*:[0-9A-Fa-f:]+(?![0-9A-Fa-f:])")


def redact_ip_addresses(value: str) -> str:
    value = IPV4_PATTERN.sub("[REDACTED_IP]", value)

    def redact_ipv6(match: re.Match[str]) -> str:
        candidate = match.group(0)
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            return candidate
        return "[REDACTED_IP]" if address.version == 6 else candidate

    return IPV6_CANDIDATE_PATTERN.sub(redact_ipv6, value)


def redact(value: Any, key: str = "") -> Any:
    if any(part in key.lower() for part in SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_ip_addresses(value)
    return value


class AuditLog:
    def __init__(self, database: Path, server_id: str) -> None:
        self.database = database
        self.server_id = server_id
        self._lock = threading.Lock()
        database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database, timeout=5)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    result_summary TEXT,
                    error_code TEXT,
                    duration_ms INTEGER NOT NULL
                )
                """
            )

    def record(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        success: bool,
        duration_ms: int,
        result_summary: str | None = None,
        error_code: str | None = None,
    ) -> None:
        safe_arguments = json.dumps(redact(arguments), ensure_ascii=False, sort_keys=True)
        safe_summary = str(redact(result_summary))[:500] if result_summary else None
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    timestamp, server_id, tool_name, arguments_json, success,
                    result_summary, error_code, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    self.server_id,
                    tool_name,
                    safe_arguments,
                    int(success),
                    safe_summary,
                    error_code,
                    max(0, duration_ms),
                ),
            )
