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
