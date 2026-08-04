import sqlite3

import pytest

from minecraft_admin_mcp.adapters.backup import BackupManager
from minecraft_admin_mcp.adapters.log_reader import LogReader
from minecraft_admin_mcp.adapters.process_metrics import ProcessMetricsAdapter
from minecraft_admin_mcp.adapters.rcon import RconAdapter
from minecraft_admin_mcp.audit import AuditLog
from minecraft_admin_mcp.concurrency import InstanceLocks
from minecraft_admin_mcp.config import AppConfig
from minecraft_admin_mcp.errors import MinecraftAdminError
from minecraft_admin_mcp.rate_limit import RateLimiter
from minecraft_admin_mcp.service import AdminService


@pytest.fixture
def service(app_config: AppConfig) -> AdminService:
    def runner(command: str, *arguments: str) -> str:
        if command == "list":
            return "There are 0 of a max of 20 players online:"
        if command == "whitelist" and arguments == ("list",):
            return "There are 0 whitelisted players:"
        return "OK"

    rcon = RconAdapter(app_config.rcon, "secret", runner)
    return AdminService(
        config=app_config,
        rcon=rcon,
        log_reader=LogReader(app_config.minecraft.log_file, app_config.logs),
        metrics=ProcessMetricsAdapter(app_config.minecraft.data_directory),
        backups=BackupManager(app_config.backup, app_config.minecraft, rcon, InstanceLocks()),
        audit=AuditLog(app_config.audit.database, app_config.server.id),
        rate_limiter=RateLimiter(app_config.rate_limits),
    )


def test_read_and_write_tools(service: AdminService) -> None:
    assert service.get_identity().server_id == "survival"
    assert service.get_status().online_players == 0
    assert service.list_players().players == []
    assert service.get_whitelist() == []
    assert service.broadcast("Maintenance soon").success
    assert service.whitelist_add("Steve").success
    assert service.whitelist_remove("Steve").success
    assert service.kick_player("Steve").success
    assert service.save_world().success
    with sqlite3.connect(service.config.audit.database) as connection:
        count = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert count == 5


def test_validation_failure_is_audited(service: AdminService) -> None:
    with pytest.raises(MinecraftAdminError):
        service.whitelist_add("Steve; stop")
    with sqlite3.connect(service.config.audit.database) as connection:
        row = connection.execute(
            "SELECT success, error_code FROM audit_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row == (0, "PLAYER_NAME_INVALID")


def test_v02_read_tools_and_backup_are_integrated(service: AdminService) -> None:
    service.config.minecraft.log_file.write_text(
        "[12:00:00] [Server thread/INFO]: Steve joined the game\n"
        "[12:00:01] [Server thread/ERROR]: simulated failure\n",
        encoding="utf-8",
    )
    (service.config.minecraft.world_directories[0] / "level.dat").write_bytes(b"world")
    assert service.get_metrics().data_volume_free_bytes is not None
    assert service.get_recent_events()[0].event_type == "player_join"
    assert service.get_recent_errors()[0].category == "error"
    backup = service.create_backup("pytest")
    assert backup.reason == "pytest"
    assert service.list_backups() == [backup]
    with sqlite3.connect(service.config.audit.database) as connection:
        row = connection.execute(
            "SELECT success, error_code FROM audit_events WHERE tool_name = 'create_backup'"
        ).fetchone()
    assert row == (1, None)


def test_log_tools_use_configured_default_and_honor_explicit_limit(
    service: AdminService,
) -> None:
    service.config.logs.default_lines = 1
    service.config.minecraft.log_file.write_text(
        "[12:00:00] [Server thread/INFO]: Steve joined the game\n"
        "[12:00:01] [Server thread/INFO]: Alex joined the game\n"
        "[12:00:02] [Server thread/ERROR]: first failure\n"
        "[12:00:03] [Server thread/ERROR]: second failure\n",
        encoding="utf-8",
    )
    assert [event.player for event in service.get_recent_events()] == ["Alex"]
    assert len(service.get_recent_events(2)) == 2
    assert "second failure" in service.get_recent_errors()[0].message
    assert len(service.get_recent_errors(2)) == 2


@pytest.mark.parametrize("limit", [0, 201])
def test_log_limit_is_validated(service: AdminService, limit: int) -> None:
    with pytest.raises(MinecraftAdminError, match="limit must be between"):
        service.get_recent_events(limit)


def test_backup_reason_is_validated_and_audited(service: AdminService) -> None:
    with pytest.raises(MinecraftAdminError, match="MESSAGE_INVALID"):
        service.create_backup("bad\nreason")
    with sqlite3.connect(service.config.audit.database) as connection:
        row = connection.execute(
            "SELECT success, error_code FROM audit_events WHERE tool_name = 'create_backup'"
        ).fetchone()
    assert row == (0, "MESSAGE_INVALID")
