import hashlib
import tarfile
from pathlib import Path

import pytest
import zstandard

from minecraft_admin_mcp.adapters.backup import BackupManager
from minecraft_admin_mcp.concurrency import InstanceLocks
from minecraft_admin_mcp.config import BackupConfig, MinecraftConfig
from minecraft_admin_mcp.errors import ErrorCode, MinecraftAdminError
from minecraft_admin_mcp.models import ActionResult


class FakeRcon:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def save_off(self) -> ActionResult:
        self.calls.append("save-off")
        return ActionResult(message="saving disabled")

    def save_all_flush(self) -> ActionResult:
        self.calls.append("save-all flush")
        return ActionResult(message="saved")

    def save_on(self) -> ActionResult:
        self.calls.append("save-on")
        return ActionResult(message="saving enabled")


@pytest.fixture
def backup_setup(tmp_path: Path) -> tuple[BackupManager, FakeRcon, Path]:
    data = tmp_path / "minecraft"
    world = data / "world"
    world.mkdir(parents=True)
    (world / "level.dat").write_bytes(b"world-data")
    rcon = FakeRcon()
    manager = BackupManager(
        BackupConfig(directory=tmp_path / "backups", retention_count=2),
        MinecraftConfig(
            version="1.21.1",
            loader="fabric",
            data_directory=data,
            log_file=data / "logs" / "latest.log",
            world_directories=[world],
        ),
        rcon,
        InstanceLocks(),
    )
    return manager, rcon, tmp_path / "backups"


def archive_members(path: Path) -> list[str]:
    with path.open("rb") as compressed:
        with zstandard.ZstdDecompressor().stream_reader(compressed) as stream:
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                return archive.getnames()


def test_creates_consistent_archive_and_metadata(
    backup_setup: tuple[BackupManager, FakeRcon, Path],
) -> None:
    manager, rcon, directory = backup_setup
    info = manager.create_backup("before maintenance")
    assert rcon.calls == ["save-off", "save-all flush", "save-on"]
    archive = next(directory.glob("backup-*.tar.zst"))
    members = archive_members(archive)
    assert "world/level.dat" in members
    assert "server.properties" not in members
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == info.sha256
    assert info.size_bytes == archive.stat().st_size
    assert info.reason == "before maintenance"
    assert manager.list_backups() == [info]
    assert all("/" not in backup.backup_id for backup in manager.list_backups())


def test_constructor_does_not_require_or_create_backup_directory(
    backup_setup: tuple[BackupManager, FakeRcon, Path],
) -> None:
    manager, _rcon, directory = backup_setup
    assert not directory.exists()
    assert manager.list_backups() == []
    assert not directory.exists()


def test_next_backup_removes_abandoned_artifacts(
    backup_setup: tuple[BackupManager, FakeRcon, Path],
) -> None:
    manager, _rcon, directory = backup_setup
    existing = manager.create_backup("existing backup")
    abandoned = [
        directory / ".backup-abandoned.tar.zst.partial",
        directory / ".backup-abandoned.tar.zst.json.partial",
        directory / "backup-20260804T000000Z-orphan.tar.zst",
        directory / "backup-20260804T000001Z-orphan.tar.zst.json",
    ]
    for path in abandoned:
        path.write_bytes(b"abandoned")

    created = manager.create_backup("cleanup test")

    assert all(not path.exists() for path in abandoned)
    assert {backup.backup_id for backup in manager.list_backups()} == {
        existing.backup_id,
        created.backup_id,
    }


def test_backup_lock_rejects_concurrent_backup(
    backup_setup: tuple[BackupManager, FakeRcon, Path],
) -> None:
    manager, _rcon, _directory = backup_setup
    assert manager._locks.backup_lock.acquire(blocking=False)  # noqa: SLF001
    try:
        with pytest.raises(MinecraftAdminError) as caught:
            manager.create_backup("concurrent")
        assert caught.value.code is ErrorCode.BACKUP_IN_PROGRESS
    finally:
        manager._locks.backup_lock.release()  # noqa: SLF001


def test_save_on_is_restored_after_archive_failure(
    backup_setup: tuple[BackupManager, FakeRcon, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, rcon, directory = backup_setup

    def fail_archive(destination: Path, deadline: float) -> None:
        raise MinecraftAdminError(ErrorCode.BACKUP_FAILED, "simulated failure")

    monkeypatch.setattr(manager, "_archive_worlds", fail_archive)
    with pytest.raises(MinecraftAdminError) as caught:
        manager.create_backup("failure test")
    assert caught.value.code is ErrorCode.BACKUP_FAILED
    assert rcon.calls == ["save-off", "save-all flush", "save-on"]
    assert list(directory.glob("*.partial")) == []
    assert manager.list_backups() == []


def test_retention_keeps_newest_backups(
    backup_setup: tuple[BackupManager, FakeRcon, Path],
) -> None:
    manager, _rcon, directory = backup_setup
    created = [manager.create_backup(f"backup {index}") for index in range(3)]
    listed = manager.list_backups()
    assert len(listed) == 2
    assert {backup.backup_id for backup in listed} == {
        created[1].backup_id,
        created[2].backup_id,
    }
    assert len(list(directory.glob("backup-*.tar.zst"))) == 2
    assert len(list(directory.glob("backup-*.tar.zst.json"))) == 2


def test_retention_runs_after_saving_is_restored(
    backup_setup: tuple[BackupManager, FakeRcon, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, rcon, _directory = backup_setup

    def record_retention() -> None:
        rcon.calls.append("retention")

    monkeypatch.setattr(manager, "_enforce_retention", record_retention)
    manager.create_backup("ordering test")
    assert rcon.calls == ["save-off", "save-all flush", "save-on", "retention"]


def test_timeout_has_stable_error_and_restores_saving(
    backup_setup: tuple[BackupManager, FakeRcon, Path],
) -> None:
    manager, rcon, _directory = backup_setup
    manager._config.timeout_seconds = 1e-9  # noqa: SLF001
    with pytest.raises(MinecraftAdminError) as caught:
        manager.create_backup("timeout test")
    assert caught.value.code is ErrorCode.BACKUP_TIMEOUT
    assert rcon.calls == ["save-off", "save-all flush", "save-on"]
