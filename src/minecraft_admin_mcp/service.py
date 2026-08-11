import time
from collections.abc import Callable, Mapping
from typing import Annotated, TypeVar

from pydantic import Field, SecretStr

from .adapters.backup import BackupManager
from .adapters.log_reader import LogReader
from .adapters.process_metrics import ProcessMetricsAdapter
from .adapters.rcon import RconAdapter
from .approval import ApprovalQueue
from .audit import AuditLog
from .concurrency import InstanceLocks
from .config import AppConfig
from .errors import ErrorCode, MinecraftAdminError
from .models import (
    ActionResult,
    ApprovalRequestInfo,
    BackupInfo,
    Identity,
    LogError,
    LogEvent,
    PlayerList,
    ServerMetrics,
    ServerStatus,
)
from .rate_limit import RateLimiter
from .validation import (
    validate_backup_id,
    validate_backup_reason,
    validate_ban_reason,
    validate_broadcast,
    validate_kick_reason,
    validate_player_name,
)

T = TypeVar("T")
LogLimit = Annotated[int, Field(ge=1, le=200)]


class AdminService:
    def __init__(
        self,
        config: AppConfig,
        rcon: RconAdapter,
        log_reader: LogReader,
        metrics: ProcessMetricsAdapter,
        backups: BackupManager,
        audit: AuditLog,
        rate_limiter: RateLimiter,
        approvals: ApprovalQueue | None = None,
        locks: InstanceLocks | None = None,
    ) -> None:
        self.config = config
        self.rcon = rcon
        self.log_reader = log_reader
        self.metrics = metrics
        self.backups = backups
        self.audit = audit
        self.rate_limiter = rate_limiter
        self.locks = locks if locks is not None else InstanceLocks()
        self.approvals = approvals or ApprovalQueue(
            config.approval_database(),
            default_ttl_seconds=config.approval.default_ttl_seconds,
        )

    def get_identity(self) -> Identity:
        """Return the identity of the single Minecraft server managed by this instance."""
        return Identity(
            server_id=self.config.server.id,
            name=self.config.server.name,
            aliases=self.config.server.aliases,
            environment=self.config.server.environment,
            risk_level=self.config.server.risk_level,
            minecraft_version=self.config.minecraft.version,
            loader=self.config.minecraft.loader,
        )

    def get_status(self) -> ServerStatus:
        """Return RCON reachability and the current player count."""
        return self.rcon.get_status()

    def list_players(self) -> PlayerList:
        """List players currently online on this Minecraft server."""
        return self.rcon.list_players()

    def get_whitelist(self) -> list[str]:
        """List players on this Minecraft server's whitelist."""
        return self.rcon.get_whitelist()

    def get_metrics(self) -> ServerMetrics:
        """Return reliable server-visible metrics; unavailable values are null."""
        return self.metrics.get_metrics()

    @staticmethod
    def _validate_log_limit(limit: int) -> int:
        if not 1 <= limit <= 200:
            raise MinecraftAdminError(ErrorCode.MESSAGE_INVALID, "limit must be between 1 and 200")
        return limit

    def _resolve_log_limit(self, limit: int | None) -> int:
        return self._validate_log_limit(self.config.logs.default_lines if limit is None else limit)

    def get_recent_events(self, limit: LogLimit | None = None) -> list[LogEvent]:
        """Return recent recognized events from the one configured Minecraft log."""
        return self.log_reader.get_recent_events(self._resolve_log_limit(limit))

    def get_recent_errors(self, limit: LogLimit | None = None) -> list[LogError]:
        """Return recent errors from the one configured Minecraft log."""
        return self.log_reader.get_recent_errors(self._resolve_log_limit(limit))

    def list_backups(self) -> list[BackupInfo]:
        """List instance-local backup metadata without exposing filesystem paths."""
        return self.backups.list_backups()

    def _write(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        operation: Callable[[], T],
    ) -> T:
        started = time.monotonic()
        try:
            result = operation()
        except MinecraftAdminError as exc:
            self.audit.record(
                tool_name=tool_name,
                arguments=arguments,
                success=False,
                error_code=exc.code,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        except Exception:
            self.audit.record(
                tool_name=tool_name,
                arguments=arguments,
                success=False,
                error_code=ErrorCode.INTERNAL_ERROR,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise MinecraftAdminError(ErrorCode.INTERNAL_ERROR, "operation failed") from None
        action_failed = isinstance(result, ActionResult) and not result.success
        summary: str
        if isinstance(result, ActionResult):
            summary = result.message
        elif isinstance(result, ApprovalRequestInfo):
            summary = f"queued {result.request_id}"
        else:
            summary = "completed"
        self.audit.record(
            tool_name=tool_name,
            arguments=arguments,
            success=not action_failed,
            result_summary=summary,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    def broadcast(self, message: str) -> ActionResult:
        """Send a validated one-line announcement (maximum 200 characters)."""

        def operation() -> ActionResult:
            self.rate_limiter.check("broadcast")
            return self.rcon.broadcast(validate_broadcast(message))

        return self._write("broadcast", {"message": message}, operation)

    def whitelist_add(self, player: str) -> ActionResult:
        """Add one validated Minecraft player name to the whitelist."""

        def operation() -> ActionResult:
            self.rate_limiter.check("whitelist_write")
            return self.rcon.whitelist_add(validate_player_name(player))

        return self._write("whitelist_add", {"player": player}, operation)

    def whitelist_remove(self, player: str) -> ActionResult:
        """Remove one validated Minecraft player name from the whitelist."""

        def operation() -> ActionResult:
            self.rate_limiter.check("whitelist_write")
            return self.rcon.whitelist_remove(validate_player_name(player))

        return self._write("whitelist_remove", {"player": player}, operation)

    def kick_player(
        self, player: str, reason: str = "Removed by server administrator"
    ) -> ActionResult:
        """Kick one validated player with a validated one-line reason."""

        def operation() -> ActionResult:
            self.rate_limiter.check("kick_player")
            return self.rcon.kick_player(validate_player_name(player), validate_kick_reason(reason))

        return self._write("kick_player", {"player": player, "reason": reason}, operation)

    def save_world(self) -> ActionResult:
        """Flush all loaded world data to disk."""
        return self._write("save_world", {}, self.rcon.save_all_flush)

    def create_backup(self, reason: str = "manual") -> BackupInfo:
        """Create a consistent compressed backup of configured world directories."""

        def operation() -> BackupInfo:
            validated_reason = validate_backup_reason(reason)
            self.rate_limiter.check("create_backup")
            return self.backups.create_backup(validated_reason)

        return self._write("create_backup", {"reason": reason}, operation)

    def ban_player(
        self, player: str, reason: str = "Banned by server administrator"
    ) -> ActionResult:
        """Ban one validated player via fixed RCON ban (direct allow path)."""

        def operation() -> ActionResult:
            self.rate_limiter.check("ban_player")
            return self.rcon.ban_player(validate_player_name(player), validate_ban_reason(reason))

        return self._write("ban_player", {"player": player, "reason": reason}, operation)

    def restart(self) -> ActionResult:
        """Controlled restart via fixed RCON stop (no Docker socket/API)."""

        def operation() -> ActionResult:
            self.rate_limiter.check("restart")
            return self._execute_restart()

        return self._write("restart", {}, operation)

    def restore_backup(self, backup_id: str) -> ActionResult:
        """Restore worlds from an instance-local backup id only."""

        def operation() -> ActionResult:
            self.rate_limiter.check("restore_backup")
            return self.backups.restore_backup(validate_backup_id(backup_id))

        return self._write("restore_backup", {"backup_id": backup_id}, operation)

    def request_ban_player(
        self, player: str, reason: str = "Banned by server administrator"
    ) -> ApprovalRequestInfo:
        """Queue a ban for external approval; does not ban until approved off-MCP."""

        def operation() -> ApprovalRequestInfo:
            self.rate_limiter.check("request_high_risk")
            validated_player = validate_player_name(player)
            validated_reason = validate_ban_reason(reason)
            return self.approvals.enqueue(
                "ban_player",
                {"player": validated_player, "reason": validated_reason},
            )

        return self._write("request_ban_player", {"player": player, "reason": reason}, operation)

    def request_restart(self, reason: str = "maintenance") -> ApprovalRequestInfo:
        """Queue a controlled restart for external approval."""

        def operation() -> ApprovalRequestInfo:
            self.rate_limiter.check("request_high_risk")
            validated_reason = validate_backup_reason(reason)
            return self.approvals.enqueue("restart", {"reason": validated_reason})

        return self._write("request_restart", {"reason": reason}, operation)

    def request_restore_backup(self, backup_id: str) -> ApprovalRequestInfo:
        """Queue a restore by backup id for external approval; no path parameters."""

        def operation() -> ApprovalRequestInfo:
            self.rate_limiter.check("request_high_risk")
            validated_id = validate_backup_id(backup_id)
            # Ensure the id refers to a real local backup before queuing.
            self.backups.verify_backup_integrity(validated_id)
            return self.approvals.enqueue("restore_backup", {"backup_id": validated_id})

        return self._write("request_restore_backup", {"backup_id": backup_id}, operation)

    def _execute_restart(self) -> ActionResult:
        if not self.locks.maintenance_lock.acquire(blocking=False):
            raise MinecraftAdminError(
                ErrorCode.MAINTENANCE_IN_PROGRESS, "maintenance is in progress"
            )
        try:
            if self.locks.backup_lock.locked():
                raise MinecraftAdminError(
                    ErrorCode.BACKUP_IN_PROGRESS, "another backup is in progress"
                )
            # Fixed stop only. Docker Compose restart policy may restart the process;
            # this service never talks to the Docker socket or API.
            return self.rcon.stop_server()
        finally:
            self.locks.maintenance_lock.release()

    def execute_approved_request(self, request: ApprovalRequestInfo) -> ApprovalRequestInfo:
        """Run the operation for an already-claimed (approved) request. Internal/admin use."""
        try:
            if request.operation == "ban_player":
                player = str(request.arguments["player"])
                reason = str(request.arguments.get("reason", "Banned by server administrator"))
                result = self.rcon.ban_player(
                    validate_player_name(player), validate_ban_reason(reason)
                )
                if not result.success:
                    return self.approvals.mark_failed(
                        request.request_id,
                        error_code=ErrorCode.INTERNAL_ERROR.value,
                        result_message=result.message,
                    )
                return self.approvals.mark_executed(
                    request.request_id, result_message=result.message
                )
            if request.operation == "restart":
                result = self._execute_restart()
                if not result.success:
                    return self.approvals.mark_failed(
                        request.request_id,
                        error_code=ErrorCode.INTERNAL_ERROR.value,
                        result_message=result.message,
                    )
                return self.approvals.mark_executed(
                    request.request_id, result_message=result.message
                )
            if request.operation == "restore_backup":
                backup_id = validate_backup_id(str(request.arguments["backup_id"]))
                result = self.backups.restore_backup(backup_id)
                if not result.success:
                    return self.approvals.mark_failed(
                        request.request_id,
                        error_code=ErrorCode.RESTORE_FAILED.value,
                        result_message=result.message,
                    )
                return self.approvals.mark_executed(
                    request.request_id, result_message=result.message
                )
            return self.approvals.mark_failed(
                request.request_id,
                error_code=ErrorCode.APPROVAL_INVALID.value,
                result_message=f"unknown operation {request.operation}",
            )
        except MinecraftAdminError as exc:
            return self.approvals.mark_failed(
                request.request_id,
                error_code=exc.code.value,
                result_message=exc.message,
            )


class ApprovalAdmin:
    """External approval entry point. Uses admin token, never the Agent MCP bearer."""

    def __init__(self, service: AdminService, admin_token: SecretStr | None = None) -> None:
        self._service = service
        self._admin_token = admin_token if admin_token is not None else service.config.admin_token

    def _require_admin(self, token: str) -> None:
        import hmac

        # Agent MCP token must never be usable for approval, even if mis-copied.
        mcp = self._service.config.mcp_token.get_secret_value()
        if hmac.compare_digest(token.encode(), mcp.encode()):
            raise MinecraftAdminError(
                ErrorCode.AUTH_FAILED, "MCP agent token cannot approve requests"
            )
        expected = self._admin_token
        if expected is None:
            raise MinecraftAdminError(
                ErrorCode.AUTH_FAILED, "admin approval token is not configured"
            )
        if not hmac.compare_digest(token.encode(), expected.get_secret_value().encode()):
            raise MinecraftAdminError(ErrorCode.AUTH_FAILED, "invalid admin approval token")

    def list_pending(self, token: str) -> list[ApprovalRequestInfo]:
        self._require_admin(token)
        return self._service.approvals.list_pending()

    def get(self, token: str, request_id: str) -> ApprovalRequestInfo:
        self._require_admin(token)
        return self._service.approvals.get(request_id)

    def reject(self, token: str, request_id: str, note: str = "") -> ApprovalRequestInfo:
        self._require_admin(token)
        started = time.monotonic()
        try:
            result = self._service.approvals.mark_rejected(request_id, note=note)
        except MinecraftAdminError as exc:
            self._service.audit.record(
                tool_name="admin_reject",
                arguments={"request_id": request_id},
                success=False,
                error_code=exc.code,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        self._service.audit.record(
            tool_name="admin_reject",
            arguments={"request_id": request_id},
            success=True,
            result_summary=result.status,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    def approve(self, token: str, request_id: str, note: str = "") -> ApprovalRequestInfo:
        """Approve with admin token and execute exactly once."""
        self._require_admin(token)
        started = time.monotonic()
        try:
            claimed = self._service.approvals.claim_for_execution(request_id, note=note)
            result = self._service.execute_approved_request(claimed)
        except MinecraftAdminError as exc:
            self._service.audit.record(
                tool_name="admin_approve",
                arguments={"request_id": request_id},
                success=False,
                error_code=exc.code,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        self._service.audit.record(
            tool_name="admin_approve",
            arguments={"request_id": request_id},
            success=result.status == "executed",
            result_summary=result.status,
            error_code=result.error_code,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result
