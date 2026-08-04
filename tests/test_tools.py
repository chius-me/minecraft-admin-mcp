import sqlite3

import pytest

from minecraft_admin_mcp.adapters.rcon import RconAdapter
from minecraft_admin_mcp.audit import AuditLog
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

    return AdminService(
        app_config,
        RconAdapter(app_config.rcon, "secret", runner),
        AuditLog(app_config.audit.database, app_config.server.id),
        RateLimiter(app_config.rate_limits),
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
