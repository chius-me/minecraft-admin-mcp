from pathlib import Path

from minecraft_admin_mcp.adapters.process_metrics import ProcessMetricsAdapter


def test_reports_disk_and_does_not_guess_unavailable_metrics(tmp_path: Path) -> None:
    metrics = ProcessMetricsAdapter(tmp_path).get_metrics()
    assert metrics.data_volume_free_bytes is not None
    assert metrics.data_volume_free_bytes > 0
    assert metrics.cpu_percent is None
    assert metrics.memory_used_bytes is None
    assert metrics.memory_limit_bytes is None
    assert metrics.tps is None
    assert metrics.mspt is None
