"""Container health check: ``python -m observenet.workers.healthcheck worker|scheduler``.

Exits 0 when the named process has refreshed its Redis heartbeat recently.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import redis

from observenet.core.config import get_config
from observenet.core.redis import WORKER_HEARTBEAT_KEY

MAX_AGE_S = {"worker": 60, "scheduler": 20}


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "worker"
    if name not in MAX_AGE_S:
        print(f"unknown component {name!r}", file=sys.stderr)
        return 2
    client = redis.Redis.from_url(get_config().redis_url, decode_responses=True, socket_timeout=3)
    raw = client.get(WORKER_HEARTBEAT_KEY.format(name=name))
    if not raw:
        print("no heartbeat", file=sys.stderr)
        return 1
    age = (datetime.now(UTC) - datetime.fromisoformat(str(raw))).total_seconds()
    if age > MAX_AGE_S[name]:
        print(f"heartbeat is {age:.0f}s old", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
