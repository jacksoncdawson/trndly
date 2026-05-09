"""Cross-cutting unit tests for `feature_lookups.extract_*`.

Pins the keyword-priority resolution order so future keyword additions
don't silently flip established mappings. Each parametric case is a
known-good pairing surfaced by the cross-retailer leakage audits.
"""
from __future__ import annotations

import pytest

from pipelines.collectors import feature_lookups as fl


# --------------------------------------------------------------------------- #
# extract_color                                                                 #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,expected", [
    # Brand-specific compound names beat the generic singletons.
    ("Tapestry navy blue", "navy"),
    ("New Classic Navy", "navy"),
    ("Vintage Navy", "navy"),  # specific compound; not the bare "vintage" → blue
    # Shade-qualified compounds
    ("Light blue", "blue"),
    ("Medium blue", "blue"),
    ("Dark blue", "blue"),
    # Single-word standard generics
    ("Black", "black"),
    ("White", "white"),
    ("Khaki", "beige"),
    # AE marketing names land on canonical buckets
    ("Bordeaux", "red"),
    ("Mint", "green"),
    ("Heather Frost", "gray"),
    ("Coffee", "brown"),
    ("Chalk", "white"),
    ("Skylight", "blue"),
    # AE denim wash variants → blue
    ("Medium Vintage", "blue"),
    ("Dark Vintage Wash", "blue"),
    ("Tinted Medium", "blue"),
    ("Faded Light", "blue"),
])
def test_extract_color_keyword_priority(text, expected):
    assert fl.extract_color(text) == expected


def test_extract_color_returns_none_when_no_match():
    assert fl.extract_color("xxxx089") is None
    assert fl.extract_color("Multi") is None  # genuinely multi-color, no canonical bucket


# --------------------------------------------------------------------------- #
# extract_product_type                                                          #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,expected", [
    # Canonical apparel
    ("Vintage Classic T-Shirt", "T-shirt"),
    ("Slim-Fit Chino Pants", "Trousers"),
    ("Soft Cotton Hoodie", "Hoodie"),
    ("Performance Jogger", "Trousers"),
    ("Stretch Denim Jeans", "Trousers"),
    # Coverage extensions surfaced in this round
    ("UV Protection Compact Umbrella", "Umbrella"),
    ("Bucket Hat Wide Brim", "Bucket hat"),
    ("UV Protection Twill Cap", "Cap"),
    # Underwear families
    ("AIRism Seamless Hiphuggers", "Underwear bottom"),
    ("Wireless Bra | 3D Hold", "Bra"),
    ("High Rise Briefs", "Underwear bottom"),
    # Outerwear
    ("Cotton Blend Short Blouson", "Jacket"),
    # Tops
    ("Long-Sleeve Square-Neck Babydoll", "Top"),
    # Accessories
    ("Multi Pocket Backpack", "Bag"),
    ("Footsies | 3 Pairs", "Socks"),
    # The "capri" → Trousers fix preventing "cap" → Cap false positive
    ("Double Knit Easy Capris", "Trousers"),
    # Graphic jersey collapses to T-shirt as last-resort
    ("University of Michigan Graphic Football Jersey", "T-shirt"),
    # Sweater family
    ("Featherweight Turtleneck", "Sweater"),
    # Universe-expansion (2026-05): hat / footwear / sleepwear specifics
    # win against generic "hat" / "boot" / loungewear keywords.
    ("Wool Felt Hat", "Felt hat"),
    ("Classic Fedora", "Felt hat"),
    ("Straw Hat with Ribbon", "Straw hat"),
    ("Panama Hat", "Straw hat"),
    ("Wide-Brim Hat", "Hat/brim"),
    ("Floppy Hat", "Hat/brim"),
    ("Sun Hat", "Hat/brim"),
    ("Suede Ankle Booties", "Bootie"),
    ("Block-Heel Bootie", "Bootie"),
    ("Velvet Headband", "Headband"),
    ("Knot Hairband", "Headband"),
    ("Silk Necktie", "Tie"),
    ("Lace-Trim Nightgown", "Night gown"),
    ("Cotton Night Gown", "Night gown"),
])
def test_extract_product_type_keyword_priority(text, expected):
    assert fl.extract_product_type(text) == expected


def test_extract_product_type_returns_none_when_no_match():
    assert fl.extract_product_type("Pistachio Creme Perfume") is None
    assert fl.extract_product_type("Mersea Coconut Sugar Hand Soap") is None


# --------------------------------------------------------------------------- #
# extract_material                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,inferred_category,expected", [
    # Percentage-aware: 98% Cotton wins over 2% Elastane (would be polyester).
    ("98% Cotton, 2% Elastane", None, "cotton"),
    # The historical mis-bucketing case that the percentage path fixes.
    ("Polyester 23%, Cotton 77%", None, "cotton"),
    # Multi-component with parens
    ("100% Cotton (25% Recycled Cotton Fiber)", None, "cotton"),
    # Direct keyword
    ("Linen-Blend Top", None, "linen"),
    # Universe-expansion (2026-05): each fabric resolves to its own bucket
    # rather than collapsing into wool/silk/knit/cotton/polyester.
    ("Cashmere Cardigan", None, "cashmere"),
    ("Silk Chiffon Blouse", None, "chiffon"),     # chiffon beats silk in keyword order
    ("Crepe Midi Dress", None, "crepe"),
    ("Satin Slip Dress", None, "satin"),
    ("Cotton Jersey Tee", None, "jersey"),         # jersey beats cotton order-wise
    ("Velvet Bodysuit", None, "velvet"),
    ("Nylon Windbreaker", None, "nylon"),
    ("Fleece Pullover", None, "fleece"),
    ("Suede Ankle Boots", None, "suede"),
    ("Modal-Blend Tee", None, "modal"),
    ("Corduroy Pants", None, "corduroy"),
    ("Lace Trim Camisole", None, "lace"),
    ("Faux Fur Coat", None, "faux fur"),
    ("Sherpa-Lined Jacket", None, "fleece"),       # sherpa routes to fleece
    # Imitation leather still routes to leather (HM-cat artifact unreachable)
    ("Imitation Leather Jacket", None, "leather"),
    # Pants need an explicit denim hint to fall to denim
    ("5-Pocket Selvedge Pants", "pants", "denim"),
    # Pants without a hint return None (not a category default)
    ("Modern Trousers", "pants", None),
    # Category default fires for non-pants categories without a fabric word
    ("Floral Dress", "dress", "cotton"),
])
def test_extract_material_resolution_order(text, inferred_category, expected):
    assert fl.extract_material(text, inferred_category=inferred_category) == expected


@pytest.mark.parametrize("bucket,expected_id", [
    # Spot-check the expanded MATERIAL_TO_ID covers retailer-realistic fabrics.
    ("cashmere", 27),
    ("chiffon", 16),
    ("jersey", 2),
    ("velvet", 30),
    ("satin", 14),
    ("nylon", 28),
    ("fleece", 22),
    ("suede", 29),
    ("modal", 23),
    ("corduroy", 25),
    ("lace", 4),
    ("tencel", 34),
    ("viscose", 5),
])
def test_material_bucket_to_id_coverage(bucket, expected_id):
    assert fl.MATERIAL_TO_ID[bucket] == expected_id


# --------------------------------------------------------------------------- #
# extract_graphical_appearance                                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,expected", [
    ("Floral Print Top", "All over pattern"),
    ("Polka Dot Dress", "Dot"),
    ("Striped Tee", "Stripe"),
    ("Plaid Shirt", "Check"),
    ("Heather Gray Sweater", "Melange"),
    ("Sequin Skirt", "Sequin"),
    # No keyword → defaults to Solid (the structural fallback)
    ("Plain Cotton Tee", "Solid"),
])
def test_extract_graphical_appearance(text, expected):
    assert fl.extract_graphical_appearance(text) == expected


# --------------------------------------------------------------------------- #
# extract_color_spectrum_id + extract_product_group_id                          #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("color_label,expected_spectrum", [
    ("Heather grey", 2),          # heather matches first → Dusty Light
    ("Pure light grey", 3),       # light wins when no heather
    ("Medium dusty blue", 4),     # Medium dusty (compound)
    ("Dark navy", 1),             # Dark
    ("Bright pink", 6),
    ("Black", 0),                 # No shade keyword → Unknown
])
def test_extract_color_spectrum_id(color_label, expected_spectrum):
    assert fl.extract_color_spectrum_id(color_label) == expected_spectrum


@pytest.mark.parametrize("product_type,expected_group", [
    ("T-shirt", 1),   # Garment Upper body
    ("Trousers", 2),  # Garment Lower body
    ("Dress", 3),     # Garment Full body
    ("Bag", 6),       # Accessories
    ("Sneakers", 7),  # Shoes
    ("Bra", 5),       # Underwear
    ("Cap", 6),       # Accessories — NEW
    ("Umbrella", 6),  # NEW
    # Universe-expansion (2026-05): new product types must route to the
    # correct product_group so the cube's group-level rollups stay coherent.
    ("Hat/brim", 6),
    ("Felt hat", 6),
    ("Straw hat", 6),
    ("Headband", 6),
    ("Tie", 6),
    ("Bootie", 7),
    ("Night gown", 9),
    (None, 0),
    ("xxxx", 0),
])
def test_extract_product_group_id(product_type, expected_group):
    assert fl.extract_product_group_id(product_type) == expected_group
