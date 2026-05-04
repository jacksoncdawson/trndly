"""
Uniqlo retail scraper for trndly trend signals.

Scrapes Uniqlo's "New Arrivals" and category pages using a real browser
(Playwright) to count how often each color, category, and material attribute
appears across featured product listings. Normalizes those counts to 0–1 and
writes the result as trend_signals_uniqlo.csv.

WHERE EACH ATTRIBUTE COMES FROM
--------------------------------
- category : product title keywords  ("jeans" → pants, "hoodie" → tops, etc.)
- material  : product title keywords  ("linen", "denim", "knit", etc.)
             + category inference when no material keyword is in the title
- color     : color swatch aria-labels or chip labels
             + product title keywords as a fallback

BOT PROTECTION
--------------
Uniqlo does not use Akamai, PerimeterX, or Imperva, making it well-suited
for headless / pipeline use. The standard de-fingerprinting init scripts are
applied as a precaution. If you hit consistent empty pages, try --headless
false.

NOTE ON URLS
-------------
If any page returns 0 titles, open the URL in your browser to verify the
path is still correct — Uniqlo occasionally restructures categories. Update
the URL from your browser's address bar and re-run.

SETUP (one-time)
----------------
  pip install playwright
  playwright install chromium

Usage:
  python uniqlo_scraper.py
  python uniqlo_scraper.py --output-path path/to/trend_signals_uniqlo.csv
  python uniqlo_scraper.py --existing-path trend_signals_uniqlo.csv --blend-weight 0.5
  python uniqlo_scraper.py --headless false
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from pipelines.collectors.scrape_url_utils import (  # noqa: E402
    dedupe_product_urls_preserve_order,
)
from pipelines.training.feature_contract import (  # noqa: E402
    DEFAULT_MISSING_SCORE,
    FEATURE_TYPES,
    validate_trend_signals_frame,
)

# --------------------------------------------------------------------------- #
# Target pages                                                                  #
# --------------------------------------------------------------------------- #

UNIQLO_PAGES = [
    {"url": "https://www.uniqlo.com/us/en/women/new-arrivals/",          "label": "women new arrivals"},
    {"url": "https://www.uniqlo.com/us/en/men/new-arrivals/",            "label": "men new arrivals"},
    {"url": "https://www.uniqlo.com/us/en/women/tops",                   "label": "women tops"},
    {"url": "https://www.uniqlo.com/us/en/men/tops",                     "label": "men tops"},
    {"url": "https://www.uniqlo.com/us/en/women/bottoms",                "label": "women bottoms"},
    {"url": "https://www.uniqlo.com/us/en/men/bottoms",                  "label": "men bottoms"},
    {"url": "https://www.uniqlo.com/us/en/women/dresses-and-skirts",     "label": "women dresses"},
    {"url": "https://www.uniqlo.com/us/en/women/sweaters",               "label": "women outerwear"},
    {"url": "https://www.uniqlo.com/us/en/men/sweaters",                 "label": "men outerwear"},
]

# --------------------------------------------------------------------------- #
# Selectors                                                                     #
#                                                                               #
# PRODUCT NAME selectors — tried in order, first non-empty match wins.         #
# Uniqlo uses a React SPA with class names following a fr-ec-* pattern.        #
# --------------------------------------------------------------------------- #

PRODUCT_NAME_SELECTORS = [
    # Confirmed from live inspection — scoped to product tiles so we don't
    # pick up prices, filters, and other ITOTypography elements on the page.
    "[class*='product-tile'] [data-testid='ITOTypography']",
    "[class*='product-tile__name'] [data-testid='ITOTypography']",
    "[class*='product-tile__description'] [data-testid='ITOTypography']",
    # Generic fallbacks
    "[class*='product-name']",
    "[class*='ProductName']",
    "article h2",
    "article h3",
    "li[class*='product'] h2",
    "li[class*='product'] h3",
]

# COLOR SWATCH selectors — Uniqlo shows color chips on product tiles with
# the color name in aria-label or as a data attribute.
COLOR_SWATCH_SELECTORS = [
    # Uniqlo-specific
    "[class*='fr-ec-color-chip'][aria-label]",
    "[class*='fr-ec-color-chip'] [aria-label]",
    "[class*='fr-ec-product-tile'] [aria-label][class*='color']",
    "[class*='ColorChip'][aria-label]",
    "[class*='color-chip'][aria-label]",
    # Generic fallbacks
    "[class*='ColorSwatch'] [aria-label]",
    "[class*='color-swatch'] [aria-label]",
    "[class*='Swatch'] button[aria-label]",
    "[class*='swatch'] button[aria-label]",
    "button[aria-label][class*='color']",
    "[data-color]",
    "[data-color-name]",
]

# Selector to wait for before extracting — signals the product grid is ready
PRODUCT_GRID_WAIT_SELECTORS = [
    # Confirmed — wait for the product name element to appear
    "[data-testid='ITOTypography']",
    # Generic fallbacks
    "[class*='ProductGrid']",
    "[class*='product-grid']",
    "[class*='ProductTile']",
    "article",
    "li[class*='product']",
]

# Selectors for product card links on Uniqlo listing pages
PRODUCT_CARD_LINK_SELECTORS = [
    "[class*='product-tile'] a[href*='/products/']",
    "[class*='ProductTile'] a[href*='/products/']",
    "a[href*='/us/en/products/']",
    "[class*='product'] a[href]",
    "article a[href]",
]

# --------------------------------------------------------------------------- #
# Attribute keyword maps                                                        #
# --------------------------------------------------------------------------- #

# For COLOR: Uniqlo uses "SMOKY" prefix for muted tones and other descriptive
# names. These are listed up-front before generic fallbacks.
COLOR_KEYWORDS: list[tuple[str, str]] = [
    # Uniqlo-specific names
    ("smoky blue", "blue"),
    ("smoky navy", "navy"),
    ("smoky green", "green"),
    ("smoky pink", "pink"),
    ("smoky yellow", "beige"),
    ("smoky gray", "gray"),
    ("smoky grey", "gray"),
    ("smoky black", "black"),
    ("off black", "black"),
    ("off white", "white"),
    ("natural", "beige"),
    ("ecru", "beige"),
    ("oatmeal", "beige"),
    # Shade-qualified generics (color_spectrum from lookup: Dark, Dusty Light, Light, Medium, Bright)
    ("dusty light blue", "blue"),
    ("medium dusty blue", "blue"),
    ("light blue", "blue"),
    ("medium blue", "blue"),
    ("dark blue", "blue"),
    ("bright blue", "blue"),
    ("dark navy", "navy"),
    ("light green", "green"),
    ("dark green", "green"),
    ("bright green", "green"),
    ("dusty green", "green"),
    ("light pink", "pink"),
    ("bright pink", "pink"),
    ("hot pink", "pink"),
    ("dark pink", "pink"),
    ("light red", "red"),
    ("bright red", "red"),
    ("dark red", "red"),
    ("light gray", "gray"),
    ("dark gray", "gray"),
    ("light grey", "gray"),
    ("dark grey", "gray"),
    ("dark brown", "brown"),
    ("light brown", "brown"),
    ("light purple", "purple"),
    ("dark purple", "purple"),
    ("bright purple", "purple"),
    ("mustard", "beige"),
    ("butter", "beige"),
    ("golden", "beige"),
    ("terracotta", "red"),
    ("orange", "orange"),
    ("tangerine", "orange"),
    ("yellow", "yellow"),
    ("lemon", "yellow"),
    ("gold", "yellow"),
    ("silver", "metal"),
    ("metallic", "metal"),
    ("brick", "red"),
    ("wine", "red"),
    ("rust", "red"),
    ("coral", "pink"),
    ("blush", "pink"),
    ("sage", "green"),
    ("olive", "green"),
    ("forest", "green"),
    ("moss", "green"),
    ("camel", "beige"),
    ("tan", "beige"),
    ("khaki", "beige"),
    ("plum", "purple"),
    ("lavender", "purple"),
    ("lilac", "purple"),
    # Shared generics
    ("navy", "navy"),
    ("black", "black"),
    ("white", "white"),
    ("cream", "white"),
    ("ivory", "white"),
    ("red", "red"),
    ("burgundy", "red"),
    ("maroon", "red"),
    ("green", "green"),
    ("cobalt", "blue"),
    ("indigo", "blue"),
    ("turquoise", "blue"),
    ("aqua", "blue"),
    ("teal", "blue"),
    ("blue", "blue"),
    ("beige", "beige"),
    ("sand", "beige"),
    ("taupe", "beige"),
    ("amber", "brown"),
    ("mocha", "brown"),
    ("chocolate", "brown"),
    ("cognac", "brown"),
    ("espresso", "brown"),
    ("brown", "brown"),
    ("dusty pink", "pink"),
    ("mauve", "pink"),
    ("rose", "pink"),
    ("peach", "pink"),
    ("pink", "pink"),
    ("violet", "purple"),
    ("purple", "purple"),
    ("charcoal", "gray"),
    ("heather", "gray"),
    ("slate", "gray"),
    ("stone", "gray"),
    ("grey", "gray"),
    ("gray", "gray"),
]

# For CATEGORY: checked against product title.
# Uniqlo uses terms like "Ultra Stretch", "AIRism", "Heattech" in titles
# but the garment type words are standard enough for the shared list.
CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    # Pants / bottoms
    ("barrel jean", "pants"),
    ("straight jean", "pants"),
    ("slim jean", "pants"),
    ("wide-leg jean", "pants"),
    ("wide leg jean", "pants"),
    ("jeans", "pants"),
    ("trouser", "pants"),
    ("chino", "pants"),
    ("legging", "pants"),
    ("jogger", "pants"),
    ("jort", "shorts"),
    ("sweatpant", "pants"),
    ("dungarees", "pants"),
    ("overalls", "pants"),
    ("pant", "pants"),
    # Shorts
    ("shorts", "shorts"),
    # Skirt
    ("sarong", "skirt"),
    ("skirt", "skirt"),
    # Dress / full body
    ("playsuit", "dress"),
    ("romper", "dress"),
    ("jumpsuit", "dress"),
    ("bodysuit", "dress"),
    ("body suit", "dress"),
    ("dress", "dress"),
    # Outerwear
    ("anorak", "outerwear"),
    ("windbreaker", "outerwear"),
    ("gilet", "outerwear"),
    ("waistcoat", "outerwear"),
    ("jacket", "outerwear"),
    ("coat", "outerwear"),
    ("parka", "outerwear"),
    ("puffer", "outerwear"),
    ("blouson", "outerwear"),
    ("blazer", "outerwear"),
    ("cardigan", "outerwear"),
    # Tops
    ("polo", "tops"),
    ("corset", "tops"),
    ("hoodie", "tops"),
    ("sweatshirt", "tops"),
    ("sweater", "tops"),
    ("pullover", "tops"),
    ("flannel", "tops"),
    ("shirt", "tops"),
    ("tee", "tops"),
    ("t-shirt", "tops"),
    ("crop", "tops"),
    ("blouse", "tops"),
    ("cami", "tops"),
    ("tank", "tops"),
    ("vest", "tops"),
    ("top", "tops"),
    # Shoes
    ("pump", "shoes"),
    ("heel", "shoes"),
    ("loafer", "shoes"),
    ("mule", "shoes"),
    ("clog", "shoes"),
    ("ballerina", "shoes"),
    ("slipper", "shoes"),
    ("flip flop", "shoes"),
    ("wedge", "shoes"),
    ("sneaker", "shoes"),
    ("boot", "shoes"),
    ("sandal", "shoes"),
    ("shoe", "shoes"),
    # Accessories
    ("sunglasses", "accessories"),
    ("glasses", "accessories"),
    ("watch", "accessories"),
    ("wallet", "accessories"),
    ("bracelet", "accessories"),
    ("necklace", "accessories"),
    ("earring", "accessories"),
    ("ring", "accessories"),
    ("gloves", "accessories"),
    ("bag", "accessories"),
    ("belt", "accessories"),
    ("hat", "accessories"),
    ("beanie", "accessories"),
    ("scarf", "accessories"),
    ("sock", "accessories"),
]

# For MATERIAL: Uniqlo is very material-focused — AIRism (polyester),
# Heattech (polyester blend), flannel (cotton), etc.
# Sourced from lookup.csv material list + common retail keyword variants.
MATERIAL_KEYWORDS: list[tuple[str, str]] = [
    # Uniqlo brand materials first (before generic matches)
    ("airism", "polyester"),
    ("heattech", "polyester"),
    # Denim
    ("denim", "denim"),
    ("jean", "denim"),
    ("jort", "denim"),
    # Linen
    ("linen-blend", "linen"),
    ("linen", "linen"),
    # Silk / silk-like
    ("chiffon", "silk"),
    ("crepe", "silk"),
    ("georgette", "silk"),
    ("silk", "silk"),
    ("satin", "silk"),
    # Wool / wool-like
    ("cashmere", "wool"),
    ("merino", "wool"),
    ("shearling", "wool"),
    ("sherpa", "wool"),
    ("faux fur", "wool"),
    ("wool", "wool"),
    ("fleece", "wool"),
    # Leather / leather-like
    ("imitation leather", "leather"),
    ("imitation suede", "leather"),
    ("faux leather", "leather"),
    ("vegan leather", "leather"),
    ("suede", "leather"),
    ("leather", "leather"),
    # Knit / knit-like
    ("rib-knit", "knit"),
    ("ribbed", "knit"),
    ("jersey", "knit"),
    ("velvet", "knit"),
    ("velour", "knit"),
    ("knit", "knit"),
    ("crochet", "knit"),
    ("waffle", "knit"),
    # Polyester / synthetics
    ("nylon", "polyester"),
    ("acrylic", "polyester"),
    ("tulle", "polyester"),
    ("mesh", "polyester"),
    ("spandex", "polyester"),
    ("elastane", "polyester"),
    ("polyester", "polyester"),
    ("recycled", "polyester"),
    # Cotton / cellulosics
    ("poplin", "cotton"),
    ("twill", "cotton"),
    ("terry", "cotton"),
    ("flannel", "cotton"),
    ("oxford", "cotton"),
    ("corduroy", "cotton"),
    ("canvas", "cotton"),
    ("tencel", "cotton"),
    ("lyocell", "cotton"),
    ("modal", "cotton"),
    ("viscose", "cotton"),
    ("rayon", "cotton"),
    ("cotton", "cotton"),
]

# When no material keyword is in the title, infer from category (pants: see
# extract_material — do not default all trousers to denim).
CATEGORY_TO_MATERIAL_DEFAULT: dict[str, str] = {
    "shorts": "cotton",
    "dress": "cotton",
    "tops": "cotton",
    "outerwear": "polyester",
    "shoes": "leather",
    "accessories": "cotton",
    "skirt": "cotton",
}

GRAPHICAL_APPEARANCE_KEYWORDS: list[tuple[str, str]] = [
    ("polka dot", "Dot"),
    ("striped", "Stripe"),
    ("stripe", "Stripe"),
    ("plaid", "Check"),
    ("gingham", "Check"),
    ("tartan", "Check"),
    ("check", "Check"),
    ("floral", "All over pattern"),
    ("animal print", "All over pattern"),
    ("all over", "All over pattern"),
    ("graphic tee", "Front print"),
    ("graphic", "Front print"),
    ("placement print", "Placement print"),
    ("print", "Placement print"),
    ("embroidered", "Embroidery"),
    ("embroidery", "Embroidery"),
    ("lace", "Lace"),
    ("denim", "Denim"),
    ("heather", "Melange"),
    ("melange", "Melange"),
    ("glitter", "Glittering/Metallic"),
    ("metallic", "Glittering/Metallic"),
    ("sequin", "Sequin"),
    ("mesh", "Mesh"),
    ("jacquard", "Jacquard"),
    ("chambray", "Chambray"),
    ("argyle", "Argyle"),
    ("dot", "Dot"),
]
GRAPHICAL_APPEARANCE_DEFAULT = "Solid"

PRODUCT_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("polo", "Polo shirt"),
    ("hoodie", "Hoodie"),
    ("sweatshirt", "Hoodie"),
    ("cardigan", "Cardigan"),
    ("sweater", "Sweater"),
    ("pullover", "Sweater"),
    ("blazer", "Blazer"),
    ("puffer", "Jacket"),
    ("windbreaker", "Jacket"),
    ("jacket", "Jacket"),
    ("parka", "Coat"),
    ("anorak", "Coat"),
    ("coat", "Coat"),
    ("jeans", "Trousers"),
    ("jean", "Trousers"),
    ("trouser", "Trousers"),
    ("chino", "Trousers"),
    ("jogger", "Trousers"),
    ("sweatpant", "Trousers"),
    ("jort", "Shorts"),
    ("legging", "Leggings/Tights"),
    ("dungarees", "Dungarees"),
    ("overalls", "Dungarees"),
    ("pant", "Trousers"),
    ("shorts", "Shorts"),
    ("sarong", "Sarong"),
    ("skirt", "Skirt"),
    ("playsuit", "Jumpsuit/Playsuit"),
    ("romper", "Jumpsuit/Playsuit"),
    ("jumpsuit", "Jumpsuit/Playsuit"),
    ("bodysuit", "Bodysuit"),
    ("body suit", "Bodysuit"),
    ("dress", "Dress"),
    ("t-shirt", "T-shirt"),
    ("tee", "T-shirt"),
    ("cami", "Vest top"),
    ("tank", "Vest top"),
    ("vest", "Vest top"),
    ("blouse", "Blouse"),
    ("crop", "Top"),
    ("shirt", "Shirt"),
    ("top", "Top"),
    ("sneaker", "Sneakers"),
    ("boot", "Boots"),
    ("sandal", "Sandals"),
    ("pump", "Pumps"),
    ("loafer", "Flat shoe"),
    ("mule", "Flat shoe"),
    ("ballerina", "Ballerinas"),
    ("slipper", "Slippers"),
    ("wedge", "Wedge"),
    ("heel", "Heels"),
    ("shoe", "Other shoe"),
    ("sunglasses", "Sunglasses"),
    ("glasses", "Eyeglasses"),
    ("watch", "Watch"),
    ("wallet", "Wallet"),
    ("bracelet", "Bracelet"),
    ("necklace", "Necklace"),
    ("earring", "Earring"),
    ("ring", "Ring"),
    ("gloves", "Gloves"),
    ("bag", "Bag"),
    ("belt", "Belt"),
    ("beanie", "Beanie"),
    ("hat", "Hat/beanie"),
    ("scarf", "Scarf"),
    ("sock", "Socks"),
]

COLOR_MASTER_TO_ID: dict[str, int] = {
    "black": 1,
    "blue": 2,
    "navy": 2,
    "white": 3,
    "beige": 4,
    "green": 5,
    "gray": 6,
    "grey": 6,
    "red": 7,
    "pink": 8,
    "brown": 9,
    "yellow": 10,
    "orange": 11,
    "metal": 12,
    "purple": 13,
}

GENDER_TO_ID: dict[str, int] = {"women": 1, "unisex": 2, "men": 3}

GRAPHICAL_APPEARANCE_TO_ID: dict[str, int] = {
    # Aligned with trndly/EDA/data/lookup.csv (graphical_appearance)
    "Unknown": 0,
    "Solid": 1,
    "All over pattern": 2,
    "Denim": 3,
    "Melange": 4,
    "Stripe": 5,
    "Lace": 6,
    "Check": 7,
    "Placement print": 8,
    "Embroidery": 9,
    "Dot": 10,
    "Front print": 11,
    "Colour blocking": 12,
    "Glittering/Metallic": 13,
    "Contrast": 14,
    "Jacquard": 15,
    "Treatment": 16,
    "Metallic": 17,
    "Mixed solid/pattern": 18,
    "Sequin": 19,
    "Mesh": 20,
    "Neps": 21,
    "Chambray": 22,
    "Slub": 23,
    "Transparent": 24,
    "Argyle": 25,
    "Hologram": 26,
}

MATERIAL_TO_ID: dict[str, int] = {
    "cotton": 1, "knit": 6, "denim": 3, "linen": 12, "silk": 26,
    "wool": 9, "polyester": 15, "leather": 18,
}

PRODUCT_TYPE_TO_ID: dict[str, int] = {
    "Trousers": 1, "Dress": 2, "Sweater": 3, "T-shirt": 4, "Top": 5,
    "Blouse": 6, "Vest top": 7, "Shorts": 11, "Skirt": 13, "Shirt": 14,
    "Leggings/Tights": 15, "Jacket": 16, "Socks": 17, "Blazer": 18,
    "Hoodie": 19, "Cardigan": 20, "Bag": 22, "Jumpsuit/Playsuit": 23,
    "Belt": 24, "Earring": 26, "Boots": 27, "Scarf": 29, "Necklace": 30,
    "Coat": 31, "Sandals": 32, "Bodysuit": 33, "Sunglasses": 34,
    "Sneakers": 35, "Polo shirt": 39, "Hat/beanie": 41, "Flat shoe": 44,
    "Ballerinas": 46, "Sarong": 47, "Wedge": 49, "Ring": 51, "Pumps": 53,
    "Dungarees": 54, "Gloves": 55, "Heels": 68, "Watch": 70, "Wallet": 73,
    "Beanie": 74, "Eyeglasses": 95, "Bracelet": 63, "Flip flop": 59,
    "Slippers": 60, "Other shoe": 58,
}

PRODUCT_TYPE_TO_GROUP_ID: dict[str, int] = {
    "T-shirt": 1, "Top": 1, "Blouse": 1, "Vest top": 1, "Shirt": 1,
    "Sweater": 1, "Hoodie": 1, "Cardigan": 1, "Polo shirt": 1,
    "Jacket": 1, "Coat": 1, "Blazer": 1,
    "Trousers": 2, "Shorts": 2, "Skirt": 2, "Leggings/Tights": 2,
    "Dungarees": 2, "Sarong": 2,
    "Dress": 3, "Jumpsuit/Playsuit": 3, "Bodysuit": 3,
    "Bag": 6, "Belt": 6, "Scarf": 6, "Hat/beanie": 6, "Beanie": 6,
    "Gloves": 6, "Sunglasses": 6, "Eyeglasses": 6, "Watch": 6,
    "Wallet": 6, "Bracelet": 6, "Necklace": 6, "Earring": 6, "Ring": 6,
    "Boots": 7, "Sneakers": 7, "Sandals": 7, "Flat shoe": 7,
    "Ballerinas": 7, "Slippers": 7, "Flip flop": 7, "Wedge": 7,
    "Heels": 7, "Pumps": 7, "Other shoe": 7,
    "Socks": 8,
}

COLOR_SPECTRUM_KEYWORDS: list[tuple[str, int]] = [
    ("medium dusty", 4), ("dusty", 2), ("heather", 2), ("muted", 2),
    ("washed", 2), ("faded", 2), ("light", 3), ("pale", 3), ("soft", 3),
    ("pastel", 3), ("cream", 3), ("bright", 6), ("vivid", 6), ("neon", 6),
    ("electric", 6), ("dark", 1), ("deep", 1), ("rich", 1), ("medium", 5), ("mid", 5),
]


# --------------------------------------------------------------------------- #
# Attribute extraction                                                          #
# --------------------------------------------------------------------------- #

def _first_match(text: str, keyword_map: list[tuple[str, str]]) -> str | None:
    lowered = text.lower()
    for keyword, mapped in keyword_map:
        if keyword in lowered:
            return mapped
    return None


def extract_color(text: str) -> str | None:
    return _first_match(text, COLOR_KEYWORDS)


def extract_category(text: str) -> str | None:
    return _first_match(text, CATEGORY_KEYWORDS)


def extract_material(text: str, inferred_category: str | None = None) -> str | None:
    result = _first_match(text, MATERIAL_KEYWORDS)
    if result:
        return result
    if inferred_category == "pants":
        lowered = text.lower()
        if any(
            hint in lowered
            for hint in (
                "jean",
                "denim",
                "jort",
                "5-pocket",
                "five pocket",
                "selvedge",
                "selvage",
            )
        ):
            return "denim"
        return None
    if inferred_category:
        return CATEGORY_TO_MATERIAL_DEFAULT.get(inferred_category)
    return None


def extract_graphical_appearance(text: str) -> str:
    result = _first_match(text, GRAPHICAL_APPEARANCE_KEYWORDS)
    return result if result else GRAPHICAL_APPEARANCE_DEFAULT


def extract_product_type(text: str) -> str | None:
    return _first_match(text, PRODUCT_TYPE_KEYWORDS)


def extract_color_spectrum_id(color_label: str) -> int:
    lowered = color_label.lower()
    for keyword, spectrum_id in COLOR_SPECTRUM_KEYWORDS:
        if keyword in lowered:
            return spectrum_id
    return 0


def extract_product_group_id(product_type: str | None) -> int:
    if not product_type:
        return 0
    return PRODUCT_TYPE_TO_GROUP_ID.get(product_type, 0)


# --------------------------------------------------------------------------- #
# Browser helpers                                                               #
# --------------------------------------------------------------------------- #

def _wait_for_products(page: "Page", timeout_ms: int = 15_000) -> bool:
    """
    Wait until at least one product grid selector appears on the page.
    Returns True if found, False if all selectors timed out.
    """
    for selector in PRODUCT_GRID_WAIT_SELECTORS:
        try:
            page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


def _scroll_to_bottom(page: "Page", pause_secs: float = 1.2, max_scrolls: int = 8) -> None:
    for _ in range(max_scrolls):
        page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        time.sleep(pause_secs)


def _extract_product_names(page: "Page") -> list[str]:
    """Extract product title text using the first selector that returns results."""
    for selector in PRODUCT_NAME_SELECTORS:
        try:
            elements = page.query_selector_all(selector)
            texts = [el.inner_text().strip() for el in elements if el.inner_text().strip()]
            if texts:
                return texts
        except Exception:
            continue
    return []


def _extract_swatch_colors(page: "Page") -> list[str]:
    """
    Extract color names from swatch/chip aria-labels.
    Uniqlo typically exposes color names as aria-label on color chip elements.
    """
    for selector in COLOR_SWATCH_SELECTORS:
        try:
            elements = page.query_selector_all(selector)
            if not elements:
                continue
            labels: list[str] = []
            for el in elements:
                label = (
                    el.get_attribute("aria-label")
                    or el.get_attribute("alt")
                    or el.get_attribute("title")
                    or el.get_attribute("data-color")
                    or el.get_attribute("data-color-name")
                    or ""
                )
                if label.strip():
                    labels.append(label.strip())
            if labels:
                return labels
        except Exception:
            continue
    return []


def _extract_pdp_swatch_colors(page: "Page") -> list[str]:
    """
    Color chips on the PDP only — scoped under main / buy-box so we do not
    collect chips from carousels, recommendations, or listing remnants.
    """
    for container in (
        "main",
        "[class*='pdp']",
        "[class*='Pdp']",
        "[class*='product-detail']",
        "[class*='ProductDetail']",
        "[class*='buy-box']",
        "[class*='BuyBox']",
    ):
        try:
            root = page.query_selector(container)
            if not root:
                continue
            for selector in COLOR_SWATCH_SELECTORS:
                try:
                    elements = root.query_selector_all(selector)
                    if not elements:
                        continue
                    labels: list[str] = []
                    for el in elements:
                        label = (
                            el.get_attribute("aria-label")
                            or el.get_attribute("alt")
                            or el.get_attribute("title")
                            or el.get_attribute("data-color")
                            or el.get_attribute("data-color-name")
                            or ""
                        )
                        if label.strip():
                            labels.append(label.strip())
                    if labels:
                        return labels
                except Exception:
                    continue
        except Exception:
            continue
    return []


def _dedupe_preserve_order(labels: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lab in labels:
        k = (lab or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(lab.strip())
    return out


def _filter_uniqlo_dom_color_noise(labels: list[str]) -> list[str]:
    """
    PDP color-chip selectors can still match quantity steppers, share, etc.
    Strip obvious non-color aria-labels before treating DOM as authoritative.
    """
    junk = (
        "close", "share", "minimum", "maximum", "increase", "decrease",
        "button", "cart", "checkout", "menu", "search", "account", "wishlist",
        "favorite", "notify", "quantity", "sign in", "sign-in",
        "add to", "added", "remove", "delete", "zoom", "play", "pause",
        "filter", "sort", "rating", "review",
    )
    out: list[str] = []
    for lab in labels:
        low = lab.lower().strip()
        if any(j in low for j in junk):
            continue
        if len(lab) > 80:
            continue
        out.append(lab.strip())
    return out


def _uniqlo_color_batch_quality(labels: list[str]) -> float:
    """Share of labels that look like real Uniqlo color names (keyword or code)."""
    if not labels:
        return 0.0
    hits = 0
    for lab in labels:
        if extract_color(lab):
            hits += 1
        elif re.match(r"^\d{1,3}\s+\S", lab.strip()):
            hits += 1
    return hits / len(labels)


def _uniqlo_pdp_product_slug(prod_url: str) -> str | None:
    """e.g. .../products/E458186-000 → 'E458186-000' (uppercased)."""
    m = re.search(r"/products/([^/?#]+)", prod_url, re.I)
    if not m:
        return None
    slug = m.group(1).strip()
    if not slug:
        return None
    return slug.split(".")[0].upper()


def _uniqlo_item_codes_for_match(item: dict) -> set[str]:
    """Collect product identifiers from one commerce API item for URL matching."""
    out: set[str] = set()
    candidates: list[str] = []
    for key in (
        "losProductCode",
        "parentLosProductCode",
        "productCode",
        "parentProductCode",
        "code",
        "shortName",
    ):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip().upper())
    for key in ("productId", "product_id", "id"):
        v = item.get(key)
        if v is not None and str(v).strip():
            candidates.append(str(v).strip().upper())
    for s in candidates:
        out.add(s)
        out.add(re.sub(r"\s+", "", s))
        digits = re.sub(r"\D", "", s)
        if len(digits) >= 6:
            out.add(digits)
    return {x for x in out if x}


def _uniqlo_api_item_matches_pdp(item: dict, prod_url: str) -> bool:
    slug = _uniqlo_pdp_product_slug(prod_url)
    if not slug:
        return False
    slug_alnum = re.sub(r"[^A-Z0-9]", "", slug.upper())
    slug_digits = re.sub(r"\D", "", slug_alnum)
    for c in _uniqlo_item_codes_for_match(item):
        c_alnum = re.sub(r"[^A-Z0-9]", "", c.upper())
        if not c_alnum:
            continue
        if slug_alnum == c_alnum:
            return True
        if len(c_alnum) >= 6 and (c_alnum in slug_alnum or slug_alnum in c_alnum):
            return True
        cd = re.sub(r"\D", "", c_alnum)
        if (
            len(slug_digits) >= 6
            and len(cd) >= 6
            and (slug_digits == cd or slug_digits in cd or cd in slug_digits)
        ):
            return True
    return False


def _pick_best_api_color_batch_for_pdp(
    api_entries: list[tuple[list[str], dict]],
    prod_url: str,
) -> list[str]:
    """
    Commerce responses often include multiple `result.items` (carousel, etc.).
    Each entry is one item's color list plus that item's dict. Prefer the batch
    whose product id matches the PDP URL, then best color-keyword quality, then
    the **largest** batch on ties (main SKU usually has the full color run;
    tiny carousel items often tie on quality but have fewer swatches).
    """
    if not api_entries:
        return []
    reasonable = [(b, it) for b, it in api_entries if 1 <= len(b) <= 48]
    if not reasonable:
        flat: list[str] = []
        for b, _ in api_entries:
            flat.extend(b)
        return _dedupe_preserve_order(flat)

    matched = [(b, it) for b, it in reasonable if _uniqlo_api_item_matches_pdp(it, prod_url)]
    pool = matched if matched else reasonable

    def sort_key(entry: tuple[list[str], dict]) -> tuple[float, int]:
        b, _ = entry
        return (_uniqlo_color_batch_quality(b), len(b))

    return max(pool, key=sort_key)[0]


# --------------------------------------------------------------------------- #
# Main scraping loop                                                            #
# --------------------------------------------------------------------------- #

def scrape_uniqlo(
    sleep_between_pages: float = 3.0,
    headless: bool = True,
) -> tuple[list[str], list[str]]:
    """
    Scrape all Uniqlo pages.
    Returns (product_titles, color_labels).

    Colors are sourced from Uniqlo's internal product API (intercepted via
    Playwright's response handler) which returns proper color names like
    "Off White", "Dark Navy", etc. The DOM color chips only carry numeric
    codes ("37") so this interception approach is required for accurate colors.
    Falls back to DOM swatch labels and then title keywords if the API yields
    nothing.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright is not installed.")
        print("  Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    all_titles: list[str] = []
    all_swatch_colors: list[str] = []
    api_colors: list[str] = []
    raw_items: list[dict] = []

    def _handle_response(response: object) -> None:
        """Intercept Uniqlo's internal product API to collect real color names."""
        try:
            url = response.url  # type: ignore[attr-defined]
            if "/api/commerce/v5/en/products" not in url:
                return
            if response.status != 200:  # type: ignore[attr-defined]
                return
            data = response.json()  # type: ignore[attr-defined]
            items = data.get("result", {}).get("items", [])
            for item in items:
                for color_obj in item.get("colors", []):
                    name = color_obj.get("name", "").strip()
                    if name:
                        api_colors.append(name)
        except Exception:
            pass

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "window.chrome = {runtime: {}};"
        )

        page = context.new_page()
        page.on("response", _handle_response)

        for page_info in UNIQLO_PAGES:
            url = page_info["url"]
            label = page_info["label"]
            print(f"  [{label}] → {url}")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                grid_found = _wait_for_products(page, timeout_ms=15_000)
                if not grid_found:
                    page_title = page.title()
                    print(f"    WARNING: product grid not found (page title: '{page_title}')")

                time.sleep(1.5)
                _scroll_to_bottom(page)

                titles = _extract_product_names(page)
                swatches = _extract_swatch_colors(page)

                api_count = len(api_colors)
                print(f"    {len(titles)} product titles, {len(swatches)} DOM swatches, {api_count} API colors so far")
                all_titles.extend(titles)
                all_swatch_colors.extend(swatches)

                gender = "women" if label.startswith("women") else "men" if label.startswith("men") else "unisex"
                # Snapshot API colors collected so far for this page's titles.
                # api_colors accumulates across pages, so take a copy of the
                # colors added during this page's load (swatches as fallback).
                page_colors = _dedupe_preserve_order(list(api_colors) if api_colors else list(swatches))
                for t in titles:
                    raw_items.append({"title": t, "gender": gender, "page_swatches": page_colors})

            except Exception as exc:
                print(f"    ERROR: {exc}")

            time.sleep(sleep_between_pages)

        browser.close()

    # Prefer API-intercepted colors (real names) over DOM swatch codes
    final_colors = api_colors if api_colors else all_swatch_colors
    if api_colors:
        print(f"  Using {len(api_colors)} API-intercepted color names")
    else:
        print(f"  API interception yielded nothing — falling back to {len(all_swatch_colors)} DOM swatch labels")

    return all_titles, final_colors, raw_items


def _extract_product_urls(page: "Page", base_url: str = "https://www.uniqlo.com") -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for selector in PRODUCT_CARD_LINK_SELECTORS:
        try:
            elements = page.query_selector_all(selector)
            for el in elements:
                href = el.get_attribute("href") or ""
                if not href:
                    continue
                if href.startswith("/"):
                    href = base_url + href
                if href not in seen and "uniqlo.com" in href:
                    seen.add(href)
                    urls.append(href)
            if urls:
                return urls
        except Exception:
            continue
    return urls


def scrape_uniqlo_detailed(
    sleep_between_pages: float = 3.0,
    sleep_between_products: float = 2.0,
    headless: bool = True,
    max_products_per_page: int = 50,
) -> list[dict]:
    """
    Two-step scraper: listing page → product URLs → visit each product page.

    Colors are chosen in this order:
      1. **Scoped PDP swatches** — chips under `main` / buy-box only (matches
         the Color row on the product page, not site-wide chips).
      2. **Commerce API** — responses may include several `result.items` (this
         PDP plus recommendations). We take **one item's** `colors` list at a time,
         prefer the item whose product code matches the PDP URL, then quality /
         smallest batch on ties.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed.")
        sys.exit(1)

    raw_items: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "window.chrome = {runtime: {}};"
        )
        page = context.new_page()

        for page_info in UNIQLO_PAGES:
            url   = page_info["url"]
            label = page_info["label"]
            gender = "women" if label.startswith("women") else "men" if label.startswith("men") else "unisex"
            print(f"\n  [{label}] → {url}")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                _wait_for_products(page, timeout_ms=15_000)
                time.sleep(1.5)
                _scroll_to_bottom(page)
                raw_urls = _extract_product_urls(page)
                product_urls = dedupe_product_urls_preserve_order(raw_urls)
                print(
                    f"    Found {len(product_urls)} distinct product URLs "
                    f"from {len(raw_urls)} listing links (cap {max_products_per_page})"
                )
                product_urls = product_urls[:max_products_per_page]
            except Exception as exc:
                print(f"    ERROR loading listing page: {exc}")
                continue

            for i, prod_url in enumerate(product_urls, 1):
                api_entries: list[tuple[list[str], dict]] = []

                def _handle_product_response(response: object) -> None:
                    try:
                        r_url = response.url  # type: ignore[attr-defined]
                        if "/api/commerce/v5/en/products" not in r_url:
                            return
                        if response.status != 200:  # type: ignore[attr-defined]
                            return
                        data = response.json()  # type: ignore[attr-defined]
                        items = data.get("result", {}).get("items", [])
                        for item in items:
                            batch: list[str] = []
                            for c in item.get("colors", []):
                                name = c.get("name", "").strip()
                                if name:
                                    batch.append(name)
                            if batch:
                                api_entries.append((batch, item))
                    except Exception:
                        pass

                page.on("response", _handle_product_response)
                try:
                    page.goto(prod_url, wait_until="domcontentloaded", timeout=30_000)
                    time.sleep(1.5)

                    # Title from PDP
                    title = ""
                    for sel in ["h1[class*='product-name']", "h1[data-testid='ITOTypography']",
                                "[class*='product-name'] h1", "h1"]:
                        try:
                            el = page.query_selector(sel)
                            if el:
                                title = el.inner_text().strip()
                                if title:
                                    break
                        except Exception:
                            continue

                    # 1) Scoped PDP swatches (what the shopper sees under Color).
                    # 2) API batches — multiple responses fire on a PDP; pick the batch
                    #    whose names look most like real colors (not smallest blindly).
                    # DOM selectors can still hit quantity/share controls; filter those
                    # and only prefer DOM when a solid share of labels map to colors.
                    dom_raw = _dedupe_preserve_order(_extract_pdp_swatch_colors(page))
                    dom_swatches = _filter_uniqlo_dom_color_noise(dom_raw)
                    api_swatches = _dedupe_preserve_order(
                        _pick_best_api_color_batch_for_pdp(api_entries, prod_url)
                    )
                    q_dom = _uniqlo_color_batch_quality(dom_swatches)
                    q_api = _uniqlo_color_batch_quality(api_swatches)
                    min_dom_quality = 0.34
                    dom_ok = (
                        dom_swatches
                        and q_dom >= min_dom_quality
                        and q_dom >= q_api
                    )
                    # Visible chips can be a subset; if API has many more plausible
                    # colors at similar quality, keep the API list.
                    api_richer = (
                        api_swatches
                        and len(api_swatches) >= len(dom_swatches) + 4
                        and q_api >= q_dom - 0.1
                    )
                    if dom_ok and not api_richer:
                        colors = dom_swatches
                    elif api_swatches:
                        colors = api_swatches
                    else:
                        colors = dom_swatches

                    colors = _dedupe_preserve_order(colors)

                    category     = extract_category(title)
                    material     = extract_material(title, inferred_category=category)
                    product_type = extract_product_type(title)
                    base_graphical = extract_graphical_appearance(title)

                    if colors:
                        for color_label in colors:
                            color    = extract_color(color_label) or extract_color(title)
                            graphical = extract_graphical_appearance(color_label)
                            if graphical == GRAPHICAL_APPEARANCE_DEFAULT:
                                graphical = base_graphical
                            raw_items.append({
                                "title": title, "gender": gender,
                                "color_raw": color_label, "color": color or "unknown",
                                "product_type_raw": product_type or "unknown",
                                "material_raw": material or "unknown",
                                "graphical_appearance_raw": graphical,
                            })
                    else:
                        color = extract_color(title)
                        raw_items.append({
                            "title": title, "gender": gender,
                            "color_raw": color or "unknown", "color": color or "unknown",
                            "product_type_raw": product_type or "unknown",
                            "material_raw": material or "unknown",
                            "graphical_appearance_raw": base_graphical,
                        })
                except Exception as exc:
                    print(f"      ERROR on {prod_url}: {exc}")
                finally:
                    page.remove_listener("response", _handle_product_response)

                if i % 10 == 0:
                    print(f"    ... {i}/{len(product_urls)} products scraped, {len(raw_items)} rows so far")
                time.sleep(sleep_between_products)

            time.sleep(sleep_between_pages)

        browser.close()

    print(f"\nDetailed scrape complete: {len(raw_items)} total item-color rows")
    return raw_items


# --------------------------------------------------------------------------- #
# Frequency counting and normalization                                          #
# --------------------------------------------------------------------------- #

def count_attribute_frequencies(
    titles: list[str],
    swatch_colors: list[str],
) -> dict[str, dict[str, int]]:
    """
    Count occurrences of each feature value across all products.

    Color is sourced from swatch_colors first (more reliable), then title fallback.
    Category and material come from title keyword matching.
    """
    counts: dict[str, dict[str, int]] = {ft: {} for ft in FEATURE_TYPES}

    for swatch_label in swatch_colors:
        color = extract_color(swatch_label)
        if color:
            counts["color"][color] = counts["color"].get(color, 0) + 1

    for title in titles:
        category = extract_category(title)
        if category:
            counts["category"][category] = counts["category"].get(category, 0) + 1

        material = extract_material(title, inferred_category=category)
        if material:
            counts["material"][material] = counts["material"].get(material, 0) + 1

        # Color from title only when no swatch/API colors were captured.
        # When the API interception works, swatch_colors contains real names
        # (e.g. "Off White", "Dark Navy") and title extraction is unnecessary.
        if not swatch_colors:
            color = extract_color(title)
            if color:
                counts["color"][color] = counts["color"].get(color, 0) + 1

    return counts


def normalize_counts(
    counts: dict[str, dict[str, int]],
    total_items: int,
) -> dict[str, dict[str, float]]:
    """
    Normalize raw feature counts to proportion scores.

    score = count / total_items  (actual market-share proportion)

    Using total items scraped as the denominator (instead of the per-feature
    max count) makes scores directly comparable across retailers and over time:
    a score of 0.30 always means "30% of products on this site had this value".
    """
    denom = max(total_items, 1)
    scores: dict[str, dict[str, float]] = {}
    for feature_type, value_counts in counts.items():
        scores[feature_type] = {
            value: round(count / denom, 6)
            for value, count in value_counts.items()
        }
    return scores


def build_trend_signals_frame(
    scores: dict[str, dict[str, float]],
    known_feature_values: dict[str, list[str]],
) -> pd.DataFrame:
    rows = []
    for feature_type, values in known_feature_values.items():
        type_scores = scores.get(feature_type, {})
        for feature_value in values:
            rows.append({
                "feature_type": feature_type,
                "feature_value": feature_value,
                "current": type_scores.get(feature_value, DEFAULT_MISSING_SCORE),
            })
    return pd.DataFrame(rows)


def build_raw_items_frame(
    raw_items: list[dict],
    scraped_date: str,
    retailer: str = "uniqlo",
) -> pd.DataFrame:
    """Build a one-row-per-item-color DataFrame with lookup-table IDs.

    Each product is expanded to one row per unique swatch/API color seen on
    its page, matching the H&M one-row-per-article-color schema.
    """
    rows = []
    for item in raw_items:
        title        = item["title"]
        gender       = item["gender"]
        product_type = extract_product_type(title)
        material     = extract_material(title, inferred_category=extract_category(title))
        graphical    = extract_graphical_appearance(title)

        if item.get("color"):
            color_labels = [item.get("color_raw") or item["color"]]
        elif item.get("page_swatches"):
            color_labels = _dedupe_preserve_order(item["page_swatches"])
        else:
            title_color = extract_color(title)
            color_labels = [title_color] if title_color else ["unknown"]

        for color_raw_lbl in color_labels:
            color = item.get("color") if item.get("color") else (
                extract_color(color_raw_lbl) if color_raw_lbl != "unknown" else None
            )
            rows.append({
                "scraped_at":               scraped_date,
                "retailer":                 retailer,
                "title":                    title,
                "gender":                   gender,
                "color_raw":                color_raw_lbl,
                "product_type_raw":         product_type or "unknown",
                "material_raw":             material or "unknown",
                "graphical_appearance_raw": graphical,
                "color_master_id":          COLOR_MASTER_TO_ID.get(color or "", 0),
                "color_spectrum_id":        extract_color_spectrum_id(color_raw_lbl),
                "gender_id":                GENDER_TO_ID.get(gender, 2),
                "product_type_id":          PRODUCT_TYPE_TO_ID.get(product_type or "", 0),
                "product_group_id":         extract_product_group_id(product_type),
                "material_id":              MATERIAL_TO_ID.get(material or "", 0),
                "graphical_appearance_id":  GRAPHICAL_APPEARANCE_TO_ID.get(graphical, 1),
            })
    return pd.DataFrame(rows)


def blend_with_existing(
    scraped: pd.DataFrame,
    existing_path: Path,
    blend_weight: float,
) -> pd.DataFrame:
    existing = pd.read_csv(existing_path)
    existing_validated = validate_trend_signals_frame(existing)
    existing_map = {
        (row["feature_type"], row["feature_value"]): row["current"]
        for _, row in existing_validated.iterrows()
    }
    blended = scraped.copy()
    for idx, row in blended.iterrows():
        key = (row["feature_type"], row["feature_value"])
        existing_score = existing_map.get(key, DEFAULT_MISSING_SCORE)
        blended.at[idx, "current"] = round(
            blend_weight * float(row["current"]) + (1.0 - blend_weight) * existing_score,
            6,
        )
    return blended


# --------------------------------------------------------------------------- #
# Argument parsing                                                              #
# --------------------------------------------------------------------------- #

KNOWN_FEATURE_VALUES: dict[str, list[str]] = {
    "color":    [
        "black", "white", "blue", "red", "green", "beige", "pink", "gray", "navy", "brown", "purple",
        "yellow", "orange", "metal",
    ],
    "category": ["pants", "shorts", "skirt", "dress", "tops", "outerwear", "shoes", "accessories"],
    "material": ["cotton", "denim", "linen", "silk", "wool", "polyester", "leather", "knit"],
}


def parse_args() -> argparse.Namespace:
    _synth = Path(__file__).resolve().parents[1] / "training" / "synthetic_data"
    default_output = _synth / "trend_signals_uniqlo.csv"
    default_items  = _synth / "items_uniqlo.csv"
    parser = argparse.ArgumentParser(
        description="Scrape Uniqlo new arrivals and write trend_signals_uniqlo.csv + items_uniqlo.csv."
    )
    parser.add_argument("--output-path", default=str(default_output))
    parser.add_argument(
        "--items-path", default=str(default_items),
        help="Where to write the raw items CSV (one row per product title).",
    )
    parser.add_argument(
        "--existing-path", default=None,
        help="Existing trend_signals.csv to blend with scraped scores.",
    )
    parser.add_argument(
        "--blend-weight", type=float, default=0.5,
        help="Weight for scraped scores when blending (default 0.5).",
    )
    parser.add_argument(
        "--sleep", type=float, default=3.0,
        help="Seconds between page loads (default 3.0).",
    )
    parser.add_argument(
        "--headless", type=lambda v: v.lower() != "false", default=True,
        help="Run headless browser. Pass 'false' for a visible window (default: true).",
    )
    parser.add_argument(
        "--detailed", action="store_true", default=False,
        help="Visit each product detail page for accurate per-item colors and material.",
    )
    parser.add_argument(
        "--max-products", type=int, default=50,
        help="Max products to visit per listing page in --detailed mode (default: 50).",
    )
    return parser.parse_args()


def main() -> None:
    import datetime

    args = parse_args()
    output_path = Path(args.output_path).expanduser().resolve()
    items_path  = Path(args.items_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    items_path.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"Uniqlo retail scraper\n"
        f"  pages: {len(UNIQLO_PAGES)}  headless: {args.headless}\n"
        f"  color source: API interception (falls back to DOM swatches, then title keywords)\n"
        f"  detailed mode: {args.detailed}"
        + (f"  (max {args.max_products} products/page)" if args.detailed else "") + "\n"
        f"  output: {output_path}\n"
        f"  items:  {items_path}"
    )

    titles, swatch_colors, raw_items = scrape_uniqlo(
        sleep_between_pages=args.sleep,
        headless=args.headless,
    )

    if args.detailed:
        print("\nRunning detailed per-product scrape for items CSV...")
        raw_items = scrape_uniqlo_detailed(
            sleep_between_pages=args.sleep,
            sleep_between_products=2.0,
            headless=args.headless,
            max_products_per_page=args.max_products,
        )

    print(f"\nTotal collected: {len(titles)} product titles, {len(swatch_colors)} colors")

    if not titles and not swatch_colors:
        print(
            "\nWARNING: Nothing was scraped. Uniqlo's bot protection may be\n"
            "blocking headless Chrome. Try running with --headless false\n"
            "to open a visible browser window, which is harder to detect."
        )

    counts = count_attribute_frequencies(titles, swatch_colors)
    scores = normalize_counts(counts, total_items=len(titles))

    print(f"\nTotal items used as proportion denominator: {len(titles)}")
    print("\nAttribute coverage:")
    for feature_type in FEATURE_TYPES:
        found = len(counts.get(feature_type, {}))
        total = len(KNOWN_FEATURE_VALUES[feature_type])
        print(f"  {feature_type}: {found}/{total} values seen")
        for value, score in sorted(scores.get(feature_type, {}).items(), key=lambda x: -x[1]):
            count = counts[feature_type].get(value, 0)
            print(f"    {value:<15} score={score:.3f}  (count={count})")

    frame = build_trend_signals_frame(scores=scores, known_feature_values=KNOWN_FEATURE_VALUES)

    if args.existing_path and Path(args.existing_path).exists():
        frame = blend_with_existing(
            scraped=frame,
            existing_path=Path(args.existing_path),
            blend_weight=args.blend_weight,
        )
        print(f"\nBlended with existing: {args.existing_path}")

    validated = validate_trend_signals_frame(frame)
    validated.to_csv(output_path, index=False)
    print(f"\nWrote {len(validated)} rows → {output_path}")

    meta_path = output_path.with_name(output_path.stem + "_meta.json")
    meta_path.write_text(json.dumps({"total_items": len(titles)}, indent=2))
    print(f"Wrote metadata   → {meta_path}")

    scraped_date = datetime.date.today().isoformat()
    items_frame = build_raw_items_frame(raw_items, scraped_date=scraped_date, retailer="uniqlo")
    items_frame.to_csv(items_path, index=False)
    print(f"Wrote {len(items_frame)} raw item rows → {items_path}")


if __name__ == "__main__":
    main()
