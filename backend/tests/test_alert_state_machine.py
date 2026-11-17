"""Pure debounce / hysteresis / cooldown state machine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from observenet.services.alerts import KeyState, step

T0 = datetime(2026, 1, 1, tzinfo=UTC)
PARAMS = {"for_s": 60, "clear_for_s": 30, "cooldown_s": 300}


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def run(
    state: KeyState, seq: list[tuple[float, bool | None, bool | None]], *, active: bool = False
) -> list[tuple[float, str]]:
    """Feed (t, breached, cleared) evaluations; returns the actions and tracks 'active'."""
    actions = []
    for t, breached, cleared in seq:
        state, action = step(state, breached=breached, cleared=cleared, active=active, now=at(t), **PARAMS)
        if action == "fire":
            active = True
        elif action == "resolve":
            active = False
        if action:
            actions.append((t, action))
    return actions


def test_fires_only_after_breach_held_for_the_debounce_window() -> None:
    seq = [(t, True, False) for t in range(0, 70, 10)]
    assert run(KeyState(), seq) == [(60, "fire")]


def test_transient_one_tick_blip_never_fires() -> None:
    seq = [(0, False, True), (10, True, False), (20, False, True), (30, False, True), (100, False, True)]
    assert run(KeyState(), seq) == []


def test_breach_interrupted_before_debounce_restarts_the_clock() -> None:
    seq = [
        (0, True, False),
        (50, True, False),
        (55, False, False),
        (60, True, False),
        (110, True, False),
        (120, True, False),
    ]
    assert run(KeyState(), seq) == [(120, "fire")]  # 60 s after the *second* streak began


def test_hysteresis_between_thresholds_stays_firing() -> None:
    # breached=False, cleared=False means "below the fire threshold but above the clear threshold".
    seq = [(t, False, False) for t in range(0, 600, 10)]
    assert run(KeyState(), seq, active=True) == []


def test_resolves_only_after_sustained_clear() -> None:
    seq = [(0, False, True), (10, False, True), (20, False, True), (30, False, True)]
    assert run(KeyState(), seq, active=True) == [(30, "resolve")]


def test_clear_interrupted_by_hysteresis_band_restarts_clear_clock() -> None:
    seq = [(0, False, True), (20, False, False), (25, False, True), (50, False, True), (55, False, True)]
    assert run(KeyState(), seq, active=True) == [(55, "resolve")]


def test_cooldown_blocks_refire_then_allows_it() -> None:
    state = KeyState()
    seq: list[tuple[float, bool | None, bool | None]] = [
        (0, False, True),
        (30, False, True),
    ]  # resolve at t=30
    seq += [(t, True, False) for t in range(40, 400, 10)]  # immediately breaching again
    actions = run(state, seq, active=True)
    assert actions[0] == (30, "resolve")
    # debounce satisfied at t=100, but cooldown (300 s after t=30) holds it until t=330
    assert actions[1] == (330, "fire")
    assert len(actions) == 2


def test_unknown_data_holds_state_and_neither_fires_nor_clears() -> None:
    state = KeyState()
    state, action = step(state, breached=True, cleared=False, active=False, now=at(0), **PARAMS)
    pending = state.pending_since
    state, action = step(state, breached=None, cleared=None, active=False, now=at(500), **PARAMS)
    assert action is None and state.pending_since == pending  # no data ≠ recovered and ≠ breached
    # active alert with missing data stays active
    state2, action2 = step(KeyState(), breached=None, cleared=None, active=True, now=at(0), **PARAMS)
    assert action2 is None and state2.clear_since is None


def test_zero_debounce_fires_immediately() -> None:
    _state, action = step(
        KeyState(),
        breached=True,
        cleared=False,
        active=False,
        now=at(0),
        for_s=0,
        clear_for_s=0,
        cooldown_s=0,
    )
    assert action == "fire"


def test_key_state_json_round_trip() -> None:
    state = KeyState(pending_since=at(1), clear_since=None, last_resolved_at=at(2))
    again = KeyState.from_json(state.to_json())
    assert again == state
    assert KeyState.from_json(None) == KeyState()
    assert KeyState.from_json({"unknown_field": "x"}) == KeyState()
