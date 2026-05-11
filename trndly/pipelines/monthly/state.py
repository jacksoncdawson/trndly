"""Trend-state classifier: forward-first hybrid → (state, stat_string).

Maps a `(past3 lag + anchor + 6 forward)` trajectory to the four-way state
vocabulary the React frontend uses (`rising | peak | flat | falling`) and a
human-readable stat string.

Decision rule (in this order — first match wins):

    1. peak    : peak of the trajectory sits in the near-anchor band
                 {lag1, share_t, h1, h2} AND drops to y_h6 by at least
                 `PEAK_MIN_DROP` AND the forward forecast declines from anchor.
                 → "at peak"
    2. rising  : y_h6 > `RISING_RATIO` × share_t                           → "+{int}% next 6mo"
    3. falling : y_h6 < `FALLING_RATIO` × share_t                          → "−{int}% next 6mo"
    4. flat    : otherwise                                                 → "stable"

The previous iteration used the end-to-end ratio (lag3 → h6) for rising/falling,
which conflated past growth with forecast direction. A user looking at a card
reads the label as "what's about to happen" — so we now decide direction off
the **forward window only** (`share_t → y_h6`). Past data still drives peak
detection (peak needs evidence of having reached a high), but the rising/falling
verdict is forward-only.

Notes:
    * Stat percentages report the **forward** change (`y_h6 / share_t − 1`),
      matching the literal reading of "next 6mo".
    * `past_lags` is required to enable the `lag1` slot in the peak band, but
      callers without past history can pass `None` — peak then only fires when
      the high is at `share_t` or in the forward window.
    * The peak rule requires THREE conditions simultaneously (high in band,
      meaningful drop, forward decline) — kills both the "past-peak-already"
      misfire and the "modestly-declining-from-flat" misfire from previous
      iterations.

Edge cases:
    * Any non-finite value: classify as flat / stable.
    * Denominator == 0: classify as flat / stable.
"""

from __future__ import annotations

import math
from typing import Final, Sequence

# Forward-only direction thresholds (share_t → y_h6).
RISING_RATIO: Final[float] = 1.08
FALLING_RATIO: Final[float] = 0.92

# Peak: drop from in-band high to y_h6, relative to the high. Filters out
# noisy "high happens to be at anchor but only 2% above the floor" cases.
PEAK_MIN_DROP: Final[float] = 0.08

VALID_STATES: Final[tuple[str, ...]] = ("rising", "peak", "flat", "falling")


def _safe_pct(numer: float, denom: float) -> int | None:
    """Return integer percent change ((numer/denom - 1) * 100) or None on
    pathological inputs (zero/NaN denom, NaN numer)."""
    if denom is None or numer is None:
        return None
    if not math.isfinite(numer) or not math.isfinite(denom):
        return None
    if denom == 0.0:
        return None
    return int(round((numer / denom - 1.0) * 100))


def classify_state(
    y_h0: float,
    y_h1_to_h6: Sequence[float],
    past_lags: Sequence[float] | None = None,
) -> tuple[str, str]:
    """Classify a trajectory.

    Args:
        y_h0: anchor-month value (`share_t` in the cube).
        y_h1_to_h6: forecasts for horizons h=1..6 (six floats).
        past_lags: optional ``[share_lag3, share_lag2, share_lag1]`` —
            observed shares for the three months prior to the anchor.
            Only `share_lag1` is consumed by the rule (as the leftmost slot
            in the peak band). The other two are accepted for API stability
            with callers that pass the full triple.

    Returns:
        ``(state, stat_string)`` where state ∈ VALID_STATES.

    Examples:
        >>> classify_state(0.10, [0.11, 0.12, 0.13, 0.14, 0.15, 0.16])
        ('rising', '+60% next 6mo')
        >>> classify_state(0.10, [0.09, 0.08, 0.07, 0.06, 0.05, 0.04])
        ('falling', '−60% next 6mo')
        >>> classify_state(0.10, [0.10] * 6)
        ('flat', 'stable')
    """
    if len(y_h1_to_h6) != 6:
        raise ValueError(f"expected 6 horizons, got {len(y_h1_to_h6)}")
    if past_lags is not None and len(past_lags) != 3:
        raise ValueError(f"expected 3 past lags, got {len(past_lags)}")

    forward = list(y_h1_to_h6)
    if not all(math.isfinite(v) for v in [y_h0, *forward]):
        return "flat", "stable"
    if past_lags is not None and not all(math.isfinite(v) for v in past_lags):
        return "flat", "stable"

    y_h6 = forward[-1]
    fwd_pct = _safe_pct(y_h6, y_h0)
    if fwd_pct is None:
        return "flat", "stable"

    # ── Peak first ───────────────────────────────────────────────────────
    # Near-anchor band: {lag1, share_t, h1, h2}. We use lag1 only if past
    # lags were provided. Peak requires evidence of a real rise to the high
    # — three conditions all must hold:
    #   (a) the in-band max is at a slot OTHER than anchor, OR if it is at
    #       anchor, lag1 must be below anchor (climbed to anchor);
    #   (b) the drop from the high to y_h6 is at least PEAK_MIN_DROP;
    #   (c) the forward forecast declines from anchor (y_h6 < y_h0).
    # Without (a) we'd label monotonically-falling series as peak just
    # because the anchor happens to be the local max.
    band: list[float] = []
    if past_lags is not None:
        band.append(past_lags[2])
    band.extend([y_h0, forward[0], forward[1]])
    peak_value = max(band)
    # Real high requires the max in band to be strictly above anchor, OR
    # tied with anchor but with lag1 below anchor (climbed to anchor).
    # This filters out monotone-decline series where anchor is trivially
    # the local max.
    peak_above_anchor = peak_value > y_h0
    climbed_to_anchor = (
        peak_value == y_h0
        and past_lags is not None
        and past_lags[2] < y_h0
    )
    has_real_high = peak_above_anchor or climbed_to_anchor
    if (
        has_real_high
        and peak_value > 0
        and (peak_value - y_h6) / peak_value >= PEAK_MIN_DROP
        and y_h6 < y_h0
    ):
        return "peak", "at peak"

    # ── Forward-only direction ───────────────────────────────────────────
    if y_h6 > RISING_RATIO * y_h0:
        return "rising", f"+{fwd_pct}% next 6mo"
    if y_h6 < FALLING_RATIO * y_h0:
        # `fwd_pct` is negative; render with U+2212 to match React frontend
        # styling (visually balanced minus sign).
        return "falling", f"−{abs(fwd_pct)}% next 6mo"

    return "flat", "stable"
