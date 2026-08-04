import threading
import time
from collections import defaultdict, deque

from .config import RateLimitRule
from .errors import ErrorCode, MinecraftAdminError


class RateLimiter:
    def __init__(self, rules: dict[str, RateLimitRule]) -> None:
        self._rules = rules
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, bucket: str) -> None:
        rule = self._rules.get(bucket)
        if rule is None:
            return
        now = time.monotonic()
        cutoff = now - rule.period_seconds
        with self._lock:
            calls = self._calls[bucket]
            while calls and calls[0] <= cutoff:
                calls.popleft()
            if len(calls) >= rule.calls:
                raise MinecraftAdminError(
                    ErrorCode.RATE_LIMITED, f"rate limit exceeded for {bucket}"
                )
            calls.append(now)
