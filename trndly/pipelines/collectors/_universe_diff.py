"""Diff live cube universe coverage before vs. after a re-scrape.

Loads:
  data/processed/live_<kind>_<MONTH>.pre_followup.parquet  (snapshot)
  data/processed/live_<kind>_<MONTH>.parquet              (current)

Reports per-dimension {n_before, n_after, added, removed} for the
fingerprint cube's 5 ID columns and the univariate cube's `dimension`
groups. Cross-checks new IDs against the documented allow-list in
``feature_lookups._DELIBERATELY_UNREACHABLE_LOOKUP_IDS``.

Usage (from repo root or trndly/):

    /opt/anaconda3/bin/python pipelines/collectors/_universe_diff.py [MONTH]

If MONTH is omitted, picks the most recent ``live_fingerprint_*.parquet``.
Exit code is 0 unless a previously-reachable ID disappeared without
being in the allow-list (signals a keyword-ordering regression).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve()
TRNDLY_ROOT = HERE.parents[2]
if str(TRNDLY_ROOT) not in sys.path:
    sys.path.insert(0, str(TRNDLY_ROOT))

from pipelines.collectors.feature_lookups import (  # noqa: E402
    _DELIBERATELY_UNREACHABLE_LOOKUP_IDS,
)

PROC = TRNDLY_ROOT / "data" / "processed"
LOOKUP_CSV = PROC / "lookup.csv"

FINGERPRINT_DIM_COLS = [
    "product_type_id", "gender_id", "color_master_id",
    "graphical_appearance_id", "material_id",
]


def _resolve_month(arg: str | None) -> str:
    if arg:
        return arg
    candidates = sorted(PROC.glob("live_fingerprint_*.parquet"))
    candidates = [c for c in candidates if ".pre_followup" not in c.name]
    if not candidates:
        sys.exit("No live_fingerprint_*.parquet found in data/processed/.")
    stem = candidates[-1].stem  # live_fingerprint_2026-05
    return stem.removeprefix("live_fingerprint_")


def _load_lookup_names() -> dict[tuple[str, int], str]:
    df = pd.read_csv(LOOKUP_CSV)
    return {(row.category, int(row.id)): row["name"] for _, row in df.iterrows()}


COL_TO_CAT = {
    "product_type_id": "product_type",
    "gender_id": "gender",
    "color_master_id": "color_master",
    "graphical_appearance_id": "graphical_appearance",
    "material_id": "material",
}


def _diff_set(label: str, before: set[int], after: set[int],
              cat: str, names: dict[tuple[str, int], str]) -> bool:
    """Print a diff line. Returns True if there was an unexpected loss."""
    added = after - before
    removed = before - after
    allowlist = _DELIBERATELY_UNREACHABLE_LOOKUP_IDS.get(cat, set())
    bad_loss = removed - allowlist - {0}  # Unknown sentinel always OK to lose

    print(f"  {label:24s} before={len(before):>3d} after={len(after):>3d}"
          f"  added={sorted(added) or '[]'}"
          f"  removed={sorted(removed) or '[]'}")
    if added:
        print(f"      + {[names.get((cat, i), '?') for i in sorted(added)]}")
    if removed:
        marker = "⚠" if bad_loss else " "
        print(f"      {marker} - {[names.get((cat, i), '?') for i in sorted(removed)]}")
        if bad_loss:
            print(f"        REGRESSION: {sorted(bad_loss)} were reachable, now aren't, "
                  f"and aren't in _DELIBERATELY_UNREACHABLE_LOOKUP_IDS.")
    return bool(bad_loss)


def run(month: str | None = None) -> int:
    month = _resolve_month(month)
    pre_fp = PROC / f"live_fingerprint_{month}.pre_followup.parquet"
    post_fp = PROC / f"live_fingerprint_{month}.parquet"
    pre_uv = PROC / f"live_univariate_{month}.pre_followup.parquet"
    post_uv = PROC / f"live_univariate_{month}.parquet"

    if not pre_fp.exists() or not post_fp.exists():
        sys.exit(f"Missing parquet(s):\n  pre  = {pre_fp.exists()}\n  post = {post_fp.exists()}")

    names = _load_lookup_names()

    print("=" * 72)
    print(f"FINGERPRINT cube — {month}")
    print("=" * 72)
    pre = pd.read_parquet(pre_fp)
    post = pd.read_parquet(post_fp)
    print(f"  rows: {len(pre):,} -> {len(post):,}")

    regressions: list[str] = []
    for col in FINGERPRINT_DIM_COLS:
        cat = COL_TO_CAT[col]
        before = set(pre[col].astype(int).unique())
        after = set(post[col].astype(int).unique())
        bad = _diff_set(col, before, after, cat, names)
        if bad:
            regressions.append(col)

    if pre_uv.exists() and post_uv.exists():
        print()
        print("=" * 72)
        print(f"UNIVARIATE cube — {month}")
        print("=" * 72)
        pre = pd.read_parquet(pre_uv)
        post = pd.read_parquet(post_uv)
        print(f"  rows: {len(pre):,} -> {len(post):,}")
        for cat in sorted(set(post["dimension"].astype(str)) | set(pre["dimension"].astype(str))):
            before = set(pre.loc[pre["dimension"].astype(str) == cat, "level_id"].astype(int))
            after = set(post.loc[post["dimension"].astype(str) == cat, "level_id"].astype(int))
            bad = _diff_set(f"dim={cat}", before, after, cat, names)
            if bad:
                regressions.append(cat)

    print()
    print("=" * 72)
    if regressions:
        print(f"⚠ Possible regressions in: {regressions}")
        return 1
    print("Diff clean — no IDs lost outside the allow-list.")
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(run(arg))
