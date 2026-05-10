"""Unit tests for ``pipelines.monthly.state.classify_state``."""
from __future__ import annotations

import math

import pytest

from pipelines.monthly.state import (
    FALLING_RATIO,
    PEAK_HORIZON_INDEX,
    RISING_RATIO,
    VALID_STATES,
    classify_state,
)


@pytest.mark.parametrize(
    "y_h0, y_h1_to_h6, expected_state",
    [
        # Monotonic rising, ratio > 1.15 → rising
        (0.10, [0.11, 0.12, 0.13, 0.14, 0.15, 0.16], "rising"),
        # Strong rise → rising
        (0.05, [0.06, 0.07, 0.08, 0.09, 0.10, 0.12], "rising"),
        # Monotonic falling, ratio < 0.85 → falling
        (0.10, [0.09, 0.08, 0.07, 0.06, 0.05, 0.04], "falling"),
        # Strong fall (max happens to be at h0, but ratio is below FALLING_RATIO
        # so falling fires before peak) → falling
        (0.20, [0.18, 0.15, 0.12, 0.10, 0.08, 0.06], "falling"),
        # Same shape, max at h1 — still falling because ratio is harsh
        (0.10, [0.20, 0.18, 0.15, 0.12, 0.10, 0.08], "falling"),
        # Truly flat → flat
        (0.10, [0.10, 0.10, 0.10, 0.10, 0.10, 0.10], "flat"),
        # Mild noise within ±15% → flat
        (0.10, [0.105, 0.103, 0.107, 0.104, 0.106, 0.108], "flat"),
        # Peak proper: max at h0, gentle decline within FALLING/RISING band → peak
        (0.20, [0.20, 0.19, 0.19, 0.18, 0.18, 0.18], "peak"),
        # Peak with argmax at h1 (within PEAK_HORIZON_INDEX) and gentle slope → peak
        (0.20, [0.21, 0.20, 0.20, 0.19, 0.19, 0.18], "peak"),
    ],
)
def test_classify_state_basic(y_h0, y_h1_to_h6, expected_state):
    state, _stat = classify_state(y_h0, y_h1_to_h6)
    assert state == expected_state, f"got {state!r} for {y_h0}, {y_h1_to_h6}"


def test_classify_state_all_states_in_valid_set():
    """Every output state lives in VALID_STATES — guards against typos."""
    cases = [
        (0.10, [0.11, 0.12, 0.13, 0.14, 0.15, 0.16]),
        (0.10, [0.09, 0.08, 0.07, 0.06, 0.05, 0.04]),
        (0.10, [0.10] * 6),
        (0.20, [0.18, 0.15, 0.12, 0.10, 0.08, 0.06]),
    ]
    for y_h0, y_h1_to_h6 in cases:
        state, _ = classify_state(y_h0, y_h1_to_h6)
        assert state in VALID_STATES


def test_classify_state_stat_format_rising():
    _, stat = classify_state(0.10, [0.11, 0.12, 0.13, 0.14, 0.15, 0.16])
    # +60% next 6mo (0.16/0.10 = 1.6 → +60%)
    assert stat == "+60% next 6mo"


def test_classify_state_stat_format_falling():
    _, stat = classify_state(0.10, [0.09, 0.08, 0.07, 0.06, 0.05, 0.04])
    # 0.04/0.10 = 0.4 → -60% (rendered with U+2212)
    assert stat == "−60% next 6mo"


def test_classify_state_stat_format_flat():
    _, stat = classify_state(0.10, [0.10] * 6)
    assert stat == "stable"


def test_classify_state_stat_format_peak():
    _, stat = classify_state(0.20, [0.20, 0.19, 0.19, 0.18, 0.18, 0.18])
    assert stat == "at peak"


def test_classify_state_zero_anchor_is_flat():
    """Zero anchor → pct undefined → flat (defensive)."""
    state, stat = classify_state(0.0, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert state == "flat"
    assert stat == "stable"


def test_classify_state_nan_anywhere_is_flat():
    """NaN in any horizon → defensive flat / stable."""
    state, stat = classify_state(0.10, [float("nan"), 0.12, 0.13, 0.14, 0.15, 0.16])
    assert state == "flat"
    assert stat == "stable"

    state, stat = classify_state(float("nan"), [0.11, 0.12, 0.13, 0.14, 0.15, 0.16])
    assert state == "flat"
    assert stat == "stable"


def test_classify_state_wrong_horizon_count_raises():
    with pytest.raises(ValueError, match="6 horizons"):
        classify_state(0.10, [0.10, 0.11, 0.12])


def test_classify_state_thresholds_are_strict():
    """Boundary checks around the rising/falling ratio thresholds.

    Note: when y_h0 ties or exceeds early horizons, the peak rule may also
    apply once we're inside the rising/falling band. These cases use a
    series whose max sits in the middle horizons so the peak rule does not
    pre-empt the boundary check.
    """
    # Slightly above rising threshold (1.15) — should classify rising
    state, _ = classify_state(0.10, [0.10, 0.10, 0.10, 0.10, 0.10, 0.116])
    assert state == "rising"

    # Slightly below rising threshold (1.15) — flat (max is at h6 → not peak)
    state, _ = classify_state(0.10, [0.10, 0.10, 0.10, 0.10, 0.10, 0.114])
    assert state == "flat"

    # Slightly below falling threshold (0.85) — falling
    state, _ = classify_state(0.10, [0.10, 0.10, 0.10, 0.10, 0.10, 0.084])
    assert state == "falling"

    # Slightly above falling threshold (0.85), max in middle (not h0/h1) → flat
    state, _ = classify_state(0.10, [0.10, 0.10, 0.105, 0.10, 0.099, 0.086])
    assert state == "flat"


def test_classify_state_ratios_are_module_constants():
    # Sanity — these are flagged for tuning; if they change, tests above need updating
    assert RISING_RATIO == 1.15
    assert FALLING_RATIO == 0.85
    assert PEAK_HORIZON_INDEX == 1
