import httpx

from minecraft_admin_mcp.auth import StaticBearerTokenVerifier
from minecraft_admin_mcp.config import AppConfig
from minecraft_admin_mcp.server import create_mcp_server


async def test_token_verifier_accepts_only_exact_token() -> None:
    verifier = StaticBearerTokenVerifier("correct-token")
    assert await verifier.verify_token("wrong-token") is None
    access = await verifier.verify_token("correct-token")
    assert access is not None
    assert access.client_id == "minecraft-admin"


async def test_http_endpoint_requires_bearer_token(app_config: AppConfig) -> None:
    app = create_mcp_server(app_config).http_app(path="/mcp", stateless_http=True)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.post("/mcp", json=request, headers=headers)).status_code == 401
            assert (
                await client.post(
                    "/mcp",
                    json=request,
                    headers={**headers, "Authorization": f"Bearer {'a' * 32}"},
                )
            ).status_code == 200
