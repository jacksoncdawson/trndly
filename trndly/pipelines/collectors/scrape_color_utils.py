"""Small helpers shared by retailer scrapers."""

from __future__ import annotations


def dedupe_swatch_labels_preserve_order(labels: list[str] | None) -> list[str]:
    """
    Drop duplicate swatch / color labels for the same product or tile,
    preserving first-seen order. Comparison is case-insensitive so
    e.g. "Black" and "BLACK" collapse to one row.
    """
    if not labels:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for lab in labels:
        s = (lab or "").strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out
