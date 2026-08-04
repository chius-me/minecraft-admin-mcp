import pytest

from minecraft_admin_mcp.config import AppConfig, PermissionState
from minecraft_admin_mcp.errors import MinecraftAdminError
from minecraft_admin_mcp.server import create_mcp_server


async def test_disabled_and_approval_tools_are_absent(app_config: AppConfig) -> None:
    app_config.permissions["broadcast"] = PermissionState.DISABLED
    app_config.permissions["kick_player"] = PermissionState.APPROVAL
    server = create_mcp_server(app_config)
    names = {tool.name for tool in await server.list_tools()}
    assert "get_identity" in names
    assert "broadcast" not in names
    assert "kick_player" not in names
    assert not ({"run_rcon", "run_command", "execute_shell"} & names)


async def test_expected_error_retains_stable_code(app_config: AppConfig) -> None:
    server = create_mcp_server(app_config)
    with pytest.raises(MinecraftAdminError, match="PLAYER_NAME_INVALID"):
        await server.call_tool("whitelist_add", {"player": "Steve; stop"})


async def test_v02_tools_are_registered_without_path_parameters(app_config: AppConfig) -> None:
    server = create_mcp_server(app_config)
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert {
        "get_metrics",
        "get_recent_events",
        "get_recent_errors",
        "create_backup",
        "list_backups",
    } <= tools.keys()
    assert "path" not in tools["get_recent_events"].parameters["properties"]
    assert "path" not in tools["get_recent_errors"].parameters["properties"]
    assert "path" not in tools["create_backup"].parameters["properties"]


async def test_disabled_backup_tools_do_not_require_backup_storage(
    app_config: AppConfig,
) -> None:
    app_config.permissions["create_backup"] = PermissionState.DISABLED
    app_config.permissions["list_backups"] = PermissionState.DISABLED
    unavailable_directory = app_config.backup.directory / "missing" / "backups"
    app_config.backup.directory = unavailable_directory

    server = create_mcp_server(app_config)

    names = {tool.name for tool in await server.list_tools()}
    assert "create_backup" not in names
    assert "list_backups" not in names
    assert not unavailable_directory.exists()
