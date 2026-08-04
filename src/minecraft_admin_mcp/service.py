import time
from collections.abc import Callable, Mapping
from typing import Annotated, TypeVar

from pydantic import Field

from .adapters.backup import BackupManager
from .adapters.log_reader import LogReader
from .adapters.process_metrics import ProcessMetricsAdapter
from .adapters.rcon import RconAdapter
from .audit import AuditLog
from .config import AppConfig
from .errors import ErrorCode, MinecraftAdminError
from .models import (
    ActionResult,
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
    validate_backup_reason,
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
    ) -> None:
        self.config = config
        self.rcon = rcon
        self.log_reader = log_reader
        self.metrics = metrics
        self.backups = backups
        self.audit = audit
        self.rate_limiter = rate_limiter

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

    def get_recent_events(self, limit: LogLimit = 50) -> list[LogEvent]:
        """Return recent recognized events from the one configured Minecraft log."""
        return self.log_reader.get_recent_events(self._validate_log_limit(limit))

    def get_recent_errors(self, limit: LogLimit = 50) -> list[LogError]:
        """Return recent errors from the one configured Minecraft log."""
        return self.log_reader.get_recent_errors(self._validate_log_limit(limit))

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
        self.audit.record(
            tool_name=tool_name,
            arguments=arguments,
            success=not action_failed,
            result_summary=result.message if isinstance(result, ActionResult) else "completed",
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
