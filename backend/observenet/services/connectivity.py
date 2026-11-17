"""Connectivity maths: rolling-window latency/loss/jitter and multi-signal status.

All functions are pure so the exact numbers shown in the UI are unit-testable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.models import ProbeResult, ProbeTarget


@dataclass(frozen=True)
class ProbePoint:
    ts: datetime
    success: bool
    latency_ms: float | None


@dataclass(frozen=True)
class WindowStats:
    window_s: int
    sample_size: int
    successes: int
    loss_pct: float | None
    latency_avg_ms: float | None
    latency_min_ms: float | None
    latency_max_ms: float | None
    latency_p95_ms: float | None
    jitter_ms: float | None
    first_ts: datetime | None
    last_ts: datetime | None


def compute_jitter(latencies: Sequence[float]) -> float | None:
    """Mean absolute difference between consecutive successful probes (ms).

    Needs at least two latencies; lost probes are skipped rather than treated as zero.
    """
    if len(latencies) < 2:
        return None
    deltas = [abs(b - a) for a, b in zip(latencies, latencies[1:], strict=False)]
    return sum(deltas) / len(deltas)


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def compute_window_stats(points: Sequence[ProbePoint], *, now: datetime, window_s: int) -> WindowStats:
    """Statistics over probes with ``now - window_s < ts <= now``.

    With zero samples every statistic is ``None`` (unknown) — an empty window is not 0 % loss.
    A timed-out probe counts as lost; its latency is excluded from latency/jitter statistics.
    """
    start = now - timedelta(seconds=window_s)
    window = sorted((p for p in points if start < p.ts <= now), key=lambda p: p.ts)
    n = len(window)
    if n == 0:
        return WindowStats(window_s, 0, 0, None, None, None, None, None, None, None, None)
    lat = [p.latency_ms for p in window if p.success and p.latency_ms is not None]
    successes = sum(1 for p in window if p.success)
    ordered = sorted(lat)
    return WindowStats(
        window_s=window_s,
        sample_size=n,
        successes=successes,
        loss_pct=100.0 * (n - successes) / n,
        latency_avg_ms=sum(lat) / len(lat) if lat else None,
        latency_min_ms=ordered[0] if ordered else None,
        latency_max_ms=ordered[-1] if ordered else None,
        latency_p95_ms=_percentile(ordered, 0.95) if ordered else None,
        jitter_ms=compute_jitter(lat),
        first_ts=window[0].ts,
        last_ts=window[-1].ts,
    )


@dataclass(frozen=True)
class Signal:
    """Recent outcomes of one independent external check (newest last)."""

    name: str
    recent: Sequence[bool]
    fresh: bool


@dataclass(frozen=True)
class InternetStatus:
    status: str  # reachable | degraded | unreachable | unknown
    reason: str
    signals_total: int
    signals_ok: int


def classify_internet(
    signals: Sequence[Signal], *, consecutive_failures: int = 3, min_signals: int = 2
) -> InternetStatus:
    """Combine independent external signals. One failing endpoint is never an outage:
    ``unreachable`` needs >= ``min_signals`` fresh signals that each failed their last
    ``consecutive_failures`` checks."""
    fresh = [s for s in signals if s.fresh and s.recent]
    if not fresh:
        why = (
            "No external checks are configured." if not signals else "No recent results from external checks."
        )
        return InternetStatus("unknown", why, len(signals), 0)
    ok = [s for s in fresh if s.recent[-1]]
    if len(ok) == len(fresh):
        return InternetStatus(
            "reachable", f"All {len(fresh)} external checks succeeded.", len(fresh), len(ok)
        )
    if ok:
        failing = ", ".join(s.name for s in fresh if not s.recent[-1])
        return InternetStatus(
            "degraded", f"{len(ok)} of {len(fresh)} external checks succeed; failing: {failing}. "
            "The failing endpoint(s) may be down or filtered rather than your connection.", len(fresh), len(ok),
        )  # fmt: skip
    hard_down = [
        s
        for s in fresh
        if len(s.recent) >= consecutive_failures and not any(s.recent[-consecutive_failures:])
    ]
    if len(fresh) >= min_signals and len(hard_down) == len(fresh):
        return InternetStatus(
            "unreachable", f"All {len(fresh)} independent external checks failed {consecutive_failures}+ times in a row.",
            len(fresh), 0,
        )  # fmt: skip
    if len(fresh) < min_signals:
        return InternetStatus(
            "degraded", "The only external check is failing; one signal cannot distinguish an endpoint "
            "problem from an internet outage.", len(fresh), 0,
        )  # fmt: skip
    return InternetStatus("degraded", "External checks are failing but not yet consistently.", len(fresh), 0)


async def load_points(
    session: AsyncSession, *, target_id: int, since: datetime, source_id: int | None = None, limit: int = 5000
) -> list[ProbePoint]:
    stmt = (
        select(ProbeResult.ts, ProbeResult.success, ProbeResult.latency_ms)
        .where(ProbeResult.target_id == target_id, ProbeResult.ts > since)
        .order_by(ProbeResult.ts.desc())
        .limit(limit)
    )
    if source_id is not None:
        stmt = stmt.where(ProbeResult.source_id == source_id)
    rows = (await session.execute(stmt)).all()
    return [ProbePoint(ts, success, latency) for ts, success, latency in reversed(rows)]


async def internet_status(
    session: AsyncSession, *, now: datetime, interval_slack: float = 3.0
) -> InternetStatus:
    targets = list(
        (
            await session.execute(
                select(ProbeTarget).where(ProbeTarget.scope == "external", ProbeTarget.enabled.is_(True))
            )
        ).scalars()
    )
    signals = []
    for t in targets:
        horizon = max(t.interval_s * 6, 60)
        pts = await load_points(session, target_id=t.id, since=now - timedelta(seconds=horizon), limit=10)
        fresh = bool(pts) and (now - pts[-1].ts).total_seconds() <= max(t.interval_s * interval_slack, 15)
        signals.append(Signal(t.name, [p.success for p in pts], fresh))
    return classify_internet(signals)
