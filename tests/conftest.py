from pathlib import Path

import pytest

from minecraft_admin_mcp.config import AppConfig


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "server": {
                "id": "survival",
                "name": "Survival",
                "aliases": ["main"],
                "environment": "test",
                "risk_level": "low",
            },
            "minecraft": {"version": "1.21.1", "loader": "fabric"},
            "rcon": {"host": "minecraft"},
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
            },
            "rcon_password": "rcon-secret",
            "mcp_token": "a" * 32,
        }
    )
