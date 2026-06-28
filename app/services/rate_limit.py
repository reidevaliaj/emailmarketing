"""Redis-backed rate pacing (Section 6.3).

Two limits gate every send, both required for B2B deliverability and to stay
under Contabo's ~25 emails/min provider cap:

  * GLOBAL token bucket   — whole-system send rate (default 20/min, < Contabo 25).
  * PER-DOMAIN token bucket — corporate mail servers throttle bursts.

A send may only proceed if it can take a token from BOTH buckets at once, so we
never burn a global token while blocked on a domain (and vice-versa). This is
done with a single WATCH/MULTI optimistic transaction — atomic, smoothly paced,
and free of any Lua dependency (so it runs identically on fakeredis in tests).

Also here: a per-IP **daily cap** (warming safety net, Section 4) so a pool can
never exceed its configured daily volume by accident.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import redis as redis_lib
from redis.exceptions import WatchError

from app.config import settings


@dataclass(slots=True)
class Bucket:
    key: str
    capacity: float          # max tokens (== per-minute rate)
    refill_per_sec: float    # tokens added per second
    requested: float = 1.0


@dataclass(slots=True)
class AcquireResult:
    allowed: bool
    retry_after: float       # seconds to wait before retrying when not allowed


class RateLimiter:
    def __init__(self, client: "redis_lib.Redis", max_watch_retries: int = 25) -> None:
        self._r = client
        self._max_watch_retries = max_watch_retries

    def acquire(self, buckets: list[Bucket]) -> AcquireResult:
        """Take 1 token from every bucket atomically, or take none."""
        if not buckets:
            return AcquireResult(allowed=True, retry_after=0.0)

        keys = [b.key for b in buckets]
        for _ in range(self._max_watch_retries):
            with self._r.pipeline() as pipe:
                try:
                    pipe.watch(*keys)
                    now = time.time()
                    computed: list[tuple[Bucket, float]] = []
                    allowed = True
                    retry_after = 0.0
                    for b in buckets:
                        raw = pipe.hmget(b.key, "tokens", "ts")
                        tokens = float(raw[0]) if raw[0] is not None else b.capacity
                        ts = float(raw[1]) if raw[1] is not None else now
                        tokens = min(b.capacity, tokens + max(0.0, now - ts) * b.refill_per_sec)
                        computed.append((b, tokens))
                        if tokens < b.requested:
                            allowed = False
                            retry_after = max(
                                retry_after, (b.requested - tokens) / b.refill_per_sec
                            )

                    pipe.multi()
                    for b, tokens in computed:
                        new_tokens = tokens - b.requested if allowed else tokens
                        pipe.hset(b.key, mapping={"tokens": new_tokens, "ts": now})
                        ttl = int(b.capacity / b.refill_per_sec) + 60
                        pipe.expire(b.key, ttl)
                    pipe.execute()
                    return AcquireResult(allowed=allowed, retry_after=retry_after)
                except WatchError:
                    continue  # another worker touched a bucket; recompute
        # Couldn't settle under contention — ask caller to retry shortly.
        return AcquireResult(allowed=False, retry_after=1.0)


def _per_minute_bucket(key: str, per_minute: int) -> Bucket:
    per_minute = max(1, per_minute)
    return Bucket(key=key, capacity=float(per_minute), refill_per_sec=per_minute / 60.0)


def build_send_buckets(domain: str, global_per_minute: int | None = None) -> list[Bucket]:
    """Global + per-recipient-domain buckets. ``global_per_minute`` (from the
    editable planner config) overrides the env default when provided."""
    return [
        _per_minute_bucket("rl:global", global_per_minute or settings.rate_global_per_minute),
        _per_minute_bucket(f"rl:dom:{domain}", settings.rate_per_domain_per_minute),
    ]


def _seconds_until_midnight_utc() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow.timestamp() + 86400) - now.timestamp()))


@dataclass(slots=True)
class DailyCapResult:
    allowed: bool
    used: int
    cap: int
    retry_after: float


def _daily_key(ip_pool: str) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"ipcap:{ip_pool}:{day}"


def daily_cap_usage(client: "redis_lib.Redis", ip_pool: str) -> int:
    try:
        val = client.get(_daily_key(ip_pool))
    except redis_lib.RedisError:
        return 0  # dashboard stays up even if Redis is momentarily unavailable
    return int(val) if val else 0


def is_under_daily_cap(
    client: "redis_lib.Redis", ip_pool: str, cap: int | None = None
) -> DailyCapResult:
    """Check (without consuming) whether ``ip_pool`` may send another today.

    We check before sending and record AFTER a successful send (see
    ``record_daily_send``) rather than reserving up-front, so failed/retried
    sends never consume warming quota. Concurrent workers may overshoot the cap
    by at most (concurrency - 1), which is immaterial for a warming safety net.
    """
    cap = settings.per_ip_daily_cap if cap is None else cap
    used = daily_cap_usage(client, ip_pool)
    if used >= cap:
        return DailyCapResult(
            allowed=False, used=used, cap=cap, retry_after=float(_seconds_until_midnight_utc())
        )
    return DailyCapResult(allowed=True, used=used, cap=cap, retry_after=0.0)


def record_daily_send(client: "redis_lib.Redis", ip_pool: str) -> int:
    """Count one successful send against today's per-IP quota."""
    key = _daily_key(ip_pool)
    used = client.incr(key)
    if used == 1:
        client.expire(key, _seconds_until_midnight_utc() + 3600)
    return used


# --- global warming cap counter (total across the whole pool) --------------

def _global_key() -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"warmsent:{day}"


def global_sent_today(client: "redis_lib.Redis") -> int:
    try:
        val = client.get(_global_key())
    except redis_lib.RedisError:
        return 0
    return int(val) if val else 0


def record_global_send(client: "redis_lib.Redis") -> int:
    """Count one successful send against today's TOTAL warming cap."""
    key = _global_key()
    used = client.incr(key)
    if used == 1:
        client.expire(key, _seconds_until_midnight_utc() + 3600)
    return used


_sync_redis: "redis_lib.Redis | None" = None


def get_redis() -> "redis_lib.Redis":
    """Process-wide sync Redis client (Celery workers)."""
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    return _sync_redis


def get_rate_limiter() -> RateLimiter:
    return RateLimiter(get_redis())
