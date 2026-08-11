"""Durable SQLite approval queue for high-risk operations (V0.3)."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from .audit import redact
from .errors import ErrorCode, MinecraftAdminError
from .models import ApprovalRequestInfo


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    FAILED = "failed"


class ApprovalQueue:
    """Durable queue; multi-instance safety relies on SQLite IMMEDIATE transactions.

    Process-local threading locks are not sufficient across separate ApprovalQueue
    instances; claim/reject/terminal transitions use BEGIN IMMEDIATE plus
    conditional UPDATE rowcounts so only one claimant succeeds.
    """

    def __init__(self, database: Path, *, default_ttl_seconds: float = 3600) -> None:
        self.database = database
        self.default_ttl_seconds = default_ttl_seconds
        self._lock = threading.Lock()
        database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        # Autocommit mode so we can run explicit BEGIN IMMEDIATE transactions.
        connection.isolation_level = None
        return connection

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        """Exclusive write transaction usable across separate queue instances."""
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except Exception:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    request_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    decided_at TEXT,
                    decision_note TEXT,
                    result_message TEXT,
                    error_code TEXT
                )
                """
            )

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _row_to_info(self, row: sqlite3.Row) -> ApprovalRequestInfo:
        arguments = json.loads(row["arguments_json"])
        if not isinstance(arguments, dict):
            arguments = {}
        return ApprovalRequestInfo(
            request_id=row["request_id"],
            operation=row["operation"],
            status=row["status"],
            created_at=self._parse_time(row["created_at"]),
            expires_at=self._parse_time(row["expires_at"]),
            arguments=arguments,
            decision_note=row["decision_note"],
            result_message=row["result_message"],
            error_code=row["error_code"],
        )

    def _fetch(self, connection: sqlite3.Connection, request_id: str) -> sqlite3.Row:
        row = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM approval_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone(),
        )
        if row is None:
            raise MinecraftAdminError(ErrorCode.APPROVAL_NOT_FOUND, "approval request not found")
        return row

    def enqueue(
        self,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        ttl_seconds: float | None = None,
    ) -> ApprovalRequestInfo:
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise MinecraftAdminError(ErrorCode.APPROVAL_INVALID, "ttl must be positive")
        now = datetime.now(UTC)
        request_id = uuid.uuid4().hex
        expires_at = now + timedelta(seconds=ttl)
        safe_arguments = redact(dict(arguments))
        payload = json.dumps(safe_arguments, ensure_ascii=False, sort_keys=True)
        with self._immediate() as connection:
            connection.execute(
                """
                INSERT INTO approval_requests (
                    request_id, created_at, expires_at, status, operation, arguments_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    now.isoformat(),
                    expires_at.isoformat(),
                    ApprovalStatus.PENDING.value,
                    operation,
                    payload,
                ),
            )
        return ApprovalRequestInfo(
            request_id=request_id,
            operation=operation,
            status=ApprovalStatus.PENDING.value,
            created_at=now,
            expires_at=expires_at,
            arguments=safe_arguments if isinstance(safe_arguments, dict) else {},
        )

    def _expire_if_needed(self, connection: sqlite3.Connection, request_id: str) -> sqlite3.Row:
        row = self._fetch(connection, request_id)
        if row["status"] != ApprovalStatus.PENDING.value:
            return row
        expires_at = self._parse_time(row["expires_at"])
        if datetime.now(UTC) < expires_at:
            return row
        cursor = connection.execute(
            """
            UPDATE approval_requests
            SET status = ?, decided_at = ?, error_code = ?
            WHERE request_id = ? AND status = ?
            """,
            (
                ApprovalStatus.EXPIRED.value,
                datetime.now(UTC).isoformat(),
                ErrorCode.APPROVAL_EXPIRED.value,
                request_id,
                ApprovalStatus.PENDING.value,
            ),
        )
        if cursor.rowcount == 0:
            # Another writer may have decided the row first.
            return self._fetch(connection, request_id)
        return self._fetch(connection, request_id)

    def get(self, request_id: str) -> ApprovalRequestInfo:
        with self._immediate() as connection:
            row = self._expire_if_needed(connection, request_id)
            return self._row_to_info(row)

    def list_pending(self) -> list[ApprovalRequestInfo]:
        with self._immediate() as connection:
            rows = connection.execute(
                "SELECT request_id FROM approval_requests WHERE status = ?",
                (ApprovalStatus.PENDING.value,),
            ).fetchall()
            results: list[ApprovalRequestInfo] = []
            for row in rows:
                current = self._expire_if_needed(connection, row["request_id"])
                if current["status"] == ApprovalStatus.PENDING.value:
                    results.append(self._row_to_info(current))
            return results

    def mark_rejected(self, request_id: str, *, note: str = "") -> ApprovalRequestInfo:
        with self._immediate() as connection:
            row = self._expire_if_needed(connection, request_id)
            if row["status"] == ApprovalStatus.EXPIRED.value:
                raise MinecraftAdminError(ErrorCode.APPROVAL_EXPIRED, "approval request expired")
            if row["status"] != ApprovalStatus.PENDING.value:
                raise MinecraftAdminError(
                    ErrorCode.APPROVAL_ALREADY_DECIDED, "approval request already decided"
                )
            cursor = connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, decided_at = ?, decision_note = ?
                WHERE request_id = ? AND status = ?
                """,
                (
                    ApprovalStatus.REJECTED.value,
                    datetime.now(UTC).isoformat(),
                    note[:500] if note else None,
                    request_id,
                    ApprovalStatus.PENDING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise MinecraftAdminError(
                    ErrorCode.APPROVAL_ALREADY_DECIDED, "approval request already decided"
                )
            return self._row_to_info(self._fetch(connection, request_id))

    def claim_for_execution(self, request_id: str, *, note: str = "") -> ApprovalRequestInfo:
        """Atomically transition pending → approved. Exactly one claimant succeeds."""
        with self._immediate() as connection:
            row = self._expire_if_needed(connection, request_id)
            if row["status"] == ApprovalStatus.EXPIRED.value:
                raise MinecraftAdminError(ErrorCode.APPROVAL_EXPIRED, "approval request expired")
            if row["status"] != ApprovalStatus.PENDING.value:
                raise MinecraftAdminError(
                    ErrorCode.APPROVAL_ALREADY_DECIDED, "approval request already decided"
                )
            cursor = connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, decided_at = ?, decision_note = ?
                WHERE request_id = ? AND status = ?
                """,
                (
                    ApprovalStatus.APPROVED.value,
                    datetime.now(UTC).isoformat(),
                    note[:500] if note else None,
                    request_id,
                    ApprovalStatus.PENDING.value,
                ),
            )
            # Critical: never treat a zero-row update as success even if status is approved.
            if cursor.rowcount != 1:
                raise MinecraftAdminError(
                    ErrorCode.APPROVAL_ALREADY_DECIDED, "approval request already decided"
                )
            claimed = self._fetch(connection, request_id)
            if claimed["status"] != ApprovalStatus.APPROVED.value:
                raise MinecraftAdminError(
                    ErrorCode.APPROVAL_ALREADY_DECIDED, "approval request already decided"
                )
            return self._row_to_info(claimed)

    def mark_executed(self, request_id: str, *, result_message: str) -> ApprovalRequestInfo:
        """Terminal transition approved → executed only (single completion)."""
        with self._immediate() as connection:
            cursor = connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, result_message = ?, error_code = NULL
                WHERE request_id = ? AND status = ?
                """,
                (
                    ApprovalStatus.EXECUTED.value,
                    result_message[:500],
                    request_id,
                    ApprovalStatus.APPROVED.value,
                ),
            )
            if cursor.rowcount != 1:
                row = cast(
                    sqlite3.Row | None,
                    connection.execute(
                        "SELECT status FROM approval_requests WHERE request_id = ?",
                        (request_id,),
                    ).fetchone(),
                )
                if row is None:
                    raise MinecraftAdminError(
                        ErrorCode.APPROVAL_NOT_FOUND, "approval request not found"
                    )
                raise MinecraftAdminError(
                    ErrorCode.APPROVAL_ALREADY_DECIDED,
                    "approval request is not approved for execution",
                )
            return self._row_to_info(self._fetch(connection, request_id))

    def mark_failed(
        self, request_id: str, *, error_code: str, result_message: str
    ) -> ApprovalRequestInfo:
        """Terminal transition approved → failed only (single completion)."""
        with self._immediate() as connection:
            cursor = connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, error_code = ?, result_message = ?
                WHERE request_id = ? AND status = ?
                """,
                (
                    ApprovalStatus.FAILED.value,
                    error_code,
                    result_message[:500],
                    request_id,
                    ApprovalStatus.APPROVED.value,
                ),
            )
            if cursor.rowcount != 1:
                row = cast(
                    sqlite3.Row | None,
                    connection.execute(
                        "SELECT status FROM approval_requests WHERE request_id = ?",
                        (request_id,),
                    ).fetchone(),
                )
                if row is None:
                    raise MinecraftAdminError(
                        ErrorCode.APPROVAL_NOT_FOUND, "approval request not found"
                    )
                raise MinecraftAdminError(
                    ErrorCode.APPROVAL_ALREADY_DECIDED,
                    "approval request is not approved for execution",
                )
            return self._row_to_info(self._fetch(connection, request_id))
