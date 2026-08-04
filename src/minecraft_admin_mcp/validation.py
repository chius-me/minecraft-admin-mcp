import re
import unicodedata

from .errors import ErrorCode, MinecraftAdminError

PLAYER_NAME = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def validate_player_name(value: str) -> str:
    if not PLAYER_NAME.fullmatch(value):
        raise MinecraftAdminError(ErrorCode.PLAYER_NAME_INVALID, "invalid Minecraft player name")
    return value


def validate_text(value: str, *, maximum: int, field: str) -> str:
    if (
        not value
        or len(value) > maximum
        or any(char in "\r\n" or unicodedata.category(char).startswith("C") for char in value)
    ):
        raise MinecraftAdminError(
            ErrorCode.MESSAGE_INVALID,
            f"{field} must be 1-{maximum} printable characters on one line",
        )
    return value


def validate_broadcast(value: str) -> str:
    return validate_text(value, maximum=200, field="message")


def validate_kick_reason(value: str) -> str:
    return validate_text(value, maximum=100, field="reason")
