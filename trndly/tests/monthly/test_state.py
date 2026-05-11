"""Unit tests for ``pipelines.monthly.state.classify_state``.

Two execution modes:
  * legacy 7-point: ``y_h0 + y_h1..h6`` (no ``past_lags``). Peak's near-anchor
    band collapses to {anchor, h1, h2}.
  * full 10-point: ``past_lags=[lag3, lag2, lag1]``. Peak's band extends to
    {lag1, anchor, h1, h2} so it can catch "high was a month ago" cases.

predict.py always uses the 10-point path. The rule decides direction
(rising/falling/flat) from the forward window only (`share_t → y_h6`);
peak alone considers past + forward.
"""
from __future__ import annotations

import math

import pytest

from pipelines.monthly.state import (
    FALLING_RATIO,
    PEAK_MIN_DROP,
    RISING_RATIO,
    VALID_STATES,
    classify_state,
)


# --------------------------------------------------------------------------- #
# Legacy 7-point path (no past lags) — peak band = {anchor, h1, h2}            #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "y_h0, y_h1_to_h6, expected_state",
    [
        # Monotonic rising → rising (forward ratio > 1.08)
        (0.10, [0.11, 0.12, 0.13, 0.14, 0.15, 0.16], "rising"),
        (0.05, [0.06, 0.07, 0.08, 0.09, 0.10, 0.12], "rising"),

        # Anchor is max in band BUT no past evidence of climb → falling, not peak
        # (without past_lags we can't distinguish anchor-as-peak from
        # monotonic decline from anchor; default to forward direction)
        (0.10, [0.09, 0.08, 0.07, 0.06, 0.05, 0.04], "falling"),
        (0.20, [0.18, 0.15, 0.12, 0.10, 0.08, 0.06], "falling"),

        # h1 spikes then collapse → peak (h1 is in band, drops > 8%)
        (0.10, [0.20, 0.18, 0.15, 0.12, 0.10, 0.08], "peak"),

        # Forward goes up monotonically (max at h6 outside band) → rising
        (0.10, [0.10, 0.11, 0.12, 0.14, 0.16, 0.20], "rising"),

        # Truly flat → flat
        (0.10, [0.10, 0.10, 0.10, 0.10, 0.10, 0.10], "flat"),
        # Mild noise within ±8% → flat
        (0.10, [0.101, 0.103, 0.103, 0.104, 0.103, 0.105], "flat"),

        # Max at anchor with no past evidence → falling (not peak)
        (0.20, [0.20, 0.19, 0.19, 0.18, 0.18, 0.18], "falling"),
        # Peak: max at h1, drop > 8% → peak (h1 is non-anchor slot in band)
        (0.20, [0.21, 0.20, 0.20, 0.19, 0.19, 0.18], "peak"),
        # h2 is non-anchor max in band, drop > 8% → peak
        (0.10, [0.105, 0.108, 0.110, 0.090, 0.075, 0.070], "peak"),
        # Forward gentle drift down → flat (not peak; drop from anchor < 8%)
        (0.10, [0.099, 0.098, 0.097, 0.096, 0.095, 0.094], "flat"),
    ],
)
def test_classify_state_basic_no_lags(y_h0, y_h1_to_h6, expected_state):
    state, _stat = classify_state(y_h0, y_h1_to_h6)
    assert state == expected_state, f"got {state!r} for y_h0={y_h0}, fwd={y_h1_to_h6}"


def test_classify_state_all_states_in_valid_set():
    cases = [
        (0.10, [0.11, 0.12, 0.13, 0.14, 0.15, 0.16]),
        (0.10, [0.09, 0.08, 0.07, 0.06, 0.05, 0.04]),
        (0.10, [0.10] * 6),
        (0.20, [0.18, 0.15, 0.12, 0.10, 0.08, 0.06]),
    ]
    for y_h0, fwd in cases:
        state, _ = classify_state(y_h0, fwd)
        assert state in VALID_STATES


def test_classify_state_stat_format_rising():
    # Forward pct = (0.16/0.10 − 1)*100 = 60
    _, stat = classify_state(0.10, [0.11, 0.12, 0.13, 0.14, 0.15, 0.16])
    assert stat == "+60% next 6mo"


def test_classify_state_stat_format_falling():
    # Choose argmax OUTSIDE the peak band (h3..h6) so falling fires, not peak.
    # Anchor 0.10 → h6 0.04: forward pct = -60%.
    _, stat = classify_state(0.10, [0.099, 0.098, 0.097, 0.080, 0.060, 0.040])
    assert stat == "−60% next 6mo"


def test_classify_state_stat_format_flat():
    _, stat = classify_state(0.10, [0.10] * 6)
    assert stat == "stable"


def test_classify_state_stat_format_peak():
    # h1 is the in-band max (non-anchor slot), drop > 8% → peak
    _, stat = classify_state(0.20, [0.22, 0.20, 0.19, 0.19, 0.19, 0.18])
    assert stat == "at peak"


def test_classify_state_zero_anchor_is_flat():
    state, stat = classify_state(0.0, [0.0] * 6)
    assert state == "flat"
    assert stat == "stable"


def test_classify_state_nan_anywhere_is_flat():
    state, stat = classify_state(0.10, [float("nan"), 0.12, 0.13, 0.14, 0.15, 0.16])
    assert (state, stat) == ("flat", "stable")
    state, stat = classify_state(float("nan"), [0.11, 0.12, 0.13, 0.14, 0.15, 0.16])
    assert (state, stat) == ("flat", "stable")


def test_classify_state_wrong_horizon_count_raises():
    with pytest.raises(ValueError, match="6 horizons"):
        classify_state(0.10, [0.10, 0.11, 0.12])


def test_classify_state_thresholds_boundaries():
    """Boundary checks on the forward ratio thresholds (peak chosen to NOT fire).

    Series where argmax is outside the band ({anchor, h1, h2}) and forward
    drop is < PEAK_MIN_DROP so we can probe the rising/falling band cleanly.
    """
    # Just above 1.08 forward → rising. Use a series with argmax at h6.
    state, _ = classify_state(0.10, [0.100, 0.101, 0.102, 0.103, 0.105, 0.109])
    assert state == "rising"
    # Just below 1.08 → flat
    state, _ = classify_state(0.10, [0.100, 0.101, 0.102, 0.103, 0.104, 0.107])
    assert state == "flat"

    # Slow drift down → flat (forward 0.094 / 0.10 = 0.94, just above 0.92).
    state, _ = classify_state(0.10, [0.099, 0.098, 0.097, 0.096, 0.095, 0.094])
    assert state == "flat"
    # Slightly steeper drift → still flat (peak doesn't fire; below 8% drop).
    state, _ = classify_state(0.10, [0.099, 0.097, 0.095, 0.094, 0.094, 0.0915])
    assert state == "falling"  # 0.0915 < 0.92*0.10


def test_classify_state_ratios_are_module_constants():
    assert RISING_RATIO == pytest.approx(1.08)
    assert FALLING_RATIO == pytest.approx(0.92)
    assert PEAK_MIN_DROP == pytest.approx(0.08)


# --------------------------------------------------------------------------- #
# Full 10-point path (with past_lags) — peak band includes lag1                #
# --------------------------------------------------------------------------- #

class TestClassifyWithLags:
    def test_rising_when_forward_is_up(self):
        state, _ = classify_state(
            0.10, [0.105, 0.108, 0.110, 0.112, 0.115, 0.120],
            past_lags=[0.099, 0.100, 0.101],
        )
        assert state == "rising"

    def test_falling_when_forward_is_down(self):
        # Past has plateaued AT the level of anchor (no climb into anchor),
        # forward drops 30%. lag1 ≥ anchor so the "climbed to anchor" check
        # fails → peak does not fire → falling on forward slope.
        state, _ = classify_state(
            0.10, [0.099, 0.097, 0.095, 0.085, 0.075, 0.070],
            past_lags=[0.10, 0.10, 0.10],
        )
        assert state == "falling"

    def test_peak_when_high_was_lag1(self):
        """Past data shows the high was last month, forward declines from there."""
        state, _ = classify_state(
            0.115, [0.110, 0.107, 0.103, 0.099, 0.095, 0.092],
            past_lags=[0.108, 0.114, 0.120],
        )
        # lag1 (0.120) is the in-band max. Drop to h6 (0.092) = 23% > 8%.
        # And y_h6 (0.092) < share_t (0.115). Peak fires.
        assert state == "peak"

    def test_no_peak_when_high_was_lag3(self):
        """Lag3 is outside the near-anchor band; treat as forward-decline."""
        state, _ = classify_state(
            0.051, [0.049, 0.048, 0.047, 0.048, 0.048, 0.050],
            past_lags=[0.055, 0.054, 0.052],
        )
        # In-band: {lag1=0.052, anchor=0.051, h1=0.049, h2=0.048}. Max = lag1 = 0.052.
        # Drop to h6 (0.050) = 3.8% — under threshold. Plus y_h6 (0.050) < share_t.
        # 3.8% < 8% so peak does not fire. Forward: 0.050/0.051 = 0.98 → flat.
        assert state == "flat"

    def test_no_peak_when_forward_keeps_climbing(self):
        state, _ = classify_state(
            0.10, [0.11, 0.12, 0.13, 0.14, 0.15, 0.16],
            past_lags=[0.09, 0.10, 0.10],
        )
        # Forward up 60% → rising.
        assert state == "rising"

    # ── Production-cube regression anchors ─────────────────────────────────
    # Drawn directly from the 2026-05 predictions cube. These are the cards
    # the user flagged (or related ones) — they pin down the rule against
    # real data and prevent silent regressions.

    def test_trousers_real_case(self):
        """Trousers: past climbs to lag1, drops at anchor, forward flat.
        Previous rule labelled peak; user expects flat or falling."""
        state, _ = classify_state(
            0.2397,
            [0.2407, 0.2423, 0.2444, 0.2448, 0.2436, 0.2423],
            past_lags=[0.2430, 0.2503, 0.2523],
        )
        # In-band max: lag1=0.2523. Drop to h6 (0.2423) = 4% — below threshold.
        # And forward 0.2423/0.2397 = 1.011 → flat.
        assert state == "flat"

    def test_dress_real_case(self):
        """Dress: past climbs steeply, forward flat. Previous rule said
        +23% rising (end-to-end); user expects stable."""
        state, _ = classify_state(
            0.0276,
            [0.0285, 0.0289, 0.0287, 0.0283, 0.0274, 0.0270],
            past_lags=[0.0220, 0.0240, 0.0282],
        )
        # In-band max: h2=0.0289. Drop to h6 (0.0270) = 6.6% — below 8%.
        # Forward 0.0270/0.0276 = 0.978 → flat.
        assert state == "flat"

    def test_tshirt_real_case(self):
        """T-shirt: anchor is the high, forward drops 20%. Peak."""
        state, _ = classify_state(
            0.1572,
            [0.1510, 0.1455, 0.1369, 0.1287, 0.1245, 0.1244],
            past_lags=[0.1425, 0.1459, 0.1514],
        )
        assert state == "peak"

    def test_sweater_real_case(self):
        """Sweater: past falls into anchor, forward rises. The previous
        end-to-end rule called this falling — misleading for action."""
        state, _ = classify_state(
            0.0272,
            [0.0259, 0.0263, 0.0275, 0.0291, 0.0314, 0.0342],
            past_lags=[0.0420, 0.0374, 0.0299],
        )
        # Forward 0.0342/0.0272 = 1.257 → rising.
        assert state == "rising"

    def test_hoodie_real_case(self):
        """Hoodie: past dropped sharply, forward flat. End-to-end said
        falling (-27%); forward-honest says flat."""
        state, _ = classify_state(
            0.0584,
            [0.0601, 0.0599, 0.0597, 0.0595, 0.0598, 0.0601],
            past_lags=[0.0825, 0.0712, 0.0640],
        )
        # In-band max: h1=0.0601. Drop to h6 (0.0601) = 0%. Not peak.
        # Forward 0.0601/0.0584 = 1.029 → flat.
        assert state == "flat"

    def test_vest_top_real_case(self):
        """Vest top: forecast peaks at h2 then declines — peak, not rising."""
        state, _ = classify_state(
            0.0394,
            [0.0408, 0.0420, 0.0408, 0.0388, 0.0365, 0.0348],
            past_lags=[0.0310, 0.0340, 0.0370],
        )
        # In-band max: h2=0.0420. Drop to h6 (0.0348) = 17% > 8%.
        # And y_h6 (0.0348) < share_t (0.0394). Peak fires.
        assert state == "peak"

    def test_wrong_lag_count_raises(self):
        with pytest.raises(ValueError, match="3 past lags"):
            classify_state(0.10, [0.10] * 6, past_lags=[0.10, 0.10])

    def test_nan_in_lags_is_flat(self):
        state, _ = classify_state(
            0.10, [0.10] * 6, past_lags=[float("nan"), 0.10, 0.10],
        )
        assert state == "flat"
