from pathlib import Path

import yaml


def test_compose_enforces_container_boundaries() -> None:
    compose = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    minecraft = compose["services"]["minecraft"]
    mcp = compose["services"]["minecraft-admin-mcp"]
    assert any(str(port).endswith(":25565") for port in minecraft["ports"])
    assert all("25575" not in str(port) for port in minecraft["ports"])
    assert mcp["read_only"] is True
    assert mcp["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in mcp["security_opt"]
    assert all("docker.sock" not in str(volume) for volume in mcp["volumes"])
    assert any(str(volume).endswith(":/minecraft:ro") for volume in mcp["volumes"])
    assert mcp["networks"] == minecraft["networks"] == ["minecraft_internal"]
