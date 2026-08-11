from minecraft_admin_mcp.config import PermissionState
from minecraft_admin_mcp.permissions import registered_tool_names


def test_only_allow_registers_direct_tools() -> None:
    permissions = {
        "get_identity": PermissionState.ALLOW,
        "broadcast": PermissionState.APPROVAL,
        "kick_player": PermissionState.DISABLED,
    }
    assert registered_tool_names(permissions) == {"get_identity"}


def test_high_risk_approval_maps_to_request_tools() -> None:
    permissions = {
        "get_identity": PermissionState.ALLOW,
        "restart": PermissionState.APPROVAL,
        "ban_player": PermissionState.APPROVAL,
        "restore_backup": PermissionState.APPROVAL,
    }
    assert registered_tool_names(permissions) == {
        "get_identity",
        "request_restart",
        "request_ban_player",
        "request_restore_backup",
    }


def test_high_risk_disabled_maps_to_nothing() -> None:
    permissions = {
        "restart": PermissionState.DISABLED,
        "ban_player": PermissionState.DISABLED,
        "restore_backup": PermissionState.DISABLED,
    }
    assert registered_tool_names(permissions) == set()


def test_high_risk_allow_maps_to_direct_tools() -> None:
    permissions = {
        "restart": PermissionState.ALLOW,
        "ban_player": PermissionState.ALLOW,
        "restore_backup": PermissionState.ALLOW,
    }
    assert registered_tool_names(permissions) == {"restart", "ban_player", "restore_backup"}
