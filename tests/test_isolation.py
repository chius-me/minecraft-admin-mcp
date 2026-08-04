from copy import deepcopy

from minecraft_admin_mcp.config import AppConfig
from minecraft_admin_mcp.server import create_mcp_server


async def test_two_instances_have_distinct_identity_and_tokens(app_config: AppConfig) -> None:
    config_b = deepcopy(app_config)
    config_b.server.id = "creative"
    config_b.server.name = "Creative"
    config_b.mcp_token = type(config_b.mcp_token)("b" * 32)
    server_a = create_mcp_server(app_config)
    server_b = create_mcp_server(config_b)
    result_a = await server_a.call_tool("get_identity", {})
    result_b = await server_b.call_tool("get_identity", {})
    assert result_a.structured_content["server_id"] == "survival"
    assert result_b.structured_content["server_id"] == "creative"
    assert app_config.mcp_token.get_secret_value() != config_b.mcp_token.get_secret_value()
    assert app_config.rcon.host == config_b.rcon.host == "minecraft"
