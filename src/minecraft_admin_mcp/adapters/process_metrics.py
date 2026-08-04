import shutil
from pathlib import Path

from ..models import ServerMetrics


class ProcessMetricsAdapter:
    """Return only metrics that are reliably visible from the MCP container."""

    def __init__(self, data_directory: Path) -> None:
        self._data_directory = data_directory

    def get_metrics(self) -> ServerMetrics:
        try:
            free_bytes = shutil.disk_usage(self._data_directory).free
        except OSError:
            free_bytes = None
        # The standard Compose deployment has a separate PID namespace. Reporting the
        # MCP process as Minecraft CPU or memory would be misleading, so those values
        # intentionally remain null until a server-native metric source is configured.
        return ServerMetrics(data_volume_free_bytes=free_bytes)
