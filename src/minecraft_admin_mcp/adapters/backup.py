import hashlib
import os
import tarfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import zstandard

from ..concurrency import InstanceLocks
from ..config import BackupConfig, MinecraftConfig
from ..errors import ErrorCode, MinecraftAdminError
from ..models import ActionResult, BackupInfo


class SaveController(Protocol):
    def save_off(self) -> ActionResult: ...

    def save_all_flush(self) -> ActionResult: ...

    def save_on(self) -> ActionResult: ...


class BackupManager:
    def __init__(
        self,
        config: BackupConfig,
        minecraft: MinecraftConfig,
        rcon: SaveController,
        locks: InstanceLocks,
    ) -> None:
        self._config = config
        self._minecraft = minecraft
        self._rcon = rcon
        self._locks = locks

    def _prepare_backup_directory(self) -> None:
        try:
            self._config.directory.mkdir(parents=True, exist_ok=True)
            for partial_path in self._config.directory.glob(".backup-*.partial"):
                partial_path.unlink(missing_ok=True)
            for archive_path in self._config.directory.glob("backup-*.tar.zst"):
                if not Path(f"{archive_path}.json").is_file():
                    archive_path.unlink(missing_ok=True)
            for metadata_path in self._config.directory.glob("backup-*.tar.zst.json"):
                archive_path = Path(str(metadata_path).removesuffix(".json"))
                if not archive_path.is_file():
                    metadata_path.unlink(missing_ok=True)
        except OSError:
            raise MinecraftAdminError(
                ErrorCode.BACKUP_FAILED, "failed to prepare backup directory"
            ) from None

    @staticmethod
    def _discard_incomplete(*paths: Path) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _check_deadline(self, deadline: float) -> None:
        if time.monotonic() > deadline:
            raise MinecraftAdminError(ErrorCode.BACKUP_TIMEOUT, "backup timed out")

    @staticmethod
    def _safe_tar_filter(deadline: float):  # type: ignore[no-untyped-def]
        def check(member: tarfile.TarInfo) -> tarfile.TarInfo:
            if time.monotonic() > deadline:
                raise MinecraftAdminError(ErrorCode.BACKUP_TIMEOUT, "backup timed out")
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise MinecraftAdminError(ErrorCode.BACKUP_FAILED, "unsafe archive member")
            return member

        return check

    def _archive_worlds(self, destination: Path, deadline: float) -> None:
        data_root = self._minecraft.data_directory.resolve()
        worlds = [path.resolve() for path in self._minecraft.world_directories if path.is_dir()]
        if not worlds:
            raise MinecraftAdminError(
                ErrorCode.BACKUP_FAILED, "no configured world directory exists"
            )
        try:
            with destination.open("wb") as raw_output:
                compressor = zstandard.ZstdCompressor(level=6)
                with compressor.stream_writer(raw_output, closefd=False) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w|") as archive:
                        for world in worlds:
                            self._check_deadline(deadline)
                            archive.add(
                                world,
                                arcname=world.relative_to(data_root),
                                recursive=True,
                                filter=self._safe_tar_filter(deadline),
                            )
        except MinecraftAdminError:
            raise
        except OSError:
            raise MinecraftAdminError(
                ErrorCode.BACKUP_FAILED, "failed to create backup archive"
            ) from None

    @staticmethod
    def _sha256(path: Path, deadline: float) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as archive:
                while chunk := archive.read(1024 * 1024):
                    if time.monotonic() > deadline:
                        raise MinecraftAdminError(ErrorCode.BACKUP_TIMEOUT, "backup timed out")
                    digest.update(chunk)
        except MinecraftAdminError:
            raise
        except OSError:
            raise MinecraftAdminError(
                ErrorCode.BACKUP_FAILED, "failed to hash backup archive"
            ) from None
        return digest.hexdigest()

    def create_backup(self, reason: str) -> BackupInfo:
        if self._locks.maintenance_lock.locked():
            raise MinecraftAdminError(
                ErrorCode.MAINTENANCE_IN_PROGRESS, "maintenance is in progress"
            )
        if not self._locks.backup_lock.acquire(blocking=False):
            raise MinecraftAdminError(ErrorCode.BACKUP_IN_PROGRESS, "another backup is in progress")
        deadline = time.monotonic() + self._config.timeout_seconds
        backup_id = uuid.uuid4().hex
        created_at = datetime.now(UTC)
        timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
        archive_path = self._config.directory / f"backup-{timestamp}-{backup_id}.tar.zst"
        metadata_path = Path(f"{archive_path}.json")
        partial_path = self._config.directory / f".{archive_path.name}.partial"
        metadata_partial_path = self._config.directory / f".{archive_path.name}.json.partial"
        saving_disabled = False
        try:
            self._prepare_backup_directory()
            if not self._rcon.save_off().success:
                raise MinecraftAdminError(ErrorCode.BACKUP_FAILED, "Minecraft rejected save-off")
            saving_disabled = True
            try:
                if not self._rcon.save_all_flush().success:
                    raise MinecraftAdminError(
                        ErrorCode.BACKUP_FAILED, "Minecraft rejected save-all flush"
                    )
                self._archive_worlds(partial_path, deadline)
                os.replace(partial_path, archive_path)
                digest = self._sha256(archive_path, deadline)
                info = BackupInfo(
                    backup_id=backup_id,
                    created_at=created_at,
                    size_bytes=archive_path.stat().st_size,
                    sha256=digest,
                    reason=reason,
                )
                metadata_partial_path.write_text(info.model_dump_json(indent=2), encoding="utf-8")
                os.replace(metadata_partial_path, metadata_path)
            finally:
                if saving_disabled:
                    if not self._rcon.save_on().success:
                        raise MinecraftAdminError(
                            ErrorCode.BACKUP_FAILED, "Minecraft rejected save-on"
                        )
            self._enforce_retention()
            return info
        except MinecraftAdminError:
            self._discard_incomplete(partial_path, metadata_partial_path)
            if not metadata_path.exists():
                self._discard_incomplete(archive_path)
            raise
        except Exception:
            self._discard_incomplete(partial_path, metadata_partial_path)
            if not metadata_path.exists():
                self._discard_incomplete(archive_path)
            raise MinecraftAdminError(ErrorCode.BACKUP_FAILED, "backup failed") from None
        finally:
            self._locks.backup_lock.release()

    def _records(self) -> list[tuple[BackupInfo, Path, Path]]:
        records: list[tuple[BackupInfo, Path, Path]] = []
        for metadata_path in self._config.directory.glob("backup-*.tar.zst.json"):
            archive_path = Path(str(metadata_path).removesuffix(".json"))
            if not archive_path.is_file():
                continue
            try:
                info = BackupInfo.model_validate_json(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            records.append((info, archive_path, metadata_path))
        return sorted(records, key=lambda record: record[0].created_at, reverse=True)

    def _enforce_retention(self) -> None:
        for _info, archive_path, metadata_path in self._records()[self._config.retention_count :]:
            try:
                archive_path.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
            except OSError:
                raise MinecraftAdminError(
                    ErrorCode.BACKUP_FAILED, "failed to enforce backup retention"
                ) from None

    def list_backups(self) -> list[BackupInfo]:
        return [record[0] for record in self._records()]

    def _find_record(self, backup_id: str) -> tuple[BackupInfo, Path, Path]:
        for info, archive_path, metadata_path in self._records():
            if info.backup_id == backup_id:
                return info, archive_path, metadata_path
        raise MinecraftAdminError(ErrorCode.BACKUP_NOT_FOUND, "backup not found")

    def get_backup(self, backup_id: str) -> BackupInfo:
        return self._find_record(backup_id)[0]

    def verify_backup_integrity(self, backup_id: str) -> BackupInfo:
        info, archive_path, _metadata_path = self._find_record(backup_id)
        deadline = time.monotonic() + self._config.timeout_seconds
        digest = self._sha256(archive_path, deadline)
        if digest != info.sha256:
            raise MinecraftAdminError(
                ErrorCode.BACKUP_INTEGRITY_FAILED, "backup archive hash mismatch"
            )
        return info

    def restore_backup(self, backup_id: str) -> ActionResult:
        """Restore configured world directories from a verified backup id only.

        Never accepts filesystem paths from callers. Requires a writable data directory
        (standard Compose mounts Minecraft data read-only into MCP; host restore may
        still be required in production). Does not use Docker socket/API.
        """
        if not self._locks.maintenance_lock.acquire(blocking=False):
            raise MinecraftAdminError(
                ErrorCode.MAINTENANCE_IN_PROGRESS, "maintenance is in progress"
            )
        try:
            if self._locks.backup_lock.locked():
                raise MinecraftAdminError(
                    ErrorCode.BACKUP_IN_PROGRESS, "another backup is in progress"
                )
            info, archive_path, _metadata_path = self._find_record(backup_id)
            deadline = time.monotonic() + self._config.timeout_seconds
            digest = self._sha256(archive_path, deadline)
            if digest != info.sha256:
                raise MinecraftAdminError(
                    ErrorCode.BACKUP_INTEGRITY_FAILED, "backup archive hash mismatch"
                )
            data_root = self._minecraft.data_directory.resolve()
            if not data_root.is_dir() or not os.access(data_root, os.W_OK):
                raise MinecraftAdminError(
                    ErrorCode.RESTORE_FAILED,
                    "minecraft data directory is not writable for restore",
                )
            allowed_arcnames = {
                str(path.resolve().relative_to(data_root))
                for path in self._minecraft.world_directories
            }
            saving_disabled = False
            try:
                if not self._rcon.save_off().success:
                    raise MinecraftAdminError(
                        ErrorCode.RESTORE_FAILED, "Minecraft rejected save-off"
                    )
                saving_disabled = True
                self._extract_worlds(archive_path, data_root, allowed_arcnames, deadline)
            finally:
                if saving_disabled:
                    if not self._rcon.save_on().success:
                        raise MinecraftAdminError(
                            ErrorCode.RESTORE_FAILED, "Minecraft rejected save-on"
                        )
            return ActionResult(success=True, message=f"restored backup {backup_id}")
        except MinecraftAdminError:
            raise
        except Exception:
            raise MinecraftAdminError(ErrorCode.RESTORE_FAILED, "restore failed") from None
        finally:
            self._locks.maintenance_lock.release()

    def _extract_worlds(
        self,
        archive_path: Path,
        data_root: Path,
        allowed_arcnames: set[str],
        deadline: float,
    ) -> None:
        try:
            with archive_path.open("rb") as raw_input:
                decompressor = zstandard.ZstdDecompressor()
                with decompressor.stream_reader(raw_input) as compressed:
                    with tarfile.open(fileobj=compressed, mode="r|") as archive:
                        for member in archive:
                            self._check_deadline(deadline)
                            member_path = Path(member.name)
                            if member_path.is_absolute() or ".." in member_path.parts:
                                raise MinecraftAdminError(
                                    ErrorCode.RESTORE_FAILED, "unsafe archive member"
                                )
                            top = member_path.parts[0] if member_path.parts else ""
                            if (
                                top not in allowed_arcnames
                                and str(member_path) not in allowed_arcnames
                            ):
                                # Allow nested paths under configured world directory names.
                                if not any(
                                    str(member_path) == name
                                    or str(member_path).startswith(f"{name}/")
                                    for name in allowed_arcnames
                                ):
                                    continue
                            destination = (data_root / member_path).resolve()
                            if not destination.is_relative_to(data_root):
                                raise MinecraftAdminError(
                                    ErrorCode.RESTORE_FAILED, "unsafe extract path"
                                )
                            if member.isdir():
                                destination.mkdir(parents=True, exist_ok=True)
                                continue
                            if not member.isfile():
                                continue
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            extracted = archive.extractfile(member)
                            if extracted is None:
                                continue
                            with extracted, destination.open("wb") as output:
                                while chunk := extracted.read(1024 * 1024):
                                    self._check_deadline(deadline)
                                    output.write(chunk)
        except MinecraftAdminError:
            raise
        except OSError:
            raise MinecraftAdminError(
                ErrorCode.RESTORE_FAILED, "failed to extract backup archive"
            ) from None
