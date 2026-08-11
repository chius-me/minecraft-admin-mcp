import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from minecraft_admin_mcp.approval import ApprovalQueue, ApprovalStatus
from minecraft_admin_mcp.errors import ErrorCode, MinecraftAdminError


def test_enqueue_and_get(tmp_path: Path) -> None:
    queue = ApprovalQueue(tmp_path / "approvals.db", default_ttl_seconds=60)
    info = queue.enqueue("ban_player", {"player": "Steve", "reason": "grief"})
    assert info.status == ApprovalStatus.PENDING.value
    assert info.operation == "ban_player"
    assert info.arguments["player"] == "Steve"
    loaded = queue.get(info.request_id)
    assert loaded.request_id == info.request_id
    assert loaded.status == ApprovalStatus.PENDING.value


def test_reject_prevents_claim(tmp_path: Path) -> None:
    queue = ApprovalQueue(tmp_path / "approvals.db", default_ttl_seconds=60)
    info = queue.enqueue("restart", {"reason": "maintenance"})
    rejected = queue.mark_rejected(info.request_id, note="no")
    assert rejected.status == ApprovalStatus.REJECTED.value
    with pytest.raises(MinecraftAdminError, match="APPROVAL_ALREADY_DECIDED"):
        queue.claim_for_execution(info.request_id)


def test_claim_and_execute_marks_terminal(tmp_path: Path) -> None:
    queue = ApprovalQueue(tmp_path / "approvals.db", default_ttl_seconds=60)
    info = queue.enqueue("ban_player", {"player": "Alex"})
    claimed = queue.claim_for_execution(info.request_id, note="ok")
    assert claimed.status == ApprovalStatus.APPROVED.value
    executed = queue.mark_executed(info.request_id, result_message="banned")
    assert executed.status == ApprovalStatus.EXECUTED.value
    with pytest.raises(MinecraftAdminError, match="APPROVAL_ALREADY_DECIDED"):
        queue.claim_for_execution(info.request_id)
    with pytest.raises(MinecraftAdminError, match="APPROVAL_ALREADY_DECIDED"):
        queue.mark_executed(info.request_id, result_message="again")
    with pytest.raises(MinecraftAdminError, match="APPROVAL_ALREADY_DECIDED"):
        queue.mark_failed(
            info.request_id, error_code=ErrorCode.INTERNAL_ERROR.value, result_message="no"
        )


def test_mark_executed_requires_approved_status(tmp_path: Path) -> None:
    queue = ApprovalQueue(tmp_path / "approvals.db", default_ttl_seconds=60)
    info = queue.enqueue("ban_player", {"player": "Steve"})
    with pytest.raises(MinecraftAdminError, match="APPROVAL_ALREADY_DECIDED"):
        queue.mark_executed(info.request_id, result_message="skip claim")


def test_expired_cannot_be_executed(tmp_path: Path) -> None:
    queue = ApprovalQueue(tmp_path / "approvals.db", default_ttl_seconds=1)
    info = queue.enqueue("restart", {"reason": "soon"}, ttl_seconds=0.001)
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    with sqlite3.connect(queue.database) as connection:
        connection.execute(
            "UPDATE approval_requests SET expires_at = ? WHERE request_id = ?",
            (past, info.request_id),
        )
    with pytest.raises(MinecraftAdminError) as exc_info:
        queue.claim_for_execution(info.request_id)
    assert exc_info.value.code is ErrorCode.APPROVAL_EXPIRED
    loaded = queue.get(info.request_id)
    assert loaded.status == ApprovalStatus.EXPIRED.value


def test_list_pending_skips_expired(tmp_path: Path) -> None:
    queue = ApprovalQueue(tmp_path / "approvals.db", default_ttl_seconds=3600)
    live = queue.enqueue("ban_player", {"player": "Steve"})
    dead = queue.enqueue("ban_player", {"player": "Alex"}, ttl_seconds=0.001)
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    with sqlite3.connect(queue.database) as connection:
        connection.execute(
            "UPDATE approval_requests SET expires_at = ? WHERE request_id = ?",
            (past, dead.request_id),
        )
    pending = queue.list_pending()
    ids = {item.request_id for item in pending}
    assert live.request_id in ids
    assert dead.request_id not in ids


def test_two_queue_instances_only_one_claim_succeeds(tmp_path: Path) -> None:
    """Separate ApprovalQueue objects share SQLite, not process-local locks."""
    database = tmp_path / "approvals.db"
    seed = ApprovalQueue(database, default_ttl_seconds=3600)
    info = seed.enqueue("ban_player", {"player": "Steve", "reason": "race"})
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        queue = ApprovalQueue(database, default_ttl_seconds=3600)
        barrier.wait()
        try:
            queue.claim_for_execution(info.request_id, note="claim")
            with lock:
                outcomes.append("ok")
        except MinecraftAdminError as exc:
            with lock:
                outcomes.append(exc.code.value)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count(ErrorCode.APPROVAL_ALREADY_DECIDED.value) == 1
    assert seed.get(info.request_id).status == ApprovalStatus.APPROVED.value


def test_concurrent_claim_stress_exactly_one_winner(tmp_path: Path) -> None:
    """Stress: many separate queue instances race; only one claim per request."""
    database = tmp_path / "approvals.db"
    seed = ApprovalQueue(database, default_ttl_seconds=3600)
    double_ok = 0
    rounds = 200
    for _ in range(rounds):
        info = seed.enqueue("ban_player", {"player": "Steve"})
        barrier = threading.Barrier(4)
        successes = 0
        counter_lock = threading.Lock()

        def worker(
            request_id: str = info.request_id,
            ready: threading.Barrier = barrier,
            tally_lock: threading.Lock = counter_lock,
        ) -> None:
            nonlocal successes
            queue = ApprovalQueue(database, default_ttl_seconds=3600)
            ready.wait()
            try:
                queue.claim_for_execution(request_id)
                with tally_lock:
                    successes += 1
            except MinecraftAdminError as exc:
                assert exc.code is ErrorCode.APPROVAL_ALREADY_DECIDED

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if successes != 1:
            double_ok += 1
        assert successes == 1, f"expected exactly one claim, got {successes}"
    assert double_ok == 0


def test_concurrent_claim_and_execute_runs_once(tmp_path: Path) -> None:
    """Two queues cannot both complete mark_executed after a single claim winner."""
    database = tmp_path / "approvals.db"
    seed = ApprovalQueue(database, default_ttl_seconds=3600)
    executions = 0
    for _ in range(50):
        info = seed.enqueue("restart", {"reason": "once"})
        barrier = threading.Barrier(2)
        local_exec = 0
        lock = threading.Lock()

        def worker(
            request_id: str = info.request_id,
            ready: threading.Barrier = barrier,
            tally_lock: threading.Lock = lock,
        ) -> None:
            nonlocal local_exec
            queue = ApprovalQueue(database, default_ttl_seconds=3600)
            ready.wait()
            try:
                queue.claim_for_execution(request_id)
            except MinecraftAdminError:
                return
            try:
                queue.mark_executed(request_id, result_message="done")
                with tally_lock:
                    local_exec += 1
            except MinecraftAdminError as exc:
                assert exc.code is ErrorCode.APPROVAL_ALREADY_DECIDED

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert local_exec == 1
        executions += local_exec
        assert seed.get(info.request_id).status == ApprovalStatus.EXECUTED.value
    assert executions == 50


def test_concurrent_service_approve_executes_rcon_once(
    tmp_path: Path,
) -> None:
    """End-to-end: two ApprovalAdmin/service paths on separate queues share one ban."""
    from pydantic import SecretStr

    from minecraft_admin_mcp.adapters.backup import BackupManager
    from minecraft_admin_mcp.adapters.log_reader import LogReader
    from minecraft_admin_mcp.adapters.process_metrics import ProcessMetricsAdapter
    from minecraft_admin_mcp.adapters.rcon import RconAdapter
    from minecraft_admin_mcp.audit import AuditLog
    from minecraft_admin_mcp.concurrency import InstanceLocks
    from minecraft_admin_mcp.config import AppConfig, PermissionState
    from minecraft_admin_mcp.rate_limit import RateLimiter
    from minecraft_admin_mcp.service import AdminService, ApprovalAdmin

    data_directory = tmp_path / "minecraft"
    world = data_directory / "world"
    world.mkdir(parents=True)
    log_file = data_directory / "logs" / "latest.log"
    log_file.parent.mkdir()
    log_file.write_text("", encoding="utf-8")
    rcon_calls: list[tuple[str, ...]] = []

    def runner(command: str, *arguments: str) -> str:
        rcon_calls.append((command, *arguments))
        return "OK"

    config = AppConfig.model_validate(
        {
            "server": {"id": "survival", "name": "Survival"},
            "minecraft": {
                "version": "1.21.1",
                "loader": "fabric",
                "data_directory": data_directory,
                "log_file": log_file,
                "world_directories": [world],
            },
            "rcon": {"host": "minecraft"},
            "backup": {"directory": tmp_path / "backups"},
            "audit": {"database": tmp_path / "audit.db"},
            "approval": {"database": tmp_path / "approvals.db"},
            "permissions": {
                "get_identity": "allow",
                "ban_player": "approval",
            },
            "rcon_password": "rcon-secret",
            "mcp_token": "a" * 32,
            "admin_token": "b" * 32,
        }
    )
    config.permissions["ban_player"] = PermissionState.APPROVAL
    locks = InstanceLocks()
    rcon = RconAdapter(config.rcon, "secret", runner)
    shared_queue = ApprovalQueue(config.approval_database(), default_ttl_seconds=3600)
    service = AdminService(
        config=config,
        rcon=rcon,
        log_reader=LogReader(config.minecraft.log_file, config.logs),
        metrics=ProcessMetricsAdapter(config.minecraft.data_directory),
        backups=BackupManager(config.backup, config.minecraft, rcon, locks),
        audit=AuditLog(config.audit.database, config.server.id),
        rate_limiter=RateLimiter(config.rate_limits),
        approvals=shared_queue,
        locks=locks,
    )
    # Second service with a separate ApprovalQueue instance on the same DB.
    other_queue = ApprovalQueue(config.approval_database(), default_ttl_seconds=3600)
    service_b = AdminService(
        config=config,
        rcon=rcon,
        log_reader=LogReader(config.minecraft.log_file, config.logs),
        metrics=ProcessMetricsAdapter(config.minecraft.data_directory),
        backups=BackupManager(config.backup, config.minecraft, rcon, locks),
        audit=AuditLog(config.audit.database, config.server.id),
        rate_limiter=RateLimiter(config.rate_limits),
        approvals=other_queue,
        locks=locks,
    )
    queued = service.request_ban_player("Steve", "grief")
    barrier = threading.Barrier(2)
    results: list[str] = []
    results_lock = threading.Lock()

    def approve(admin: ApprovalAdmin) -> None:
        barrier.wait()
        try:
            outcome = admin.approve("b" * 32, queued.request_id)
            with results_lock:
                results.append(outcome.status)
        except MinecraftAdminError as exc:
            with results_lock:
                results.append(exc.code.value)

    threads = [
        threading.Thread(target=approve, args=(ApprovalAdmin(service),)),
        threading.Thread(target=approve, args=(ApprovalAdmin(service_b),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("executed") == 1
    assert results.count(ErrorCode.APPROVAL_ALREADY_DECIDED.value) == 1
    ban_calls = [call for call in rcon_calls if call[0] == "ban"]
    assert ban_calls == [("ban", "Steve", "grief")]
    assert isinstance(config.admin_token, SecretStr)
