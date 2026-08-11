from .config import PermissionState

# Permission config keys that map to request_* (approval) or direct tools (allow).
HIGH_RISK_PERMISSIONS: dict[str, tuple[str, str]] = {
    "restart": ("request_restart", "restart"),
    "ban_player": ("request_ban_player", "ban_player"),
    "restore_backup": ("request_restore_backup", "restore_backup"),
}

# Names that must never appear on the Agent MCP tool list.
FORBIDDEN_AGENT_TOOLS = frozenset(
    {
        "approve",
        "approve_request",
        "reject",
        "reject_request",
        "list_pending_approvals",
        "admin_approve",
        "admin_reject",
    }
)


def registered_tool_names(permissions: dict[str, PermissionState]) -> set[str]:
    """Map permission state to MCP tool names.

    Low-risk: only ``allow`` registers the same-named tool.
    High-risk: ``allow`` registers the direct tool; ``approval`` registers ``request_*`` only.
    """
    names: set[str] = set()
    for name, state in permissions.items():
        if name in HIGH_RISK_PERMISSIONS:
            request_name, direct_name = HIGH_RISK_PERMISSIONS[name]
            if state is PermissionState.ALLOW:
                names.add(direct_name)
            elif state is PermissionState.APPROVAL:
                names.add(request_name)
            continue
        if state is PermissionState.ALLOW:
            names.add(name)
    return names
