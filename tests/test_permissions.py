from minecraft_admin_mcp.config import PermissionState
from minecraft_admin_mcp.permissions import registered_tool_names


def test_only_allow_registers_direct_tools() -> None:
    permissions = {
        "get_identity": PermissionState.ALLOW,
        "broadcast": PermissionState.APPROVAL,
        "kick_player": PermissionState.DISABLED,
    }
    assert registered_tool_names(permissions) == {"get_identity"}
