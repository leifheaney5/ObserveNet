"""Counter-delta → rate conversion.

    bits_per_second = 8 × max(0, counter_delta) / elapsed_seconds

``elapsed_seconds`` comes from a monotonic clock, so wall-clock jumps (NTP, suspend, DST)
cannot distort rates. When a rate cannot be derived honestly — first sample, counter
reset, interface re-created, a gap longer than ``max_gap_factor`` intervals, or a
non-positive elapsed time — *no value* is produced. A missing point is a gap in the chart;
it is never filled with zero or an estimate.
"""

from __future__ import annotations

from dataclasses import dataclass

WRAP_32 = 2**32
WRAP_64 = 2**64


@dataclass(frozen=True)
class Reading:
    value: int
    monotonic: float


@dataclass(frozen=True)
class Rate:
    per_second: float
    elapsed_s: float
    delta: int
    wrapped: bool = False


def counter_delta(prev: int, cur: int, *, counter_bits: int = 64) -> tuple[int, bool] | None:
    """Delta between two cumulative counters.

    Returns ``(delta, wrapped)`` or ``None`` when the counter went backwards for a reason
    other than a plausible wrap (driver reload, interface re-creation, stats cleared).

    A 32-bit counter that decreases is assumed to have wrapped once. A 64-bit counter
    cannot realistically wrap (2^64 bytes ≈ 16 EiB), so a decrease means reset.
    """
    if cur >= prev:
        return cur - prev, False
    if counter_bits == 32 and prev < WRAP_32:
        return cur + WRAP_32 - prev, True
    return None


def derive_rate(
    prev: Reading | None,
    cur: Reading,
    *,
    interval_s: float,
    counter_bits: int = 64,
    scale: float = 1.0,
    max_gap_factor: float = 3.0,
    max_plausible_per_second: float | None = None,
) -> Rate | None:
    """Rate of change per second (``scale=8`` turns bytes into bits)."""
    if prev is None:
        return None
    elapsed = cur.monotonic - prev.monotonic
    if elapsed <= 0:
        return None
    if elapsed > interval_s * max_gap_factor:
        return None  # samples were missed: an average over the gap is not a 1-second sample
    d = counter_delta(prev.value, cur.value, counter_bits=counter_bits)
    if d is None:
        return None
    delta, wrapped = d
    per_second = scale * max(0, delta) / elapsed
    if max_plausible_per_second is not None and per_second > max_plausible_per_second:
        return None  # e.g. a mis-detected wrap; refuse to report an impossible rate
    return Rate(per_second=per_second, elapsed_s=elapsed, delta=delta, wrapped=wrapped)


class CounterTracker:
    """Remembers the previous reading per key and yields rates for consecutive readings."""

    def __init__(self, *, interval_s: float, counter_bits: int = 64, max_gap_factor: float = 3.0) -> None:
        self.interval_s = interval_s
        self.counter_bits = counter_bits
        self.max_gap_factor = max_gap_factor
        self._last: dict[str, Reading] = {}

    def update(self, key: str, value: int, monotonic: float, *, scale: float = 1.0) -> Rate | None:
        cur = Reading(value, monotonic)
        rate = derive_rate(self._last.get(key), cur, interval_s=self.interval_s, counter_bits=self.counter_bits,
                           scale=scale, max_gap_factor=self.max_gap_factor)  # fmt: skip
        self._last[key] = cur  # always re-baseline, including after a reset or gap
        return rate

    def forget_missing(self, live_keys: set[str]) -> None:
        """Drop state for interfaces that disappeared so a re-created one starts clean."""
        for key in [k for k in self._last if k not in live_keys]:
            del self._last[key]
