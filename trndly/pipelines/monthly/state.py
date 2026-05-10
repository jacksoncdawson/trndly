"""Trend-state classifier: 6-horizon forecast → (state, stat_string).

Maps a per-fingerprint or per-(dimension, level) ``(y_h0, y_h1..h6)``
trajectory to the four-way state vocabulary the React frontend uses
(``rising | peak | flat | falling``) and a human-readable stat string.

Decision rule (in this order — first match wins):

    1. rising  : y_h6 / y_h0 > RISING_RATIO        → "+{int(...)}% next 6mo"
    2. falling : y_h6 / y_h0 < FALLING_RATIO       → "−{int(...)}% next 6mo"
    3. peak    : argmax(y_h0..h6) ≤ PEAK_HORIZON_INDEX
                 AND ratio is in [FALLING_RATIO, RISING_RATIO]  → "at peak"
    4. flat    : otherwise                         → "stable"

Order matters: a strongly-declining series whose max happens to be at h0
is **falling**, not peak. Peak fires only for trajectories that are at-or-near
their high but not collapsing afterward.

Edge cases:
    * y_h0 == 0 (or any value not finite): pct undefined; classify as flat / stable.
"""

from __future__ import annotations

import math
from typing import Final

# Thresholds — placeholder values, flag for tuning on first real run.
RISING_RATIO: Final[float] = 1.15
FALLING_RATIO: Final[float] = 0.85
PEAK_HORIZON_INDEX: Final[int] = 1  # peak if argmax over y_h0..h6 is at index 0 or 1

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


def classify_state(y_h0: float, y_h1_to_h6: list[float]) -> tuple[str, str]:
    """Classify a 6-horizon trajectory.

    Args:
        y_h0: forecast at the anchor (current) month. Conventionally this is
            ``share_t`` from the cube — the anchor month's catalog share.
        y_h1_to_h6: forecasts for horizons h=1..6 (six floats).

    Returns:
        ``(state, stat_string)`` where state ∈ VALID_STATES.

    Examples:
        >>> classify_state(0.10, [0.11, 0.12, 0.13, 0.14, 0.15, 0.16])
        ('rising', '+60% next 6mo')
        >>> classify_state(0.10, [0.10, 0.10, 0.10, 0.10, 0.10, 0.10])
        ('flat', 'stable')
        >>> classify_state(0.10, [0.09, 0.08, 0.07, 0.06, 0.05, 0.04])
        ('falling', '−60% next 6mo')
        >>> classify_state(0.20, [0.20, 0.21, 0.20, 0.19, 0.19, 0.18])
        ('peak', 'at peak')
    """
    if len(y_h1_to_h6) != 6:
        raise ValueError(f"expected 6 horizons, got {len(y_h1_to_h6)}")

    series = [y_h0, *y_h1_to_h6]
    # Defensive: any NaN → flat / stable. Avoids NaN-propagation in the rule.
    if not all(math.isfinite(v) for v in series):
        return "flat", "stable"

    pct = _safe_pct(y_h1_to_h6[-1], y_h0)
    if pct is None:
        return "flat", "stable"

    # Rising / falling fire first — they catch strong directional moves
    # regardless of where the argmax happens to land.
    if y_h1_to_h6[-1] > RISING_RATIO * y_h0:
        return "rising", f"+{pct}% next 6mo"
    if y_h1_to_h6[-1] < FALLING_RATIO * y_h0:
        # ``pct`` is negative; render with U+2212 to match React frontend
        # styling (visually balanced minus sign).
        return "falling", f"−{abs(pct)}% next 6mo"

    # Peak: argmax is at or right after the anchor AND there's a real
    # drop-off by horizon 6. Without the strict ``max > y_h_last`` check,
    # an all-flat series would classify as peak because its argmax happens
    # to be at index 0.
    argmax_idx = int(max(range(len(series)), key=lambda i: series[i]))
    if argmax_idx <= PEAK_HORIZON_INDEX and series[argmax_idx] > y_h1_to_h6[-1]:
        return "peak", "at peak"

    return "flat", "stable"
