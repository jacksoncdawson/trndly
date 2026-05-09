"""Unit tests for build_live_cube.collapse_unisex.

Covers the M+W → unisex SKU-dedup heuristic that runs between
``load_items`` and ``build_fingerprint_cube``. See plan file
``~/.claude/plans/take-a-look-at-virtual-elephant.md`` for context.
"""
from __future__ import annotations

import pandas as pd
import pytest

from pipelines.collectors.build_live_cube import (
    build_fingerprint_cube,
    collapse_unisex,
)


def _row(
    *,
    retailer: str = "gap",
    style_id: int = 1,
    cc_id: int = 1,
    gender: str = "women",
    gender_id: int = 1,
    color_master_id: int = 1,
    product_type_id: int = 1,
    graphical_appearance_id: int = 1,
    material_id: int = 1,
    scraped_at: str = "2026-05-07",
) -> dict:
    """Minimal items_<retailer>.csv-shape row with the columns
    ``collapse_unisex`` and ``build_fingerprint_cube`` actually read.
    """
    return {
        "retailer": retailer,
        "style_id": style_id,
        "cc_id": cc_id,
        "gender": gender,
        "gender_id": gender_id,
        "color_master_id": color_master_id,
        "product_type_id": product_type_id,
        "graphical_appearance_id": graphical_appearance_id,
        "material_id": material_id,
        "scraped_at": scraped_at,
        "month": pd.Timestamp("2026-05-01"),
    }


def test_collapse_unisex_no_pairs_passes_through(tmp_path):
    items = pd.DataFrame([
        _row(style_id=1, gender="women", gender_id=1),
        _row(style_id=2, gender="men", gender_id=3),
    ])
    out = collapse_unisex(items)
    assert len(out) == 2
    assert sorted(out["gender"].tolist()) == ["men", "women"]


def test_collapse_unisex_pure_pair_collapses_to_one_row():
    items = pd.DataFrame([
        _row(style_id=1, cc_id=1, gender="women", gender_id=1),
        _row(style_id=1, cc_id=1, gender="men", gender_id=3),
    ])
    out = collapse_unisex(items)
    assert len(out) == 1
    assert out["gender"].iloc[0] == "unisex"
    assert int(out["gender_id"].iloc[0]) == 2


def test_collapse_unisex_partial_overlap():
    items = pd.DataFrame([
        # SKU 1: in both → collapses
        _row(style_id=1, cc_id=1, gender="women", gender_id=1),
        _row(style_id=1, cc_id=1, gender="men", gender_id=3),
        # SKU 2: women-only → unchanged
        _row(style_id=2, cc_id=1, gender="women", gender_id=1),
        # SKU 3: men-only → unchanged
        _row(style_id=3, cc_id=1, gender="men", gender_id=3),
    ])
    out = collapse_unisex(items)
    # 4 rows → 3 (one M+W pair folded)
    assert len(out) == 3
    by_style = out.set_index("style_id")["gender"].to_dict()
    assert by_style[1] == "unisex"
    assert by_style[2] == "women"
    assert by_style[3] == "men"


def test_collapse_unisex_already_unisex_passthrough():
    """A row already tagged ``gender='unisex'`` (future-retailer case)
    is not touched, even when same SKU also appears with another gender.
    Note: the dedup rule ONLY fires when both 'women' AND 'men' are
    present — a pre-existing 'unisex' label doesn't trigger collapse.
    """
    items = pd.DataFrame([
        _row(style_id=1, cc_id=1, gender="unisex", gender_id=2),
        _row(style_id=2, cc_id=1, gender="women", gender_id=1),
    ])
    out = collapse_unisex(items)
    assert len(out) == 2  # unchanged
    assert "unisex" in out["gender"].tolist()


def test_collapse_unisex_cross_retailer_does_not_collide():
    """Same (style_id, cc_id) at different retailers is NOT a pair.
    The dedup key includes ``retailer`` so two retailers with
    overlapping SKU numbers stay distinct.
    """
    items = pd.DataFrame([
        _row(retailer="gap", style_id=1, cc_id=1, gender="women", gender_id=1),
        _row(retailer="ae",  style_id=1, cc_id=1, gender="men",   gender_id=3),
    ])
    out = collapse_unisex(items)
    assert len(out) == 2
    assert sorted(out["gender"].tolist()) == ["men", "women"]


def test_collapse_unisex_cube_round_trip_preserves_share_invariant():
    """End-to-end: collapse → build_fingerprint_cube. Result must have
    gender_id=2 populated, no gender_id=1/3 row for that SKU, and
    per-month share_articles still sums to 1.0 ± 1e-3.
    """
    items = pd.DataFrame([
        # Unisex SKU 1: collapses
        _row(style_id=1, cc_id=1, gender="women", gender_id=1, product_type_id=4),
        _row(style_id=1, cc_id=1, gender="men",   gender_id=3, product_type_id=4),
        # Women-only SKU
        _row(style_id=2, cc_id=1, gender="women", gender_id=1, product_type_id=4),
        # Men-only SKU
        _row(style_id=3, cc_id=1, gender="men",   gender_id=3, product_type_id=4),
    ])
    items = collapse_unisex(items)
    fp = build_fingerprint_cube(items)

    gender_ids = set(fp["gender_id"].astype(int).unique())
    assert 2 in gender_ids, f"gender_id=2 (Unisex) absent from cube; got {gender_ids}"

    by_month = fp.groupby("month", observed=True)["share_articles"].sum()
    assert ((by_month - 1.0).abs() < 1e-3).all(), by_month.to_dict()

    # Total articles after collapse: 4 rows → 3 (one M+W pair folded).
    assert int(fp["n_articles"].sum()) == 3
