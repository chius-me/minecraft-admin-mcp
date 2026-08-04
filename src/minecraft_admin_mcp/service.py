import time
from collections.abc import Callable, Mapping
from typing import TypeVar

from .adapters.rcon import RconAdapter
from .audit import AuditLog
from .config import AppConfig
from .errors import ErrorCode, MinecraftAdminError
from .models import ActionResult, Identity, PlayerList, ServerStatus
from .rate_limit import RateLimiter
from .validation import validate_broadcast, validate_kick_reason, validate_player_name

T = TypeVar("T")


class AdminService:
    def __init__(
        self,
        config: AppConfig,
        rcon: RconAdapter,
        audit: AuditLog,
        rate_limiter: RateLimiter,
    ) -> None:
        self.config = config
        self.rcon = rcon
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
