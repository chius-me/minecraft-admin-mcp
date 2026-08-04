from pathlib import Path

import pytest

from minecraft_admin_mcp.config import PermissionState, load_config
from minecraft_admin_mcp.errors import ErrorCode, MinecraftAdminError


def write_config(path: Path) -> None:
    path.write_text(
        """
server: {id: survival, name: Survival}
minecraft: {version: '1.21.1', loader: fabric}
rcon: {host: minecraft, password_env: TEST_RCON_SECRET}
http: {token_env: TEST_MCP_SECRET}
permissions:
  get_identity: allow
  broadcast: disabled
""",
        encoding="utf-8",
    )


def test_loads_yaml_and_secrets_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.yaml"
    write_config(path)
    monkeypatch.setenv("TEST_RCON_SECRET", "rcon-value")
    monkeypatch.setenv("TEST_MCP_SECRET", "m" * 32)
    config = load_config(path)
    assert config.rcon.host == "minecraft"
    assert config.rcon_password.get_secret_value() == "rcon-value"
    assert config.permissions["broadcast"] is PermissionState.DISABLED
    assert "rcon-value" not in repr(config)


def test_rejects_missing_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.yaml"
    write_config(path)
    monkeypatch.delenv("TEST_RCON_SECRET", raising=False)
    monkeypatch.setenv("TEST_MCP_SECRET", "m" * 32)
    with pytest.raises(MinecraftAdminError) as caught:
        load_config(path)
    assert caught.value.code is ErrorCode.CONFIG_INVALID


def test_rejects_unknown_permission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.yaml"
    write_config(path)
    path.write_text(path.read_text() + "  run_rcon: allow\n", encoding="utf-8")
    monkeypatch.setenv("TEST_RCON_SECRET", "rcon-value")
    monkeypatch.setenv("TEST_MCP_SECRET", "m" * 32)
    with pytest.raises(MinecraftAdminError, match="unsupported permissions"):
        load_config(path)
