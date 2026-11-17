"""Counter-delta → rate maths. Every 'cannot know' case must yield no sample, never zero."""

from __future__ import annotations

import pytest

from observenet_agent.rates import WRAP_32, CounterTracker, Reading, counter_delta, derive_rate


def test_bits_per_second_is_exactly_8_times_delta_over_elapsed() -> None:
    rate = derive_rate(Reading(1_000, 10.0), Reading(126_000, 11.0), interval_s=1.0, scale=8.0)
    assert rate is not None
    assert rate.delta == 125_000
    assert rate.elapsed_s == 1.0
    assert rate.per_second == 8 * 125_000 / 1.0 == 1_000_000.0  # 125 kB/s == 1 Mbit/s


def test_rate_uses_actual_monotonic_elapsed_not_nominal_interval() -> None:
    # The sample arrived 1.25 s after the previous one: divide by 1.25, not by 1.
    rate = derive_rate(Reading(0, 100.0), Reading(1_000_000, 101.25), interval_s=1.0, scale=8.0)
    assert rate is not None
    assert rate.per_second == pytest.approx(8 * 1_000_000 / 1.25)
    assert rate.elapsed_s == pytest.approx(1.25)


def test_packets_per_second_uses_scale_one() -> None:
    rate = derive_rate(Reading(100, 0.0), Reading(164, 1.0), interval_s=1.0, scale=1.0)
    assert rate is not None and rate.per_second == 64.0


def test_idle_interface_reports_zero_not_none() -> None:
    rate = derive_rate(Reading(500, 1.0), Reading(500, 2.0), interval_s=1.0, scale=8.0)
    assert rate is not None and rate.per_second == 0.0


def test_first_reading_yields_no_sample() -> None:
    assert derive_rate(None, Reading(123, 1.0), interval_s=1.0) is None


@pytest.mark.parametrize("elapsed", [0.0, -0.5])
def test_zero_or_negative_elapsed_yields_no_sample(elapsed: float) -> None:
    assert derive_rate(Reading(0, 10.0), Reading(100, 10.0 + elapsed), interval_s=1.0) is None


def test_gap_longer_than_three_intervals_yields_no_sample() -> None:
    # 3.5 s between readings at a 1 s interval: samples were missed, so no 1-second claim.
    assert derive_rate(Reading(0, 0.0), Reading(10_000, 3.5), interval_s=1.0, scale=8.0) is None
    # exactly at the limit is still accepted
    assert derive_rate(Reading(0, 0.0), Reading(10_000, 3.0), interval_s=1.0, scale=8.0) is not None


def test_64_bit_counter_decrease_is_a_reset_and_yields_no_sample() -> None:
    assert counter_delta(5_000_000_000, 100, counter_bits=64) is None
    assert derive_rate(Reading(5_000_000_000, 1.0), Reading(100, 2.0), interval_s=1.0, scale=8.0) is None


def test_32_bit_counter_wrap_is_unwrapped_once() -> None:
    prev, cur = WRAP_32 - 1_000, 500
    assert counter_delta(prev, cur, counter_bits=32) == (1_500, True)
    rate = derive_rate(Reading(prev, 1.0), Reading(cur, 2.0), interval_s=1.0, counter_bits=32, scale=8.0)
    assert rate is not None
    assert rate.wrapped is True
    assert rate.per_second == 8 * 1_500


def test_increasing_counter_is_never_flagged_wrapped() -> None:
    assert counter_delta(10, 25, counter_bits=32) == (15, False)
    assert counter_delta(10, 25, counter_bits=64) == (15, False)


def test_implausible_rate_is_refused() -> None:
    rate = derive_rate(
        Reading(0, 0.0), Reading(10**12, 1.0), interval_s=1.0, scale=8.0, max_plausible_per_second=1e11
    )
    assert rate is None


def test_tracker_one_second_sampling_sequence() -> None:
    tracker = CounterTracker(interval_s=1.0)
    assert tracker.update("eth0|rx", 1_000, 100.0, scale=8.0) is None  # baseline
    r1 = tracker.update("eth0|rx", 2_000, 101.0, scale=8.0)
    r2 = tracker.update("eth0|rx", 4_500, 102.0, scale=8.0)
    assert r1 is not None and r1.per_second == 8_000.0
    assert r2 is not None and r2.per_second == 20_000.0


def test_tracker_rebaselines_after_reset_and_resumes_on_next_reading() -> None:
    tracker = CounterTracker(interval_s=1.0)
    tracker.update("eth0|rx", 9_000, 1.0)
    assert tracker.update("eth0|rx", 10, 2.0) is None  # reset: nothing emitted
    resumed = tracker.update("eth0|rx", 110, 3.0, scale=8.0)  # measured from the new baseline
    assert resumed is not None and resumed.per_second == 800.0


def test_tracker_rebaselines_after_gap() -> None:
    tracker = CounterTracker(interval_s=1.0)
    tracker.update("k", 0, 0.0)
    assert tracker.update("k", 5_000, 10.0) is None  # 10 s stall
    after = tracker.update("k", 6_000, 11.0)
    assert after is not None and after.per_second == 1_000.0


def test_tracker_keys_are_independent() -> None:
    tracker = CounterTracker(interval_s=1.0)
    tracker.update("eth0|rx", 0, 0.0)
    assert tracker.update("wlan0|rx", 999, 1.0) is None  # first reading for this key
    assert tracker.update("eth0|rx", 10, 1.0) is not None


def test_interface_that_disappears_and_reappears_starts_clean() -> None:
    tracker = CounterTracker(interval_s=1.0)
    tracker.update("veth1|rx", 50_000, 1.0)
    tracker.forget_missing({"eth0|rx"})  # veth1 went away
    # A re-created interface restarts its counters near zero; without forgetting, this
    # would look like a reset one reading later. With forgetting it is simply a baseline.
    assert tracker.update("veth1|rx", 10, 2.0) is None
    r = tracker.update("veth1|rx", 510, 3.0, scale=8.0)
    assert r is not None and r.per_second == 4_000.0
