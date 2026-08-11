import pytest
from pydantic import SecretStr

from minecraft_admin_mcp.adapters.backup import BackupManager
from minecraft_admin_mcp.adapters.log_reader import LogReader
from minecraft_admin_mcp.adapters.process_metrics import ProcessMetricsAdapter
from minecraft_admin_mcp.adapters.rcon import RconAdapter
from minecraft_admin_mcp.approval import ApprovalQueue
from minecraft_admin_mcp.audit import AuditLog
from minecraft_admin_mcp.concurrency import InstanceLocks
from minecraft_admin_mcp.config import AppConfig, PermissionState
from minecraft_admin_mcp.errors import ErrorCode, MinecraftAdminError
from minecraft_admin_mcp.rate_limit import RateLimiter
from minecraft_admin_mcp.server import create_mcp_server
from minecraft_admin_mcp.service import AdminService, ApprovalAdmin


@pytest.fixture
def rcon_calls() -> list[tuple[str, ...]]:
    return []


@pytest.fixture
def locks() -> InstanceLocks:
    return InstanceLocks()


@pytest.fixture
def service(
    app_config: AppConfig, rcon_calls: list[tuple[str, ...]], locks: InstanceLocks
) -> AdminService:
    app_config.admin_token = SecretStr("b" * 32)
    app_config.permissions["ban_player"] = PermissionState.APPROVAL
    app_config.permissions["restart"] = PermissionState.APPROVAL
    app_config.permissions["restore_backup"] = PermissionState.APPROVAL

    def runner(command: str, *arguments: str) -> str:
        rcon_calls.append((command, *arguments))
        if command == "list":
            return "There are 0 of a max of 20 players online:"
        if command == "whitelist" and arguments == ("list",):
            return "There are 0 whitelisted players:"
        return "OK"

    rcon = RconAdapter(app_config.rcon, "secret", runner)
    return AdminService(
        config=app_config,
        rcon=rcon,
        log_reader=LogReader(app_config.minecraft.log_file, app_config.logs),
        metrics=ProcessMetricsAdapter(app_config.minecraft.data_directory),
        backups=BackupManager(app_config.backup, app_config.minecraft, rcon, locks),
        audit=AuditLog(app_config.audit.database, app_config.server.id),
        rate_limiter=RateLimiter(app_config.rate_limits),
        approvals=ApprovalQueue(app_config.approval_database(), default_ttl_seconds=3600),
        locks=locks,
    )


async def test_approval_registers_request_tools_not_direct(app_config: AppConfig) -> None:
    app_config.permissions["restart"] = PermissionState.APPROVAL
    app_config.permissions["ban_player"] = PermissionState.APPROVAL
    app_config.permissions["restore_backup"] = PermissionState.APPROVAL
    server = create_mcp_server(app_config)
    names = {tool.name for tool in await server.list_tools()}
    assert {
        "request_restart",
        "request_ban_player",
        "request_restore_backup",
    } <= names
    assert "restart" not in names
    assert "ban_player" not in names
    assert "restore_backup" not in names
    assert (
        not {
            "approve",
            "approve_request",
            "reject",
            "admin_approve",
            "list_pending_approvals",
        }
        & names
    )


async def test_disabled_high_risk_registers_nothing(app_config: AppConfig) -> None:
    app_config.permissions["restart"] = PermissionState.DISABLED
    app_config.permissions["ban_player"] = PermissionState.DISABLED
    app_config.permissions["restore_backup"] = PermissionState.DISABLED
    server = create_mcp_server(app_config)
    names = {tool.name for tool in await server.list_tools()}
    assert "request_restart" not in names
    assert "request_ban_player" not in names
    assert "request_restore_backup" not in names
    assert "restart" not in names
    assert "ban_player" not in names
    assert "restore_backup" not in names


async def test_allow_registers_direct_high_risk_tools(app_config: AppConfig) -> None:
    app_config.permissions["restart"] = PermissionState.ALLOW
    app_config.permissions["ban_player"] = PermissionState.ALLOW
    app_config.permissions["restore_backup"] = PermissionState.ALLOW
    server = create_mcp_server(app_config)
    names = {tool.name for tool in await server.list_tools()}
    assert {"restart", "ban_player", "restore_backup"} <= names
    assert "request_restart" not in names


def test_request_ban_queues_without_rcon(
    service: AdminService, rcon_calls: list[tuple[str, ...]]
) -> None:
    queued = service.request_ban_player("Steve", "griefing")
    assert queued.status == "pending"
    assert queued.operation == "ban_player"
    assert ("ban", "Steve", "griefing") not in rcon_calls


def test_admin_approve_executes_ban_once(
    service: AdminService, rcon_calls: list[tuple[str, ...]]
) -> None:
    admin = ApprovalAdmin(service)
    admin_token = "b" * 32
    mcp_token = service.config.mcp_token.get_secret_value()
    queued = service.request_ban_player("Steve", "griefing")
    with pytest.raises(MinecraftAdminError, match="AUTH_FAILED"):
        admin.approve(mcp_token, queued.request_id)
    executed = admin.approve(admin_token, queued.request_id, note="ops ok")
    assert executed.status == "executed"
    assert ("ban", "Steve", "griefing") in rcon_calls
    with pytest.raises(MinecraftAdminError, match="APPROVAL_ALREADY_DECIDED"):
        admin.approve(admin_token, queued.request_id)


def test_reject_never_executes(service: AdminService, rcon_calls: list[tuple[str, ...]]) -> None:
    admin = ApprovalAdmin(service)
    queued = service.request_ban_player("Alex", "test")
    rejected = admin.reject("b" * 32, queued.request_id, note="no")
    assert rejected.status == "rejected"
    assert not any(call[0] == "ban" for call in rcon_calls)


def test_invalid_player_not_queued(service: AdminService) -> None:
    with pytest.raises(MinecraftAdminError, match="PLAYER_NAME_INVALID"):
        service.request_ban_player("bad;name")


def test_request_restart_approve_uses_stop(
    service: AdminService, rcon_calls: list[tuple[str, ...]]
) -> None:
    queued = service.request_restart("rolling restart")
    result = ApprovalAdmin(service).approve("b" * 32, queued.request_id)
    assert result.status == "executed"
    assert ("stop",) in rcon_calls


def test_restart_blocked_during_backup(service: AdminService, locks: InstanceLocks) -> None:
    assert locks.backup_lock.acquire(blocking=False)
    try:
        with pytest.raises(MinecraftAdminError) as exc_info:
            service.restart()
        assert exc_info.value.code is ErrorCode.BACKUP_IN_PROGRESS
    finally:
        locks.backup_lock.release()


def test_backup_blocked_during_maintenance(service: AdminService, locks: InstanceLocks) -> None:
    (service.config.minecraft.world_directories[0] / "level.dat").write_bytes(b"world")
    assert locks.maintenance_lock.acquire(blocking=False)
    try:
        with pytest.raises(MinecraftAdminError) as exc_info:
            service.create_backup("blocked")
        assert exc_info.value.code is ErrorCode.MAINTENANCE_IN_PROGRESS
    finally:
        locks.maintenance_lock.release()


def test_restore_rejects_path_like_backup_id(service: AdminService) -> None:
    with pytest.raises(MinecraftAdminError, match="BACKUP_NOT_FOUND"):
        service.request_restore_backup("../etc/passwd")
    with pytest.raises(MinecraftAdminError, match="BACKUP_NOT_FOUND"):
        service.request_restore_backup("not-a-hex-backup-id!!!!")
    with pytest.raises(MinecraftAdminError, match="BACKUP_NOT_FOUND"):
        service.request_restore_backup("world/level.dat")


def test_restore_request_and_approve(
    service: AdminService, rcon_calls: list[tuple[str, ...]]
) -> None:
    world = service.config.minecraft.world_directories[0]
    (world / "level.dat").write_bytes(b"original")
    backup = service.create_backup("before-restore")
    (world / "level.dat").write_bytes(b"changed")
    queued = service.request_restore_backup(backup.backup_id)
    assert queued.status == "pending"
    assert ("ban",) not in {call[:1] for call in rcon_calls}
    result = ApprovalAdmin(service).approve("b" * 32, queued.request_id)
    assert result.status == "executed"
    assert (world / "level.dat").read_bytes() == b"original"


def test_restore_unknown_backup_id(service: AdminService) -> None:
    with pytest.raises(MinecraftAdminError, match="BACKUP_NOT_FOUND"):
        service.request_restore_backup("a" * 32)


def test_direct_ban_when_allow(app_config: AppConfig, rcon_calls: list[tuple[str, ...]]) -> None:
    app_config.permissions["ban_player"] = PermissionState.ALLOW
    locks = InstanceLocks()

    def runner(command: str, *arguments: str) -> str:
        rcon_calls.append((command, *arguments))
        return "OK"

    rcon = RconAdapter(app_config.rcon, "secret", runner)
    svc = AdminService(
        config=app_config,
        rcon=rcon,
        log_reader=LogReader(app_config.minecraft.log_file, app_config.logs),
        metrics=ProcessMetricsAdapter(app_config.minecraft.data_directory),
        backups=BackupManager(app_config.backup, app_config.minecraft, rcon, locks),
        audit=AuditLog(app_config.audit.database, app_config.server.id),
        rate_limiter=RateLimiter(app_config.rate_limits),
        locks=locks,
    )
    assert svc.ban_player("Steve", "cheating").success
    assert ("ban", "Steve", "cheating") in rcon_calls
