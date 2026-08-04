import sqlite3
from pathlib import Path

from minecraft_admin_mcp.audit import AuditLog


def test_audit_redacts_secrets(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    audit = AuditLog(database, "survival")
    audit.record(
        tool_name="broadcast",
        arguments={
            "message": "connect to 192.0.2.10 or 2001:db8::1",
            "rcon_password": "never-store-this",
        },
        success=True,
        duration_ms=1,
    )
    with sqlite3.connect(database) as connection:
        arguments = connection.execute("SELECT arguments_json FROM audit_events").fetchone()[0]
    assert "never-store-this" not in arguments
    assert "192.0.2.10" not in arguments
    assert "2001:db8::1" not in arguments
    assert "[REDACTED]" in arguments
