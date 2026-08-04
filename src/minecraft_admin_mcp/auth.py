import hmac

from fastmcp.server.auth import AccessToken, TokenVerifier


class StaticBearerTokenVerifier(TokenVerifier):
    """Verify one instance-local opaque bearer token in constant time."""

    def __init__(self, expected_token: str) -> None:
        super().__init__()
        self._expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token.encode(), self._expected_token.encode()):
            return None
        return AccessToken(token=token, client_id="minecraft-admin", scopes=[])
