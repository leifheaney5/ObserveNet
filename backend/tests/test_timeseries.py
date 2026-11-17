"""Rollups, history bucketing, partitions and retention against real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text

from observenet.core.db import session_scope
from observenet.core.time import utcnow
from observenet.models import MetricRollup1h, MetricRollup1m, MetricSample, ProbeResult
from observenet.services import timeseries as ts
from observenet.services.settings import RetentionSettings
from tests import helpers as h


def hour_base(hours_ago: int = 3) -> datetime:
    return utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours_ago)


async def make_series(source_id: int, metric: str = "net.rx_bps", label: str = "eth0") -> int:
    async with session_scope() as session:
        return await ts.ensure_series(session, source_id=source_id, metric=metric, label=label, unit="bit/s",
                                      observation_point="host_nic", quality="measured", interval_s=1.0, asset_id=None)  # fmt: skip


async def insert(series_id: int, points: list[tuple[datetime, float]]) -> int:
    async with session_scope() as session:
        return await ts.insert_samples(
            session, [{"series_id": series_id, "ts": t, "value": v} for t, v in points]
        )


async def rollups(model: Any) -> list[Any]:
    async with session_scope() as session:
        return list((await session.execute(select(model).order_by(model.series_id, model.bucket))).scalars())


def as_tuples(rows: list[Any]) -> list[tuple[Any, ...]]:
    return [(r.series_id, r.bucket, r.avg, r.min, r.max, r.sum, r.count) for r in rows]


# ---------------------------------------------------------------- rollups


async def test_minute_rollup_is_exact() -> None:
    series = await make_series(await h.make_source())
    base = hour_base()
    await insert(
        series, [(base + timedelta(seconds=s), v) for s, v in [(0, 1.0), (20, 2.0), (59, 3.0), (60, 10.0)]]
    )
    async with session_scope() as session:
        await ts.run_rollups(session, now=utcnow())
    rows = await rollups(MetricRollup1m)
    assert as_tuples(rows) == [
        (series, base, 2.0, 1.0, 3.0, 6.0, 3),
        (series, base + timedelta(minutes=1), 10.0, 10.0, 10.0, 10.0, 1),
    ]


async def test_rollups_are_idempotent() -> None:
    series = await make_series(await h.make_source())
    base = hour_base()
    await insert(series, [(base + timedelta(seconds=s), float(s)) for s in range(0, 600, 7)])
    now = utcnow()
    async with session_scope() as session:
        await ts.run_rollups(session, now=now)
    first_1m, first_1h = as_tuples(await rollups(MetricRollup1m)), as_tuples(await rollups(MetricRollup1h))
    for _ in range(2):  # retries / duplicate scheduler ticks
        async with session_scope() as session:
            await ts.run_rollups(session, now=now)
    assert as_tuples(await rollups(MetricRollup1m)) == first_1m
    assert as_tuples(await rollups(MetricRollup1h)) == first_1h
    assert len(first_1m) == 10 and len(first_1h) == 1


async def test_late_data_inside_the_grace_window_is_absorbed() -> None:
    series = await make_series(await h.make_source())
    now = utcnow().replace(second=30, microsecond=0)
    minute = now.replace(second=0) - timedelta(minutes=1)  # the last complete minute
    await insert(series, [(minute + timedelta(seconds=5), 10.0)])
    async with session_scope() as session:
        await ts.run_rollups(session, now=now)
    assert [(r.avg, r.count) for r in await rollups(MetricRollup1m)] == [(10.0, 1)]
    await insert(series, [(minute + timedelta(seconds=40), 30.0)])  # a buffered batch arrives late
    async with session_scope() as session:
        await ts.run_rollups(session, now=now + timedelta(seconds=20))
    assert [(r.avg, r.min, r.max, r.count) for r in await rollups(MetricRollup1m)] == [(20.0, 10.0, 30.0, 2)]
    assert [(r.avg, r.count) for r in await rollups(MetricRollup1h)] == [(20.0, 2)]


async def test_incomplete_current_minute_is_not_rolled_up() -> None:
    series = await make_series(await h.make_source())
    now = utcnow().replace(second=30, microsecond=0)
    await insert(series, [(now - timedelta(seconds=10), 5.0)])
    async with session_scope() as session:
        await ts.run_rollups(session, now=now)
    assert await rollups(MetricRollup1m) == []


async def test_hourly_rollup_is_weighted_by_sample_count() -> None:
    series = await make_series(await h.make_source())
    base = hour_base()
    points = [(base + timedelta(seconds=1), 10.0)]  # minute 0: one sample of 10
    points += [
        (base + timedelta(minutes=1, seconds=s), 20.0) for s in (1, 2, 3)
    ]  # minute 1: three samples of 20
    await insert(series, points)
    async with session_scope() as session:
        await ts.run_rollups(session, now=utcnow())
    (hour,) = await rollups(MetricRollup1h)
    assert hour.bucket == base
    assert hour.avg == pytest.approx(70.0 / 4)  # 17.5, not the unweighted mean of minute averages (15)
    assert (hour.min, hour.max, hour.sum, hour.count) == (10.0, 20.0, 70.0, 4)


async def test_rollups_keep_series_separate() -> None:
    source = await h.make_source()
    rx, tx = await make_series(source, "net.rx_bps"), await make_series(source, "net.tx_bps")
    base = hour_base()
    await insert(rx, [(base, 100.0)])
    await insert(tx, [(base, 7.0)])
    async with session_scope() as session:
        await ts.run_rollups(session, now=utcnow())
    assert {(r.series_id, r.avg) for r in await rollups(MetricRollup1m)} == {(rx, 100.0), (tx, 7.0)}


# ---------------------------------------------------------------- history queries


def test_resolution_selection_never_scans_raw_for_coarse_buckets() -> None:
    assert ts.resolution_for(1).name == "raw" and ts.resolution_for(30).name == "raw"
    assert ts.resolution_for(60).name == "1m" and ts.resolution_for(1800).name == "1m"
    assert ts.resolution_for(3600).name == "1h" and ts.resolution_for(86400).name == "1h"


def test_auto_bucket_scales_with_range() -> None:
    assert ts.choose_bucket(60, None) == 1  # 1 minute  -> 1 s points
    assert ts.choose_bucket(15 * 60, None) == 5
    assert ts.choose_bucket(3600, None) == 10
    assert ts.choose_bucket(24 * 3600, None) == 300
    assert ts.choose_bucket(7 * 24 * 3600, None) == 1800
    assert ts.choose_bucket(366 * 24 * 3600, None) == 86400
    for range_s in (60, 900, 3600, 86400, 7 * 86400):
        assert range_s / ts.choose_bucket(range_s, None) <= 360


def test_bucket_validation() -> None:
    with pytest.raises(ts.HistoryQueryError, match="bucket must be one of"):
        ts.choose_bucket(3600, 7)
    with pytest.raises(ts.HistoryQueryError, match="more than 1500 points"):
        ts.choose_bucket(3600, 1)
    assert ts.choose_bucket(1500, 1) == 1


def test_range_validation() -> None:
    now = utcnow()
    with pytest.raises(ts.HistoryQueryError, match="after"):
        ts.validate_range(now, now)
    with pytest.raises(ts.HistoryQueryError, match="after"):
        ts.validate_range(now, now - timedelta(seconds=1))
    with pytest.raises(ts.HistoryQueryError, match="366 days"):
        ts.validate_range(now - timedelta(days=400), now)
    with pytest.raises(ts.HistoryQueryError, match="timezone"):
        ts.validate_range(datetime(2026, 1, 1), datetime(2026, 1, 2))


async def test_history_buckets_are_epoch_aligned_and_gaps_are_null_not_zero() -> None:
    series = await make_series(await h.make_source())
    base = hour_base(1)
    await insert(series, [
        (base + timedelta(seconds=0), 10.0), (base + timedelta(seconds=4), 20.0),  # bucket 0
        (base + timedelta(seconds=5), 40.0),  # bucket 1 (exact boundary belongs to the next bucket)
        # bucket 2 (10..15 s): nothing measured
        (base + timedelta(seconds=17), 7.0),  # bucket 3
    ])  # fmt: skip
    async with session_scope() as session:
        res, points = await ts.query_history(session, series_ids=[series], start=base + timedelta(seconds=2),
                                             end=base + timedelta(seconds=20), bucket_s=5)  # fmt: skip
    assert (res.name, res.bucket_s) == ("raw", 5)
    pts = points[series]
    assert [p["t"] for p in pts] == [
        base + timedelta(seconds=s) for s in (0, 5, 10, 15)
    ]  # floor(start) aligned
    # the first bucket only includes samples >= start (2 s), so the 10.0 at 0 s is excluded
    assert [(p["avg"], p["min"], p["max"], p["n"]) for p in pts] == [
        (20.0, 20.0, 20.0, 1), (40.0, 40.0, 40.0, 1), (None, None, None, 0), (7.0, 7.0, 7.0, 1),
    ]  # fmt: skip


async def test_history_reads_minute_and_hour_rollups_with_weighted_averages() -> None:
    series = await make_series(await h.make_source())
    base = hour_base(4)
    points = [(base + timedelta(minutes=m, seconds=s), float(m)) for m in range(0, 10) for s in range(m + 1)]
    await insert(series, points)  # minute m has m+1 samples of value m
    async with session_scope() as session:
        await ts.run_rollups(session, now=utcnow())
        res, out = await ts.query_history(
            session, series_ids=[series], start=base, end=base + timedelta(minutes=10), bucket_s=300
        )
        res_h, out_h = await ts.query_history(
            session, series_ids=[series], start=base, end=base + timedelta(hours=2), bucket_s=3600
        )
    assert res.name == "1m"
    first, second = out[series]
    # bucket 0 = minutes 0..4: values weighted by their sample counts
    assert first["n"] == 1 + 2 + 3 + 4 + 5
    assert first["avg"] == pytest.approx((0 * 1 + 1 * 2 + 2 * 3 + 3 * 4 + 4 * 5) / 15)
    assert (first["min"], first["max"]) == (0.0, 4.0)
    assert second["n"] == 6 + 7 + 8 + 9 + 10 and second["max"] == 9.0
    assert res_h.name == "1h"
    assert [p["n"] for p in out_h[series]] == [55, 0] and out_h[series][1]["avg"] is None


async def test_history_with_no_series_returns_empty() -> None:
    async with session_scope() as session:
        res, out = await ts.query_history(
            session, series_ids=[], start=utcnow() - timedelta(minutes=5), end=utcnow(), bucket_s=None
        )
    assert out == {} and res.name == "raw"


# ---------------------------------------------------------------- REST


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


async def test_history_endpoint_returns_measured_points_with_provenance(
    admin_client: httpx.AsyncClient,
) -> None:
    sid = await h.make_source()
    now = utcnow().replace(microsecond=0)
    await h.process(
        sid, h.payload(samples=[h.sample("net.rx_bps", 1000.0 * i, h.ago(10 - i, now)) for i in range(1, 6)])
    )
    resp = await admin_client.get(
        "/api/v1/metrics/history",
        params={"metric": "net.rx_bps", "from": iso(h.ago(60, now)), "to": iso(now), "bucket": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolution"] == "raw" and body["bucket_s"] == 1
    (series,) = body["series"]
    assert series["series"]["unit"] == "bit/s" and series["series"]["observation_point"] == "host_nic"
    measured = [p["avg"] for p in series["points"] if p["n"]]
    assert measured == [1000.0, 2000.0, 3000.0, 4000.0, 5000.0]
    assert len(series["points"]) == 60 and sum(1 for p in series["points"] if p["avg"] is None) == 55


@pytest.mark.parametrize(
    ("params", "needle"),
    [
        ({"from": -3600, "to": 0, "bucket": 1}, "more than 1500 points"),
        ({"from": 0, "to": -3600}, "after"),
        ({"from": -400 * 86400, "to": 0}, "366 days"),
        ({"from": -3600, "to": 0, "bucket": 7}, "bucket must be one of"),
    ],
)
async def test_history_endpoint_rejects_bad_ranges_with_422(
    admin_client: httpx.AsyncClient, params: dict[str, int], needle: str
) -> None:
    now = utcnow()
    query: dict[str, Any] = {"metric": "net.rx_bps", "from": iso(now + timedelta(seconds=params["from"])),
                             "to": iso(now + timedelta(seconds=params["to"]))}  # fmt: skip
    if "bucket" in params:
        query["bucket"] = params["bucket"]
    resp = await admin_client.get("/api/v1/metrics/history", params=query)
    assert resp.status_code == 422 and needle in resp.text


async def test_history_endpoint_missing_or_malformed_params_422(admin_client: httpx.AsyncClient) -> None:
    assert (
        await admin_client.get("/api/v1/metrics/history", params={"metric": "net.rx_bps"})
    ).status_code == 422
    resp = await admin_client.get(
        "/api/v1/metrics/history", params={"metric": "net.rx_bps", "from": "yesterday", "to": "now"}
    )
    assert resp.status_code == 422


async def test_out_of_retention_raw_query_returns_empty_points_and_a_note(
    admin_client: httpx.AsyncClient,
) -> None:
    sid = await h.make_source()
    await h.process(sid, h.payload(samples=[h.sample("net.rx_bps", 5.0, utcnow())]))
    now = utcnow()
    resp = await admin_client.get("/api/v1/metrics/history", params={
        "metric": "net.rx_bps", "from": iso(now - timedelta(hours=72)), "to": iso(now - timedelta(hours=71)), "bucket": 5})  # fmt: skip
    assert resp.status_code == 200
    body = resp.json()
    assert all(p["avg"] is None and p["n"] == 0 for p in body["series"][0]["points"])
    assert "Raw samples are kept for 48 h" in body["note"]


async def test_history_for_an_unmeasured_metric_says_so(admin_client: httpx.AsyncClient) -> None:
    now = utcnow()
    resp = await admin_client.get(
        "/api/v1/metrics/history",
        params={"metric": "wan.rx_bps", "from": iso(h.ago(300, now)), "to": iso(now)},
    )
    assert resp.status_code == 200
    assert resp.json()["series"] == [] and "Nothing has been measured" in resp.json()["note"]


# ---------------------------------------------------------------- partitions + retention


async def partition_names() -> set[str]:
    async with session_scope() as session:
        return set((await session.execute(text(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = 'metric_samples'"))).scalars())  # fmt: skip


async def test_ensure_partitions_creates_daily_partitions_ahead_and_is_idempotent() -> None:
    now = datetime(2031, 3, 10, 15, 30, tzinfo=UTC)
    async with session_scope() as session:
        created = await ts.ensure_partitions(session, now=now)
    assert created == ["metric_samples_p20310310", "metric_samples_p20310311", "metric_samples_p20310312"]
    async with session_scope() as session:
        assert await ts.ensure_partitions(session, now=now) == []
    assert set(created) <= await partition_names()
    series = await make_series(await h.make_source())
    await insert(series, [(now, 1.0)])
    async with session_scope() as session:  # the row landed in its day partition, not in the default one
        assert (
            await session.execute(text("SELECT count(*) FROM metric_samples_p20310310"))
        ).scalar_one() == 1
        assert (await session.execute(text("SELECT count(*) FROM metric_samples_default"))).scalar_one() == 0


async def test_partition_creation_survives_rows_already_in_the_default_partition() -> None:
    now = datetime(2031, 5, 1, 12, 0, tzinfo=UTC)
    series = await make_series(await h.make_source())
    await insert(series, [(now, 1.0)])  # no partition for that day yet -> default partition
    async with session_scope() as session:
        created = await ts.ensure_partitions(session, now=now)
    assert "metric_samples_p20310501" not in created  # cannot carve it out; keeps using the default
    assert "metric_samples_p20310502" in created
    async with session_scope() as session:
        assert (await session.execute(select(MetricSample))).scalars().one().value == 1.0  # nothing lost


async def test_retention_drops_old_day_partitions_and_bounds_every_table() -> None:
    sid = await h.make_source()
    target = await h.make_target()
    series = await make_series(sid)
    now = utcnow()
    retention = RetentionSettings()  # 48 h raw, 90 d minute rollups, 365 d hour rollups
    old_day = now - timedelta(days=5)
    async with session_scope() as session:
        await ts.ensure_partitions(session, now=old_day, days_ahead=0)
        await ts.ensure_partitions(session, now=now, days_ahead=0)
    old_partition, today_partition = ts.partition_name(old_day), ts.partition_name(now)
    await insert(series, [(old_day, 1.0), (now - timedelta(minutes=1), 2.0)])
    async with session_scope() as session:
        # a straggler that landed in the default partition, older than raw retention
        await session.execute(text("INSERT INTO metric_samples_default (series_id, ts, value) VALUES (:s, :t, 3.0)"),
                              {"s": series, "t": now - timedelta(days=9)})  # fmt: skip
        for model, age_days, keep_days in ((MetricRollup1m, 100, 10), (MetricRollup1h, 400, 100)):
            for days in (age_days, keep_days):
                session.add(
                    model(
                        series_id=series,
                        bucket=now - timedelta(days=days),
                        avg=1,
                        min=1,
                        max=1,
                        sum=1,
                        count=1,
                    )
                )
        for hours in (72, 1):
            session.add(ProbeResult(target_id=target, source_id=sid, ts=now - timedelta(hours=hours), success=True,
                                    latency_ms=1.0, method="t"))  # fmt: skip

    async with session_scope() as session:
        stats = await ts.apply_retention(session, now=now, retention=retention)

    names = await partition_names()
    assert old_partition not in names and today_partition in names and "metric_samples_default" in names
    assert stats["raw_partitions_dropped"] == 1
    assert stats["raw_default_rows_deleted"] == 1
    assert (
        stats["rollup_1m_deleted"] == 1
        and stats["rollup_1h_deleted"] == 1
        and stats["probe_results_deleted"] == 1
    )
    async with session_scope() as session:
        assert [s.value for s in (await session.execute(select(MetricSample))).scalars()] == [2.0]
        assert len((await session.execute(select(MetricRollup1m))).scalars().all()) == 1
        assert len((await session.execute(select(MetricRollup1h))).scalars().all()) == 1
        assert len((await session.execute(select(ProbeResult))).scalars().all()) == 1


async def test_retention_keeps_posture_snapshots_that_are_finding_evidence() -> None:
    sid = await h.make_source()
    old = utcnow() - timedelta(days=200)
    await h.process(
        sid,
        h.payload(posture=h.posture(old, listening=h.listening(("tcp", "0.0.0.0", 6379)))),
        received_at=old,
    )
    await h.process(sid, h.payload(posture=h.posture(old + timedelta(days=1), listening=h.listening(("tcp", "0.0.0.0", 6379), ("tcp", "127.0.0.1", 1)))),
                    received_at=old + timedelta(days=1))  # fmt: skip
    async with session_scope() as session:
        stats = await ts.apply_retention(session, now=utcnow(), retention=RetentionSettings())
        remaining = (await session.execute(text("SELECT count(*) FROM posture_snapshots"))).scalar_one()
    # the first snapshot backs the finding's initial evidence and must survive; the second one does not
    assert stats["posture_snapshots_deleted"] == 1 and remaining == 1
