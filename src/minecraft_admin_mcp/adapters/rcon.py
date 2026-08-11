import re
from typing import Protocol, cast

from rcon.exceptions import WrongPassword  # type: ignore[import-untyped]
from rcon.source import Client  # type: ignore[import-untyped]

from ..config import RconConfig
from ..errors import ErrorCode, MinecraftAdminError
from ..models import ActionResult, PlayerList, ServerStatus

LIST_PATTERN = re.compile(
    r"There are (?P<online>\d+) of a max of (?P<maximum>\d+) players online:?(?P<names>.*)",
    re.IGNORECASE,
)
WHITELIST_PATTERN = re.compile(
    r"There are \d+ whitelisted player(?:\(s\)|s)?:?(?P<names>.*)", re.IGNORECASE
)


class CommandRunner(Protocol):
    def __call__(self, command: str, *arguments: str) -> str: ...


class RconAdapter:
    """Only exposes fixed RCON operations; raw execution stays private."""

    def __init__(
        self,
        config: RconConfig,
        password: str,
        runner: CommandRunner | None = None,
    ) -> None:
        self._config = config
        self._password = password
        self._runner = runner

    def _execute(self, command: str, *arguments: str) -> str:
        try:
            if self._runner is not None:
                return self._runner(command, *arguments)
            with Client(
                self._config.host,
                self._config.port,
                timeout=self._config.timeout_seconds,
                passwd=self._password,
            ) as client:
                return cast(str, client.run(command, *arguments))
        except WrongPassword:
            raise MinecraftAdminError(
                ErrorCode.RCON_AUTH_FAILED, "RCON authentication failed"
            ) from None
        except OSError:
            raise MinecraftAdminError(ErrorCode.RCON_UNREACHABLE, "RCON is unreachable") from None
        except MinecraftAdminError:
            raise
        except Exception:
            raise MinecraftAdminError(ErrorCode.RCON_UNREACHABLE, "RCON request failed") from None

    def get_status(self) -> ServerStatus:
        try:
            players = self.list_players()
            return ServerStatus(
                minecraft_reachable=True,
                rcon_reachable=True,
                online_players=players.online,
                maximum_players=players.maximum,
                players=players.players,
            )
        except MinecraftAdminError as exc:
            if exc.code is ErrorCode.RCON_AUTH_FAILED:
                return ServerStatus(minecraft_reachable=True, rcon_reachable=False)
            if exc.code is ErrorCode.RCON_UNREACHABLE:
                return ServerStatus(minecraft_reachable=False, rcon_reachable=False)
            raise

    def list_players(self) -> PlayerList:
        response = self._execute("list")
        match = LIST_PATTERN.search(response)
        if match is None:
            raise MinecraftAdminError(
                ErrorCode.SERVER_OFFLINE, "unexpected response from Minecraft"
            )
        names = [name.strip() for name in match.group("names").split(",") if name.strip()]
        return PlayerList(
            online=int(match.group("online")),
            maximum=int(match.group("maximum")),
            players=names,
        )

    def get_whitelist(self) -> list[str]:
        response = self._execute("whitelist", "list")
        match = WHITELIST_PATTERN.search(response)
        if match is None:
            return []
        return [name.strip() for name in match.group("names").split(",") if name.strip()]

    def broadcast(self, message: str) -> ActionResult:
        response = self._execute("say", message)
        return self._action_result(response, "announcement sent")

    @staticmethod
    def _action_result(response: str, fallback: str) -> ActionResult:
        normalized = response.casefold()
        failed = any(
            marker in normalized
            for marker in (
                "does not exist",
                "could not",
                "no player was found",
                "unknown or incomplete command",
            )
        )
        return ActionResult(success=not failed, message=response or fallback)

    def whitelist_add(self, player: str) -> ActionResult:
        response = self._execute("whitelist", "add", player)
        return self._action_result(response, f"{player} added to whitelist")

    def whitelist_remove(self, player: str) -> ActionResult:
        response = self._execute("whitelist", "remove", player)
        return self._action_result(response, f"{player} removed from whitelist")

    def kick_player(self, player: str, reason: str) -> ActionResult:
        response = self._execute("kick", player, reason)
        return self._action_result(response, f"{player} kicked")

    def save_all_flush(self) -> ActionResult:
        response = self._execute("save-all", "flush")
        return self._action_result(response, "world saved")

    def save_off(self) -> ActionResult:
        response = self._execute("save-off")
        return self._action_result(response, "automatic saving disabled")

    def save_on(self) -> ActionResult:
        response = self._execute("save-on")
        return self._action_result(response, "automatic saving enabled")

    def ban_player(self, player: str, reason: str) -> ActionResult:
        """Ban one player via the fixed ban command only (no raw RCON)."""
        response = self._execute("ban", player, reason)
        return self._action_result(response, f"{player} banned")

    def stop_server(self) -> ActionResult:
        """Issue the fixed stop command. Compose restart policy may bring the process back."""
        response = self._execute("stop")
        return self._action_result(response, "stop command sent")
