from pathlib import Path

import pytest

from minecraft_admin_mcp.config import AppConfig


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    data_directory = tmp_path / "minecraft"
    world_directory = data_directory / "world"
    world_directory.mkdir(parents=True)
    log_file = data_directory / "logs" / "latest.log"
    log_file.parent.mkdir()
    log_file.write_text("", encoding="utf-8")
    return AppConfig.model_validate(
        {
            "server": {
                "id": "survival",
                "name": "Survival",
                "aliases": ["main"],
                "environment": "test",
                "risk_level": "low",
            },
            "minecraft": {
                "version": "1.21.1",
                "loader": "fabric",
                "data_directory": data_directory,
                "log_file": log_file,
                "world_directories": [world_directory],
            },
            "rcon": {"host": "minecraft"},
            "backup": {"directory": tmp_path / "backups"},
            "audit": {"database": tmp_path / "audit.db"},
            "permissions": {
                "get_identity": "allow",
                "get_status": "allow",
                "list_players": "allow",
                "get_whitelist": "allow",
                "broadcast": "allow",
                "whitelist_add": "allow",
                "whitelist_remove": "allow",
                "kick_player": "allow",
                "save_world": "allow",
                "get_metrics": "allow",
                "get_recent_events": "allow",
                "get_recent_errors": "allow",
                "create_backup": "allow",
                "list_backups": "allow",
            },
            "rcon_password": "rcon-secret",
            "mcp_token": "a" * 32,
        }
    )
