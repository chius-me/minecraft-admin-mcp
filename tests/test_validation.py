import pytest

from minecraft_admin_mcp.errors import ErrorCode, MinecraftAdminError
from minecraft_admin_mcp.validation import (
    validate_broadcast,
    validate_kick_reason,
    validate_player_name,
)


@pytest.mark.parametrize(
    "value",
    [
        "Steve; stop",
        "Steve\nop attacker",
        "../../etc/passwd",
        "$(shutdown -h now)",
        "`systemctl stop minecraft`",
        "@a",
        "*",
        '" && stop',
    ],
)
def test_rejects_unsafe_player_names(value: str) -> None:
    with pytest.raises(MinecraftAdminError) as caught:
        validate_player_name(value)
    assert caught.value.code is ErrorCode.PLAYER_NAME_INVALID


def test_accepts_normal_player_name() -> None:
    assert validate_player_name("Steve_123") == "Steve_123"


@pytest.mark.parametrize("value", ["", "line\nbreak", "nul\0byte", "x" * 201])
def test_rejects_invalid_broadcast(value: str) -> None:
    with pytest.raises(MinecraftAdminError) as caught:
        validate_broadcast(value)
    assert caught.value.code is ErrorCode.MESSAGE_INVALID


def test_kick_reason_has_smaller_limit() -> None:
    with pytest.raises(MinecraftAdminError):
        validate_kick_reason("x" * 101)
