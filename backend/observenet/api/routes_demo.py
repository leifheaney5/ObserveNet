"""DEMO_MODE-only controls. This router is not mounted in real mode."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from observenet.auth.deps import AdminUser, CurrentUser
from observenet.core.redis import get_redis
from observenet.services.demo import DEMO_STATE_KEY, FIXABLE

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


class DemoFix(BaseModel):
    item: Literal["windows-firewall", "smb1", "redis-bind"]
    fixed: bool


@router.get("/state")
async def demo_state(_: CurrentUser) -> dict[str, object]:
    fixed = set(await get_redis().smembers(DEMO_STATE_KEY))  # type: ignore[misc]
    return {"items": [{"item": k, "label": v, "fixed": k in fixed} for k, v in FIXABLE.items()],
            "note": "Simulates an operator changing a setting on a synthetic host so the Verify loop can be shown."}  # fmt: skip


@router.post("/fix")
async def demo_fix(body: DemoFix, _: AdminUser) -> dict[str, object]:
    if body.item not in FIXABLE:
        raise HTTPException(422, "unknown demo item")
    redis = get_redis()
    if body.fixed:
        await redis.sadd(DEMO_STATE_KEY, body.item)  # type: ignore[misc]
    else:
        await redis.srem(DEMO_STATE_KEY, body.item)  # type: ignore[misc]
    return {"item": body.item, "fixed": body.fixed}
