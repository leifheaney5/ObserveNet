"""Series registry, sample writes, bucketed history reads, rollups, retention, partitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.models import (
    CollectorHeartbeat,
    Event,
    IngestBatch,
    JobState,
    MetricRollup1h,
    MetricRollup1m,
    MetricSample,
    MetricSeries,
    PostureSnapshot,
    ProbeResult,
)
from observenet.services.settings import RetentionSettings

MAX_POINTS = 1500
MAX_RANGE = timedelta(days=366)
ALLOWED_BUCKETS = (1, 5, 10, 15, 30, 60, 300, 900, 1800, 3600, 10800, 21600, 86400)

# (source_id, metric, label) -> series_id; safe to cache because series rows are immutable keys.
_series_cache: dict[tuple[int, str, str], int] = {}


def clear_caches() -> None:
    _series_cache.clear()


class HistoryQueryError(ValueError):
    pass


async def ensure_series(
    session: AsyncSession,
    *,
    source_id: int,
    metric: str,
    label: str,
    unit: str,
    observation_point: str,
    quality: str,
    interval_s: float,
    asset_id: int | None,
    coverage: str | None = None,
    probe_target_id: int | None = None,
) -> int:
    key = (source_id, metric, label)
    cached = _series_cache.get(key)
    if cached is not None:
        return cached
    stmt = (
        pg_insert(MetricSeries)
        .values(
            source_id=source_id,
            metric=metric,
            label=label,
            unit=unit,
            observation_point=observation_point,
            quality=quality,
            interval_s=interval_s,
            asset_id=asset_id,
            coverage=coverage,
            probe_target_id=probe_target_id,
        )  # fmt: skip
        .on_conflict_do_update(
            index_elements=["source_id", "metric", "label"],
            set_={"asset_id": asset_id, "interval_s": interval_s, "unit": unit, "quality": quality},
        )
        .returning(MetricSeries.id)
    )
    series_id = (await session.execute(stmt)).scalar_one()
    _series_cache[key] = series_id
    return series_id


async def insert_samples(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Idempotent bulk insert: a replayed (series, ts) is ignored."""
    if not rows:
        return 0
    # RETURNING gives an exact count of newly inserted rows (rowcount is unreliable for
    # multi-row INSERT ... ON CONFLICT under psycopg).
    result = await session.execute(
        pg_insert(MetricSample)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["series_id", "ts"])
        .returning(MetricSample.series_id)
    )
    inserted = len(result.all())
    by_series: dict[int, tuple[datetime, datetime]] = {}
    for r in rows:
        lo, hi = by_series.get(r["series_id"], (r["ts"], r["ts"]))
        by_series[r["series_id"]] = (min(lo, r["ts"]), max(hi, r["ts"]))
    for series_id, (lo, hi) in by_series.items():
        await session.execute(
            text(
                "UPDATE metric_series SET first_ts = LEAST(COALESCE(first_ts, :lo), :lo), "
                "last_ts = GREATEST(COALESCE(last_ts, :hi), :hi) "
                "WHERE id = :id AND (last_ts IS NULL OR last_ts < :hi - interval '30 seconds' OR first_ts IS NULL)"
            ),
            {"lo": lo, "hi": hi, "id": series_id},
        )
    return inserted


# ---------------------------------------------------------------- history reads


@dataclass(frozen=True)
class Resolution:
    name: str  # raw | 1m | 1h
    bucket_s: int


def choose_bucket(range_s: float, requested: int | None) -> int:
    if requested is not None:
        if requested not in ALLOWED_BUCKETS:
            raise HistoryQueryError(f"bucket must be one of {list(ALLOWED_BUCKETS)} seconds")
        if range_s / requested > MAX_POINTS:
            raise HistoryQueryError(
                f"range/bucket would return more than {MAX_POINTS} points; choose a larger bucket or shorter range"
            )
        return requested
    for b in ALLOWED_BUCKETS:
        if range_s / b <= 360:
            return b
    return ALLOWED_BUCKETS[-1]


def resolution_for(bucket_s: int) -> Resolution:
    """Never scan raw samples for coarse buckets: pick the smallest table that satisfies it."""
    if bucket_s < 60:
        return Resolution("raw", bucket_s)
    if bucket_s < 3600:
        return Resolution("1m", bucket_s)
    return Resolution("1h", bucket_s)


def validate_range(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise HistoryQueryError("from/to must include a timezone offset")
    if end <= start:
        raise HistoryQueryError("'to' must be after 'from'")
    if end - start > MAX_RANGE:
        raise HistoryQueryError("range may not exceed 366 days")


def _floor(ts: datetime, bucket_s: int) -> datetime:
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - epoch % bucket_s, UTC)


async def query_history(
    session: AsyncSession, *, series_ids: list[int], start: datetime, end: datetime, bucket_s: int | None
) -> tuple[Resolution, dict[int, list[dict[str, Any]]]]:
    """Bucketed (avg/min/max/count) points per series, gap-filled with nulls.

    Buckets are aligned to the epoch; a bucket with no samples has ``avg=None`` so charts
    draw a gap instead of interpolating data that was never measured.
    """
    validate_range(start, end)
    bucket = choose_bucket((end - start).total_seconds(), bucket_s)
    res = resolution_for(bucket)
    out: dict[int, list[dict[str, Any]]] = {sid: [] for sid in series_ids}
    if not series_ids:
        return res, out

    origin = datetime(1970, 1, 1, tzinfo=UTC)
    width = timedelta(seconds=bucket)
    if res.name == "raw":
        b = func.date_bin(width, MetricSample.ts, origin).label("b")
        stmt = (
            select(
                MetricSample.series_id,
                b,
                func.avg(MetricSample.value),
                func.min(MetricSample.value),
                func.max(MetricSample.value),
                func.count(),
            )  # fmt: skip
            .where(MetricSample.series_id.in_(series_ids), MetricSample.ts >= start, MetricSample.ts < end)
            .group_by(MetricSample.series_id, b)
        )
    else:
        table = MetricRollup1m if res.name == "1m" else MetricRollup1h
        b = func.date_bin(width, table.bucket, origin).label("b")
        stmt = (
            select(
                table.series_id,
                b,
                func.sum(table.sum) / func.nullif(func.sum(table.count), 0),
                func.min(table.min),
                func.max(table.max),
                func.sum(table.count),
            )  # fmt: skip
            .where(table.series_id.in_(series_ids), table.bucket >= start, table.bucket < end)
            .group_by(table.series_id, b)
        )
    found: dict[tuple[int, datetime], tuple[float, float, float, int]] = {}
    for sid, bts, avg, mn, mx, n in (await session.execute(stmt)).all():
        found[(sid, bts)] = (avg, mn, mx, int(n))

    first = _floor(start, bucket)
    for sid in series_ids:
        t = first
        while t < end:
            hit = found.get((sid, t))
            out[sid].append(
                {"t": t, "avg": hit[0], "min": hit[1], "max": hit[2], "n": hit[3]}
                if hit
                else {"t": t, "avg": None, "min": None, "max": None, "n": 0}
            )
            t += width
    return res, out


# ---------------------------------------------------------------- rollups

_ROLLUP_1M = text(
    """
    INSERT INTO metric_rollups_1m (series_id, bucket, avg, min, max, sum, count)
    SELECT series_id, date_trunc('minute', ts), avg(value), min(value), max(value), sum(value), count(*)
    FROM metric_samples WHERE ts >= :start AND ts < :end
    GROUP BY series_id, date_trunc('minute', ts)
    ON CONFLICT (series_id, bucket) DO UPDATE
      SET avg = EXCLUDED.avg, min = EXCLUDED.min, max = EXCLUDED.max, sum = EXCLUDED.sum, count = EXCLUDED.count
    """
)
_ROLLUP_1H = text(
    """
    INSERT INTO metric_rollups_1h (series_id, bucket, avg, min, max, sum, count)
    SELECT series_id, date_trunc('hour', bucket), sum(sum) / NULLIF(sum(count), 0), min(min), max(max), sum(sum), sum(count)
    FROM metric_rollups_1m WHERE bucket >= :start AND bucket < :end
    GROUP BY series_id, date_trunc('hour', bucket)
    ON CONFLICT (series_id, bucket) DO UPDATE
      SET avg = EXCLUDED.avg, min = EXCLUDED.min, max = EXCLUDED.max, sum = EXCLUDED.sum, count = EXCLUDED.count
    """
)


async def _get_watermark(session: AsyncSession, key: str) -> datetime | None:
    row = await session.get(JobState, key)
    return datetime.fromisoformat(row.value["watermark"]) if row and row.value.get("watermark") else None


async def _set_watermark(session: AsyncSession, key: str, value: datetime) -> None:
    await session.execute(
        pg_insert(JobState)
        .values(key=key, value={"watermark": value.isoformat()})
        .on_conflict_do_update(index_elements=["key"], set_={"value": {"watermark": value.isoformat()}})
    )


async def run_rollups(session: AsyncSession, *, now: datetime, late_grace_s: int = 120) -> dict[str, int]:
    """Recompute complete minute/hour buckets since the watermark. Idempotent: every run
    re-derives each bucket in full from the finer table, so retries and late data are safe.

    The last ``late_grace_s`` seconds are recomputed on every run to absorb late batches.
    """
    minute_end = now.replace(second=0, microsecond=0)
    wm = await _get_watermark(session, "rollup_1m")
    earliest = now - timedelta(hours=48)
    start = max(wm - timedelta(seconds=late_grace_s), earliest) if wm else earliest
    start = start.replace(second=0, microsecond=0)
    r1 = await session.execute(_ROLLUP_1M, {"start": start, "end": minute_end})
    await _set_watermark(session, "rollup_1m", minute_end)

    hour_end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)  # include the partial hour
    hour_start = start.replace(minute=0)
    r2 = await session.execute(_ROLLUP_1H, {"start": hour_start, "end": hour_end})
    return {"rollup_1m_rows": r1.rowcount or 0, "rollup_1h_rows": r2.rowcount or 0}  # type: ignore[attr-defined]


# ---------------------------------------------------------------- partitions + retention


def partition_name(day: datetime) -> str:
    return f"metric_samples_p{day:%Y%m%d}"


async def ensure_partitions(session: AsyncSession, *, now: datetime, days_ahead: int = 2) -> list[str]:
    """Create daily partitions for today..today+N. If rows for a day already sit in the
    default partition the day keeps using it (retention then deletes those rows by range)."""
    created = []
    day0 = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(days_ahead + 1):
        day = day0 + timedelta(days=i)
        name = partition_name(day)
        exists = (await session.execute(text("SELECT to_regclass(:n)"), {"n": name})).scalar()
        if exists:
            continue
        try:
            async with session.begin_nested():
                await session.execute(
                    text(
                        f"CREATE TABLE {name} PARTITION OF metric_samples "  # noqa: S608 - name is derived from a date
                        f"FOR VALUES FROM ('{day.isoformat()}') TO ('{(day + timedelta(days=1)).isoformat()}')"
                    )
                )
            created.append(name)
        except Exception:  # noqa: BLE001, S112 - default partition already holds rows for that day
            continue
    return created


async def apply_retention(
    session: AsyncSession, *, now: datetime, retention: RetentionSettings
) -> dict[str, int]:
    """Bound every growing table. Raw samples are dropped a whole day-partition at a time
    (cheap, no bloat); stragglers in the default partition are deleted by range."""
    stats: dict[str, int] = {}
    raw_cutoff = now - timedelta(hours=retention.raw_hours)
    dropped = 0
    rows = (
        await session.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'metric_samples' "
                "AND c.relname ~ '^metric_samples_p[0-9]{8}$'"
            )
        )
    ).scalars()
    for name in rows:
        day = datetime.strptime(name[-8:], "%Y%m%d").replace(tzinfo=UTC)
        if day + timedelta(days=1) <= raw_cutoff:
            await session.execute(text(f"DROP TABLE IF EXISTS {name}"))  # noqa: S608 - validated by regex above
            dropped += 1
    stats["raw_partitions_dropped"] = dropped
    r = await session.execute(text("DELETE FROM metric_samples_default WHERE ts < :c"), {"c": raw_cutoff})
    stats["raw_default_rows_deleted"] = r.rowcount or 0  # type: ignore[attr-defined]

    async def _delete(model: Any, column: Any, cutoff: datetime, key: str) -> None:
        res = await session.execute(delete(model).where(column < cutoff))
        stats[key] = res.rowcount or 0  # type: ignore[attr-defined]

    await _delete(
        MetricRollup1m,
        MetricRollup1m.bucket,
        now - timedelta(days=retention.rollup_1m_days),
        "rollup_1m_deleted",
    )
    await _delete(
        MetricRollup1h,
        MetricRollup1h.bucket,
        now - timedelta(days=retention.rollup_1h_days),
        "rollup_1h_deleted",
    )
    await _delete(
        ProbeResult,
        ProbeResult.ts,
        now - timedelta(hours=retention.probe_results_hours),
        "probe_results_deleted",
    )
    await _delete(CollectorHeartbeat, CollectorHeartbeat.ts, raw_cutoff, "heartbeats_deleted")
    await _delete(IngestBatch, IngestBatch.received_at, raw_cutoff, "ingest_batches_deleted")
    await _delete(Event, Event.ts, now - timedelta(days=retention.events_days), "events_deleted")
    # Keep any snapshot still referenced as finding evidence (ON DELETE SET NULL would lose provenance).
    res = await session.execute(
        text(
            "DELETE FROM posture_snapshots s WHERE s.collected_at < :c "
            "AND NOT EXISTS (SELECT 1 FROM finding_evidence e WHERE e.snapshot_id = s.id)"
        ),
        {"c": now - timedelta(days=retention.posture_snapshots_days)},
    )
    stats["posture_snapshots_deleted"] = res.rowcount or 0  # type: ignore[attr-defined]
    _ = PostureSnapshot  # referenced for clarity
    return stats
