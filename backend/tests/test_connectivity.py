"""Rolling-window latency / loss / jitter and multi-signal internet status (pure functions)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from observenet.services.connectivity import (
    ProbePoint,
    Signal,
    classify_internet,
    compute_jitter,
    compute_window_stats,
)

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def pt(seconds_ago: float, latency: float | None) -> ProbePoint:
    return ProbePoint(NOW - timedelta(seconds=seconds_ago), latency is not None, latency)


def test_jitter_is_mean_absolute_consecutive_difference() -> None:
    assert compute_jitter([10.0, 12.0, 9.0, 9.5]) == pytest.approx((2.0 + 3.0 + 0.5) / 3)
    assert compute_jitter([5.0, 5.0, 5.0]) == 0.0


@pytest.mark.parametrize("latencies", [[], [7.0]])
def test_jitter_unknown_with_fewer_than_two_samples(latencies: list[float]) -> None:
    assert compute_jitter(latencies) is None


def test_window_stats_exact_numbers() -> None:
    points = [pt(5, 10.0), pt(4, 20.0), pt(3, None), pt(2, 30.0), pt(1, None)]
    stats = compute_window_stats(points, now=NOW, window_s=60)
    assert stats.sample_size == 5
    assert stats.successes == 3
    assert stats.loss_pct == pytest.approx(40.0)  # 2 of 5 timed out
    assert stats.latency_avg_ms == pytest.approx(20.0)  # timeouts excluded from latency
    assert stats.latency_min_ms == 10.0 and stats.latency_max_ms == 30.0
    assert stats.latency_p95_ms == pytest.approx(29.0)  # linear interpolation over [10, 20, 30]
    assert stats.jitter_ms == pytest.approx(10.0)  # |20-10|, |30-20| — lost probes skipped, not zeroed
    assert stats.first_ts == NOW - timedelta(seconds=5) and stats.last_ts == NOW - timedelta(seconds=1)


def test_empty_window_is_unknown_not_zero_percent_loss() -> None:
    stats = compute_window_stats([], now=NOW, window_s=60)
    assert stats.sample_size == 0
    assert stats.loss_pct is None
    assert stats.latency_avg_ms is None and stats.jitter_ms is None and stats.last_ts is None


def test_all_probes_timed_out_is_100_percent_loss_with_unknown_latency() -> None:
    stats = compute_window_stats([pt(3, None), pt(2, None), pt(1, None)], now=NOW, window_s=10)
    assert stats.loss_pct == 100.0
    assert stats.latency_avg_ms is None and stats.latency_p95_ms is None and stats.jitter_ms is None


def test_window_boundaries_exclude_start_and_include_now() -> None:
    points = [
        ProbePoint(NOW - timedelta(seconds=60), True, 1.0),  # exactly window start: excluded
        ProbePoint(NOW - timedelta(seconds=59.999), True, 2.0),
        ProbePoint(NOW, True, 3.0),  # exactly now: included
        ProbePoint(NOW + timedelta(seconds=1), True, 99.0),  # from the future: excluded
    ]
    stats = compute_window_stats(points, now=NOW, window_s=60)
    assert stats.sample_size == 2
    assert stats.latency_min_ms == 2.0 and stats.latency_max_ms == 3.0


def test_window_stats_sorts_out_of_order_points_before_jitter() -> None:
    stats = compute_window_stats([pt(1, 30.0), pt(3, 10.0), pt(2, 20.0)], now=NOW, window_s=10)
    assert stats.jitter_ms == pytest.approx(10.0)


def test_single_sample_window() -> None:
    stats = compute_window_stats([pt(1, 4.2)], now=NOW, window_s=10)
    assert stats.loss_pct == 0.0 and stats.latency_p95_ms == 4.2 and stats.jitter_ms is None


# ---------------------------------------------------------------- multi-signal status


def sig(name: str, recent: list[bool], fresh: bool = True) -> Signal:
    return Signal(name, recent, fresh)


def test_no_external_checks_configured_is_unknown() -> None:
    assert classify_internet([]).status == "unknown"


def test_stale_signals_are_unknown_not_unreachable() -> None:
    status = classify_internet(
        [sig("a", [False, False, False], fresh=False), sig("b", [False] * 3, fresh=False)]
    )
    assert status.status == "unknown"


def test_all_signals_ok_is_reachable() -> None:
    status = classify_internet([sig("a", [True]), sig("b", [True, True])])
    assert status.status == "reachable" and status.signals_ok == 2


def test_one_failing_endpoint_among_several_is_degraded_never_unreachable() -> None:
    status = classify_internet(
        [sig("cloudflare", [True, True, True]), sig("quad9", [False, False, False, False])]
    )
    assert status.status == "degraded"
    assert "quad9" in status.reason


def test_single_failing_endpoint_alone_is_never_an_outage() -> None:
    status = classify_internet([sig("only", [False] * 10)])
    assert status.status == "degraded"
    assert "one signal cannot distinguish" in status.reason


def test_icmp_blocking_gateway_alone_is_not_an_outage() -> None:
    # Gateway probes are not external signals at all: with the external checks passing the
    # internet is reachable no matter how many gateway pings go unanswered.
    external = [sig("tcp-a", [True]), sig("dns", [True])]
    assert classify_internet(external).status == "reachable"


def test_all_signals_down_consecutively_is_unreachable() -> None:
    status = classify_internet([sig("a", [True, False, False, False]), sig("b", [False, False, False])])
    assert status.status == "unreachable" and status.signals_ok == 0


def test_all_signals_failing_but_not_yet_consecutively_is_degraded() -> None:
    status = classify_internet([sig("a", [True, False, False]), sig("b", [False, False, False])])
    assert status.status == "degraded"  # "a" has only 2 consecutive failures of the required 3


def test_configurable_thresholds() -> None:
    signals = [sig("a", [False, False]), sig("b", [False, False])]
    assert classify_internet(signals, consecutive_failures=2).status == "unreachable"
    assert classify_internet(signals, consecutive_failures=2, min_signals=3).status == "degraded"
