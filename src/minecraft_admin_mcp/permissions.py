from .config import PermissionState


def registered_tool_names(permissions: dict[str, PermissionState]) -> set[str]:
    """Return direct V0.1 tools. Approval tools are reserved for V0.3."""
    return {name for name, state in permissions.items() if state is PermissionState.ALLOW}
