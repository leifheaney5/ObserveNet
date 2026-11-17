"""Transparent insights: plain comparisons with the method and sample sizes attached.

No opaque score. Every insight states what was compared and how; co-occurrence is
labelled as correlation, never as cause.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from observenet.models import Asset, Event, MetricRollup1h, MetricSeries
from observenet.services.views import primary_selection

MIN_HOURS = 6


async def _series_id(session: AsyncSession, source_id: int | None, metric: str, label: str | None) -> int | None:
    if source_id is None or label is None:
        return None
    return (
        await session.execute(
            select(MetricSeries.id).where(MetricSeries.source_id == source_id, MetricSeries.metric == metric,
                                          MetricSeries.label == label)  # fmt: skip
        )
    ).scalar_one_or_none()


async def _period_avg(session: AsyncSession, series_id: int, start: datetime, end: datetime) -> tuple[float | None, int, int]:
    row = (
        await session.execute(
            select(func.sum(MetricRollup1h.sum), func.sum(MetricRollup1h.count), func.count())
            .where(MetricRollup1h.series_id == series_id, MetricRollup1h.bucket >= start, MetricRollup1h.bucket < end)
        )
    ).one()
    total, n, hours = row
    return (total / n if n else None), int(n or 0), int(hours or 0)


def _fmt_bps(v: float) -> str:
    for unit, div in (("Gbit/s", 1e9), ("Mbit/s", 1e6), ("kbit/s", 1e3)):
        if v >= div:
            return f"{v / div:.1f} {unit}"
    return f"{v:.0f} bit/s"


async def build_insights(session: AsyncSession, *, now: datetime) -> dict[str, Any]:
    insights: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    primary = await primary_selection(session)
    day = timedelta(hours=24)
    hour0 = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    async def compare(key: str, title: str, series_id: int | None, fmt: Any, what: str) -> None:
        if series_id is None:
            skipped.append({"key": key, "reason": f"No series for {what} yet."})
            return
        cur, n_cur, h_cur = await _period_avg(session, series_id, hour0 - day, hour0)
        prev, n_prev, h_prev = await _period_avg(session, series_id, hour0 - 2 * day, hour0 - day)
        if cur is None or prev is None or h_cur < MIN_HOURS or h_prev < MIN_HOURS:
            skipped.append({"key": key, "reason": f"Needs at least {MIN_HOURS} h of data in both the last 24 h and the "
                                                    f"24 h before (have {h_cur} h and {h_prev} h)."})  # fmt: skip
            return
        change = (cur - prev) / prev * 100 if prev else None
        direction = "about the same as" if change is None or abs(change) < 10 else ("higher than" if change > 0 else "lower than")
        insights.append({
            "key": key, "title": title, "severity": "notice" if change is not None and abs(change) >= 50 else "info",
            "summary": f"{what} averaged {fmt(cur)} over the last 24 h, {direction} the previous 24 h ({fmt(prev)}"
                       + (f", {change:+.0f}%)." if change is not None else ")."),
            "method": f"Mean of hourly rollups. Last 24 h: {n_cur:,} samples across {h_cur} h; previous 24 h: "
                      f"{n_prev:,} samples across {h_prev} h. A difference under 10% is reported as 'about the same'.",
            "data": {"current": cur, "previous": prev, "change_pct": change, "samples": [n_cur, n_prev]},
        })  # fmt: skip

    sid, iface, gw = primary["source_id"], primary["interface"], primary["gateway_target_id"]
    lat_series = await _series_id(session, sid, "probe.latency_ms", f"target:{gw}" if gw else None)
    rx_series = await _series_id(session, sid, "net.rx_bps", iface)
    tx_series = await _series_id(session, sid, "net.tx_bps", iface)
    await compare("latency_trend", "Gateway latency vs. yesterday", lat_series, lambda v: f"{v:.1f} ms", "Gateway round-trip time")
    await compare("rx_trend", "Host download traffic vs. yesterday", rx_series, _fmt_bps, f"Receive throughput on {iface}")
    await compare("tx_trend", "Host upload traffic vs. yesterday", tx_series, _fmt_bps, f"Transmit throughput on {iface}")

    # Busiest hour (host NIC receive)
    if rx_series is not None:
        row = (
            await session.execute(
                select(MetricRollup1h.bucket, MetricRollup1h.avg, MetricRollup1h.max)
                .where(MetricRollup1h.series_id == rx_series, MetricRollup1h.bucket >= hour0 - day)
                .order_by(MetricRollup1h.avg.desc()).limit(1)  # fmt: skip
            )
        ).first()
        if row:
            insights.append({
                "key": "peak_hour", "title": "Busiest hour (host NIC)", "severity": "info",
                "summary": f"The hour starting {row[0].isoformat(timespec='minutes')} had the highest average receive "
                           f"throughput on {iface}: {_fmt_bps(row[1])} (peak 1 s sample {_fmt_bps(row[2])}).",
                "method": "Highest hourly-average bucket in the last 24 h. Host NIC traffic only — not the whole network.",
                "data": {"bucket": row[0], "avg_bps": row[1], "max_bps": row[2]},
            })  # fmt: skip

    # Co-occurrence of high traffic and elevated latency (correlation only)
    if rx_series is not None and lat_series is not None:
        rows = (
            await session.execute(
                text(
                    """
                    WITH t AS (SELECT bucket, avg FROM metric_rollups_1m WHERE series_id = :rx AND bucket >= :since),
                         l AS (SELECT bucket, avg FROM metric_rollups_1m WHERE series_id = :lat AND bucket >= :since),
                         th AS (SELECT (SELECT percentile_cont(0.9) WITHIN GROUP (ORDER BY avg) FROM t) AS p90,
                                       (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY avg) FROM l) AS med)
                    SELECT count(*) AS minutes,
                           count(*) FILTER (WHERE t.avg >= th.p90) AS busy,
                           count(*) FILTER (WHERE l.avg >= th.med * 1.5) AS slow,
                           count(*) FILTER (WHERE t.avg >= th.p90 AND l.avg >= th.med * 1.5) AS both
                    FROM t JOIN l USING (bucket), th
                    """
                ),
                {"rx": rx_series, "lat": lat_series, "since": now - day},
            )
        ).one()
        minutes, busy, slow, both = (int(x or 0) for x in rows)
        if minutes < 120 or busy == 0:
            skipped.append({"key": "traffic_latency", "reason": f"Needs 120+ minutes with both series (have {minutes})."})
        else:
            expected = busy * slow / minutes
            notable = both >= 5 and both > 2 * expected
            insights.append({
                "key": "traffic_latency", "title": "High traffic and elevated latency", "severity": "notice" if notable else "info",
                "summary": (f"Gateway latency was elevated in {both} of the {busy} busiest minutes "
                            f"(about {expected:.1f} would be expected if the two were unrelated). ")
                           + ("They tend to occur together, which is consistent with congestion but does not prove it."
                              if notable else "No notable association in the last 24 h."),
                "method": "Per-minute rollups over 24 h. 'Busy' = receive throughput in the top 10% of minutes; "
                          "'elevated' = latency ≥ 1.5× the median. Correlation only: host NIC traffic is one of many "
                          "possible contributors and causation is not established.",
                "data": {"minutes": minutes, "busy": busy, "slow": slow, "both": both, "expected_if_unrelated": expected},
            })  # fmt: skip

    # Inventory changes
    new_assets = list(
        (await session.execute(select(Asset).where(Asset.first_seen >= now - day, Asset.merged_into_id.is_(None)))).scalars()
    )
    total_assets = (await session.execute(select(func.count()).select_from(Asset).where(Asset.merged_into_id.is_(None)))).scalar_one()
    if total_assets:
        insights.append({
            "key": "new_devices", "title": "Devices first seen in the last 24 h", "severity": "notice" if new_assets else "info",
            "summary": f"{len(new_assets)} of {total_assets} known devices were first observed in the last 24 h."
                       + (" New sightings are observations, not threat verdicts; randomized MACs can create new entries "
                          "for existing devices." if new_assets else ""),
            "method": "Count of assets whose first sighting timestamp is within 24 h (passive sources only).",
            "data": {"asset_ids": [a.id for a in new_assets][:50]},
        })  # fmt: skip
        quiet = (
            await session.execute(
                select(func.count()).select_from(Asset).where(
                    Asset.merged_into_id.is_(None), Asset.managed.is_(False),
                    Asset.last_seen < now - day, Asset.last_seen >= now - timedelta(days=7))  # fmt: skip
            )
        ).scalar_one()
        if quiet:
            insights.append({
                "key": "quiet_devices", "title": "Devices not seen for over a day", "severity": "info",
                "summary": f"{quiet} device(s) seen within the last week have had no passive sighting for 24 h+.",
                "method": "Assets with last sighting between 24 h and 7 d ago. Absence of a sighting is not proof of being offline.",
                "data": {"count": quiet},
            })  # fmt: skip
    else:
        skipped.append({"key": "new_devices", "reason": "No devices observed yet."})

    changes = list(
        (
            await session.execute(
                select(Event).where(Event.kind == "posture.services_changed", Event.ts >= now - timedelta(days=7))
                .order_by(Event.ts.desc()).limit(20)  # fmt: skip
            )
        ).scalars()
    )
    if changes:
        added = sum(len(e.data.get("added", [])) for e in changes)
        removed = sum(len(e.data.get("removed", [])) for e in changes)
        insights.append({
            "key": "service_changes", "title": "Listening-service changes this week", "severity": "notice",
            "summary": f"{len(changes)} change(s) to listening services on monitored hosts in the last 7 d "
                       f"({added} added, {removed} removed). Latest: {changes[0].message}.",
            "method": "Diff of consecutive agent posture snapshots (socket table). Only hosts running an agent are covered.",
            "data": {"events": [{"ts": e.ts, "asset_id": e.asset_id, **e.data} for e in changes[:5]]},
        })  # fmt: skip
    return {"generated_at": now, "insights": insights, "not_computed": skipped}
