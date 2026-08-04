from pathlib import Path

import pytest

from minecraft_admin_mcp.adapters.log_reader import LogReader
from minecraft_admin_mcp.config import LogsConfig
from minecraft_admin_mcp.errors import ErrorCode, MinecraftAdminError


def test_parses_events_and_marks_player_chat_untrusted(tmp_path: Path) -> None:
    log_file = tmp_path / "latest.log"
    log_file.write_text(
        """[12:00:00] [Server thread/INFO]: Done (1.23s)! For help, type "help"
[12:01:00] [Server thread/INFO]: Steve joined the game
[12:02:00] [Server thread/INFO]: <Steve> visit 192.0.2.10
[12:03:00] [Server thread/INFO]: Steve was slain by Zombie
[12:04:00] [Server thread/INFO]: Added Alex to the whitelist
[12:05:00] [Server thread/INFO]: Steve lost connection: Kicked by an operator
[12:06:00] [Server thread/INFO]: Steve left the game
[12:07:00] [Server thread/INFO]: Stopping server
""",
        encoding="utf-8",
    )
    events = LogReader(log_file, LogsConfig()).get_recent_events(50)
    assert [event.event_type for event in events] == [
        "server_start",
        "player_join",
        "player_chat",
        "player_death",
        "whitelist_add",
        "player_kick",
        "player_leave",
        "server_stop",
    ]
    chat = events[2]
    assert chat.player == "Steve"
    assert chat.timestamp == "12:02:00"
    assert chat.source == "player_chat"
    assert chat.trusted is False
    assert "192.0.2.10" not in chat.message
    assert "[REDACTED_IP]" in chat.message


def test_parses_errors_and_applies_limit(tmp_path: Path) -> None:
    log_file = tmp_path / "latest.log"
    log_file.write_text(
        """[12:00:00] [Server thread/ERROR]: Failed to load mod example from 2001:db8::1
[12:00:01] [Server thread/INFO]: java.lang.IllegalStateException: broken
[12:00:02] [Server Watchdog/FATAL]: Watchdog detected a crash
""",
        encoding="utf-8",
    )
    errors = LogReader(log_file, LogsConfig()).get_recent_errors(2)
    assert len(errors) == 2
    assert errors[0].category == "exception"
    assert errors[1].category == "fatal"
    assert all(error.trusted is False for error in errors)


def test_unavailable_log_has_stable_error(tmp_path: Path) -> None:
    reader = LogReader(tmp_path / "missing.log", LogsConfig())
    with pytest.raises(MinecraftAdminError) as caught:
        reader.get_recent_events(10)
    assert caught.value.code is ErrorCode.LOG_UNAVAILABLE
