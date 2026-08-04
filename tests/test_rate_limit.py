import pytest

from minecraft_admin_mcp.config import RateLimitRule
from minecraft_admin_mcp.errors import ErrorCode, MinecraftAdminError
from minecraft_admin_mcp.rate_limit import RateLimiter


def test_rate_limiter_rejects_excess_calls() -> None:
    limiter = RateLimiter({"broadcast": RateLimitRule(calls=1, period_seconds=60)})
    limiter.check("broadcast")
    with pytest.raises(MinecraftAdminError) as caught:
        limiter.check("broadcast")
    assert caught.value.code is ErrorCode.RATE_LIMITED
