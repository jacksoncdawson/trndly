"""Re-resolve universe coverage smoke (no network).

Re-runs `extract_material` / `extract_product_type` on the saved
`items_<retailer>.csv` `title` / `web_product_type` columns using the
*current* `feature_lookups` module, and reports the per-dimension
universe diff vs. the on-disk `material_id` / `product_type_id` columns.

Useful for proving a feature_lookups dict expansion produces wider
coverage on real retailer text *without* re-scraping. Note: this is a
**lower bound** — for rows where the original scraper used PDP
composition text (not preserved in the CSV), title alone may
under-resolve material.

Run from repo root or `trndly/`:

    /opt/anaconda3/bin/python pipelines/collectors/_universe_smoke.py

Exit code 0 always — this is reporting, not gating.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve()
TRNDLY_ROOT = HERE.parents[2]  # .../trndly
if str(TRNDLY_ROOT) not in sys.path:
    sys.path.insert(0, str(TRNDLY_ROOT))

from pipelines.collectors.feature_lookups import (  # noqa: E402
    MATERIAL_TO_ID,
    PRODUCT_TYPE_TO_ID,
    extract_category,
    extract_material,
    extract_product_type,
)
from pipelines.paths import LOOKUP_CSV, RAW_ITEMS_DIR  # noqa: E402

ITEMS_DIR = RAW_ITEMS_DIR


def _load_lookup_names() -> dict[tuple[str, int], str]:
    df = pd.read_csv(LOOKUP_CSV)
    return {(row.category, int(row.id)): row["name"] for _, row in df.iterrows()}


def _resolve_row(row: pd.Series) -> tuple[int, int]:
    title = str(row.get("title") or "")
    web_pt = str(row.get("web_product_type") or "")
    category = extract_category(title)
    material = extract_material(title, inferred_category=category)
    product_type = extract_product_type(title) or extract_product_type(web_pt)
    material_id = MATERIAL_TO_ID.get(material or "", 0)
    product_type_id = PRODUCT_TYPE_TO_ID.get(product_type or "", 0)
    return material_id, product_type_id


def run() -> None:
    names = _load_lookup_names()

    items_files = sorted(ITEMS_DIR.glob("items_*.csv"))
    if not items_files:
        print(f"No items_*.csv in {ITEMS_DIR}")
        sys.exit(0)

    all_old_mat: set[int] = set()
    all_new_mat: set[int] = set()
    all_old_pt: set[int] = set()
    all_new_pt: set[int] = set()

    for f in items_files:
        df = pd.read_csv(f)
        old_mat = set(df["material_id"].astype(int))
        old_pt = set(df["product_type_id"].astype(int))
        resolved = df.apply(_resolve_row, axis=1, result_type="expand")
        resolved.columns = ["material_id", "product_type_id"]
        new_mat = set(resolved["material_id"])
        new_pt = set(resolved["product_type_id"])

        all_old_mat |= old_mat
        all_new_mat |= new_mat
        all_old_pt |= old_pt
        all_new_pt |= new_pt

        print(f"\n=== {f.name} ({len(df):,} rows) ===")
        for label, old, new, cat in [
            ("material",     old_mat, new_mat, "material"),
            ("product_type", old_pt,  new_pt,  "product_type"),
        ]:
            added = new - old
            removed = old - new
            print(f"  {label:12s}  before={len(old):>3d}  after={len(new):>3d}  "
                  f"added={sorted(added) or '[]'}  removed={sorted(removed) or '[]'}")
            if added:
                print(f"    + {[names.get((cat, i), '?') for i in sorted(added)]}")
            if removed:
                print(f"    - {[names.get((cat, i), '?') for i in sorted(removed)]}")

    print("\n" + "=" * 72)
    print("AGGREGATE (union over all retailers)")
    print("=" * 72)
    for label, old, new, cat in [
        ("material",     all_old_mat, all_new_mat, "material"),
        ("product_type", all_old_pt,  all_new_pt,  "product_type"),
    ]:
        added = new - old
        removed = old - new
        print(f"\n  {label}:")
        print(f"    before: n={len(old)}  ids={sorted(old)}")
        print(f"    after : n={len(new)}  ids={sorted(new)}")
        print(f"    added : {sorted(added)} -> {[names.get((cat, i), '?') for i in sorted(added)]}")
        if removed:
            print(f"    REMOVED: {sorted(removed)} -> {[names.get((cat, i), '?') for i in sorted(removed)]}")
            print(f"    ⚠ A previously-reachable ID is now unreachable. Likely a keyword-ordering regression.")


if __name__ == "__main__":
    run()
