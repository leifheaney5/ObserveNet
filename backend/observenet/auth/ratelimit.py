from __future__ import annotations

import time

from observenet.core.redis import get_redis

_local: dict[str, tuple[int, float]] = {}


async def hit(key: str, *, limit: int, window_s: int = 60) -> bool:
    """Fixed-window counter. Returns True while under the limit.

    Uses Redis so limits hold across API processes; if Redis is unreachable it degrades
    to a per-process counter rather than failing open.
    """
    try:
        redis = get_redis()
        full_key = f"observenet:rl:{key}"
        count = await redis.incr(full_key)
        if count == 1:
            await redis.expire(full_key, window_s)
        return int(count) <= limit
    except Exception:  # noqa: BLE001
        now = time.monotonic()
        count, start = _local.get(key, (0, now))
        if now - start > window_s:
            count, start = 0, now
        _local[key] = (count + 1, start)
        return count + 1 <= limit
