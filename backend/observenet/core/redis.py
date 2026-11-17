from __future__ import annotations

import redis.asyncio as aioredis

from observenet.core.config import get_config

LIVE_CHANNEL = "observenet:live"
CURRENT_HASH = "observenet:current"
WORKER_HEARTBEAT_KEY = "observenet:heartbeat:{name}"
INGEST_QUEUE = "ingest"
MAINT_QUEUE = "maintenance"

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(
            get_config().redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=5,
            health_check_interval=30,
        )
    return _client


def set_redis(client: aioredis.Redis | None) -> None:
    """Test hook / explicit injection."""
    global _client
    _client = client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
