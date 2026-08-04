from enum import StrEnum

from fastmcp.exceptions import ToolError


class ErrorCode(StrEnum):
    CONFIG_INVALID = "CONFIG_INVALID"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_FAILED = "AUTH_FAILED"
    RCON_UNREACHABLE = "RCON_UNREACHABLE"
    RCON_AUTH_FAILED = "RCON_AUTH_FAILED"
    SERVER_OFFLINE = "SERVER_OFFLINE"
    PLAYER_NAME_INVALID = "PLAYER_NAME_INVALID"
    MESSAGE_INVALID = "MESSAGE_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    PERMISSION_DISABLED = "PERMISSION_DISABLED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class MinecraftAdminError(ToolError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")

    def public_message(self) -> str:
        return str(self)
