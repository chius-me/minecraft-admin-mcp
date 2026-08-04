import threading


class InstanceLocks:
    """Process-local locks shared by every MCP session in one instance."""

    def __init__(self) -> None:
        self.backup_lock = threading.Lock()
        self.maintenance_lock = threading.Lock()
