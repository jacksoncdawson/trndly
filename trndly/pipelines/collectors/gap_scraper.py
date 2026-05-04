"""
Gap retail scraper for trndly trend signals.

Scrapes Gap's "New Arrivals" and category pages using a real browser
(Playwright) to count how often each color, category, and material attribute
appears across featured product listings. Normalizes those counts to 0–1 and
writes the result as trend_signals.csv.

WHERE EACH ATTRIBUTE COMES FROM
--------------------------------
- category : product title keywords  ("jeans" → pants, "hoodie" → tops, etc.)
- material  : product title keywords  ("linen", "denim", "knit", etc.)
             + category inference when no material keyword is in the title
- color     : color swatch aria-labels  (Gap puts the color name in the
             aria-label of each swatch button)
             + product title keywords as a fallback

BOT PROTECTION
--------------
Gap does not use Akamai, PerimeterX, or Imperva, making it well-suited for
headless / pipeline use. The standard de-fingerprinting init scripts are
applied as a precaution. If you hit consistent empty pages, try --headless
false.

NOTE ON URLS
-------------
Gap's category pages use readable path-based URLs which are generally stable.
If a page returns 0 titles, open the URL in a browser and confirm the path
hasn't changed, then update it here.

SETUP (one-time)
----------------
  pip install playwright
  playwright install chromium

Output:
  By default writes to
    trndly/pipelines/training/synthetic_data/trend_signals_gap.csv
  (so it sits alongside trend_signals_hollister.csv, trend_signals_pacsun.csv,
   etc.). Run combine_trend_signals.py afterwards to merge all retailer
   files into the canonical trend_signals.csv.

Usage:
  python gap_scraper.py
  python gap_scraper.py --output-path path/to/trend_signals_gap.csv
  python gap_scraper.py --existing-path trend_signals_gap.csv --blend-weight 0.5
  python gap_scraper.py --headless false   # visible browser
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from pipelines.collectors.scrape_color_utils import (  # noqa: E402
    dedupe_swatch_labels_preserve_order,
)
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

GAP_PAGES = [
    {"url": "https://www.gap.com/browse/women/new-arrivals?cid=8792",   "label": "women new arrivals"},
    {"url": "https://www.gap.com/browse/men/new-arrivals?cid=11900",     "label": "men new arrivals"},
    {"url": "https://www.gap.com/browse/women/shirts-and-tops?cid=34608","label": "women tops"},
    {"url": "https://www.gap.com/browse/men/shirts?cid=15043",             "label": "men tops"},
    {"url": "https://www.gap.com/browse/women/jeans?cid=5664",          "label": "women bottoms"},
    {"url": "https://www.gap.com/browse/men/jeans?cid=6998",            "label": "men bottoms"},
    {"url": "https://www.gap.com/browse/women/dresses?cid=13658",        "label": "women dresses"},
    {"url": "https://www.gap.com/browse/women/outerwear-and-jackets?cid=5736", "label": "women outerwear"},
    {"url": "https://www.gap.com/browse/men/coats-and-jackets?cid=5168",   "label": "men outerwear"},
]

# --------------------------------------------------------------------------- #
# Selectors                                                                     #
#                                                                               #
# PRODUCT NAME selectors — tried in order, first non-empty match wins.         #
# Gap's product cards use `data-testid="product-card"` wrappers with a         #
# nested name element.                                                         #
# --------------------------------------------------------------------------- #

PRODUCT_NAME_SELECTORS = [
    # Confirmed from live inspection: h3[data-testid="plp_product-name"]
    "[data-testid='plp_product-name']",
    "h3[data-testid='plp_product-name']",
    "[class*='plp_product-card-name']",
    "[class*='fds__card-copy']",
    # Generic fallbacks
    "[class*='product-name']",
    "[class*='ProductName']",
    "article h2",
    "article h3",
    "li[class*='product'] h2",
    "li[class*='product'] h3",
]

# COLOR SWATCH selectors — Gap puts the color name in aria-label on the swatch.
# The plp_ prefix naming convention suggests swatches may follow a similar pattern.
COLOR_SWATCH_SELECTORS = [
    # Confirmed from live inspection:
    # <label data-testid="fds_selector-swatch" for="pdp-buybox-color-swatch--Navy-blue-dots-mergedGroup-0-0">
    # Color name is parsed from the `for` attribute in _extract_swatch_colors below.
    "[data-testid='fds_selector-swatch']",
    "[class*='fds_selector-swatch']",
    # Generic fallbacks
    "[class*='color-swatch'] [aria-label]",
    "[class*='ColorSwatch'] [aria-label]",
    "[class*='Swatch'] button[aria-label]",
    "[class*='swatch'] button[aria-label]",
    "button[aria-label][class*='color']",
    "[data-color]",
    "[data-color-name]",
]

# Selector to wait for before extracting — signals the product grid is ready
PRODUCT_GRID_WAIT_SELECTORS = [
    # Confirmed from live inspection
    "[data-testid='plp_product-name']",
    "[class*='plp_product-card']",
    "[class*='plp_product']",
    # Generic fallbacks
    "[class*='ProductGrid']",
    "[class*='product-grid']",
    "article",
    "li[class*='product']",
]

# Selectors for product card links on listing pages
PRODUCT_CARD_LINK_SELECTORS = [
    "[data-testid='product-card'] a[href]",
    "[class*='plp_product-card'] a[href]",
    "[class*='plp_product'] a[href]",
    "article a[href]",
    "li[class*='product'] a[href]",
]

# Selectors for material/description text on a product detail page
PRODUCT_DETAIL_TEXT_SELECTORS = [
    "[data-testid='product-description-accordion'] *",
    "[data-testid='product-details-accordion'] *",
    "[data-testid='product-details'] *",
    "[data-testid='product-description'] *",
    "[class*='product-description'] *",
    "[class*='product-details'] *",
    "[class*='ProductDetails'] *",
    "[class*='accordion__content'] *",
    "[class*='accordion-content'] *",
]

# --------------------------------------------------------------------------- #
# Attribute keyword maps                                                        #
# --------------------------------------------------------------------------- #

# For COLOR: checked against swatch aria-labels first, then product title.
# Gap uses some brand color names like "True Black", "Pure White",
# "New Classic Navy", "Natural" — listed up-front so they match first.
COLOR_KEYWORDS: list[tuple[str, str]] = [
    # Gap-specific brand names
    ("true black", "black"),
    ("pure white", "white"),
    ("warm white", "white"),
    ("new classic navy", "navy"),
    ("classic navy", "navy"),
    ("natural", "beige"),
    ("light natural", "beige"),
    ("dark natural", "beige"),
    ("washed black", "black"),
    ("vintage navy", "navy"),
    ("dark indigo", "blue"),
    ("light indigo", "blue"),
    ("medium indigo", "blue"),
    ("light wash", "blue"),
    ("medium wash", "blue"),
    ("dark wash", "blue"),
    ("heather grey", "gray"),
    ("heather gray", "gray"),
    ("light heather", "gray"),
    # Shade-qualified generics (color_spectrum from lookup: Dark, Dusty Light, Light, Medium, Bright)
    ("dusty light blue", "blue"),
    ("medium dusty blue", "blue"),
    ("light blue", "blue"),
    ("medium blue", "blue"),
    ("dark blue", "blue"),
    ("bright blue", "blue"),
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
    ("light brown", "brown"),
    ("dark brown", "brown"),
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
    # Standard generics
    ("navy", "navy"),
    ("rinse black", "black"),
    ("black", "black"),
    ("off white", "white"),
    ("white", "white"),
    ("cream", "white"),
    ("ivory", "white"),
    ("burgundy", "red"),
    ("maroon", "red"),
    ("wine", "red"),
    ("red", "red"),
    ("rust", "red"),
    ("brick", "red"),
    ("sage", "green"),
    ("olive", "green"),
    ("khaki green", "green"),
    ("forest", "green"),
    ("moss", "green"),
    ("green", "green"),
    ("sky blue", "blue"),
    ("cobalt", "blue"),
    ("indigo", "blue"),
    ("turquoise", "blue"),
    ("aqua", "blue"),
    ("teal", "blue"),
    ("blue", "blue"),
    ("light beige", "beige"),
    ("dark beige", "beige"),
    ("beige", "beige"),
    ("tan", "beige"),
    ("camel", "beige"),
    ("sand", "beige"),
    ("taupe", "beige"),
    ("ecru", "beige"),
    ("khaki", "beige"),
    ("amber", "brown"),
    ("mocha", "brown"),
    ("chocolate", "brown"),
    ("espresso", "brown"),
    ("cognac", "brown"),
    ("brown", "brown"),
    ("blush", "pink"),
    ("dusty pink", "pink"),
    ("mauve", "pink"),
    ("rose", "pink"),
    ("coral", "pink"),
    ("peach", "pink"),
    ("pink", "pink"),
    ("lavender", "purple"),
    ("lilac", "purple"),
    ("plum", "purple"),
    ("violet", "purple"),
    ("purple", "purple"),
    ("charcoal", "gray"),
    ("light gray", "gray"),
    ("dark gray", "gray"),
    ("slate", "gray"),
    ("stone", "gray"),
    ("grey", "gray"),
    ("gray", "gray"),
]

# For CATEGORY: checked against product title.
CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    # Pants / bottoms (product_type: Trousers, Leggings/Tights, Dungarees, Outdoor trousers)
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
    # Dress / full body (product_type: Dress, Jumpsuit/Playsuit, Bodysuit, Romper)
    ("playsuit", "dress"),
    ("romper", "dress"),
    ("jumpsuit", "dress"),
    ("bodysuit", "dress"),
    ("body suit", "dress"),
    ("dress", "dress"),
    # Outerwear (product_type: Jacket, Coat, Blazer, Cardigan, Outdoor Waistcoat, Tailored Waistcoat)
    ("anorak", "outerwear"),
    ("windbreaker", "outerwear"),
    ("gilet", "outerwear"),
    ("waistcoat", "outerwear"),
    ("jacket", "outerwear"),
    ("coat", "outerwear"),
    ("parka", "outerwear"),
    ("puffer", "outerwear"),
    ("blazer", "outerwear"),
    ("cardigan", "outerwear"),
    # Tops (product_type: T-shirt, Top, Blouse, Vest top, Hoodie, Sweater, Polo shirt, Shirt)
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
    # Shoes (product_type: Boots, Sneakers, Sandals, Heeled sandals, Flat shoe, Pumps, Wedge, Ballerinas, etc.)
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
    # Accessories (product_type: Bag, Belt, Hat/beanie, Scarf, Socks, Earring, Necklace, Watch, etc.)
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

# For MATERIAL: checked against product title.
# Sourced from lookup.csv material list + common retail keyword variants.
# Multi-word phrases must appear before their single-word substrings.
MATERIAL_KEYWORDS: list[tuple[str, str]] = [
    # Denim
    ("denim", "denim"),
    ("jean", "denim"),
    ("jort", "denim"),
    ("cutoff", "denim"),
    # Linen
    ("linen-blend", "linen"),
    ("linen", "linen"),
    # Silk / silk-like (product_type lookup: satin, chiffon, crepe, georgette)
    ("chiffon", "silk"),
    ("crepe", "silk"),
    ("georgette", "silk"),
    ("silk", "silk"),
    ("satin", "silk"),
    # Wool / wool-like (lookup: cashmere, fleece, faux fur, shearling, sherpa)
    ("cashmere", "wool"),
    ("shearling", "wool"),
    ("sherpa", "wool"),
    ("faux fur", "wool"),
    ("wool", "wool"),
    ("fleece", "wool"),
    # Leather / leather-like (lookup: imitation leather, imitation suede, suede)
    ("imitation leather", "leather"),
    ("imitation suede", "leather"),
    ("faux leather", "leather"),
    ("vegan leather", "leather"),
    ("suede", "leather"),
    ("leather", "leather"),
    # Knit / knit-like (lookup: jersey, velvet, velour)
    ("rib-knit", "knit"),
    ("ribbed", "knit"),
    ("jersey", "knit"),
    ("velvet", "knit"),
    ("velour", "knit"),
    ("knit", "knit"),
    ("crochet", "knit"),
    ("waffle", "knit"),
    # Polyester / synthetics (lookup: nylon, acrylic, tulle, mesh, spandex, elastane)
    ("nylon", "polyester"),
    ("acrylic", "polyester"),
    ("tulle", "polyester"),
    ("mesh", "polyester"),
    ("spandex", "polyester"),
    ("elastane", "polyester"),
    ("polyester", "polyester"),
    ("recycled", "polyester"),
    # Cotton / cellulosics (lookup: poplin, twill, terry, corduroy, canvas, tencel, lyocell, modal, viscose, rayon)
    ("poplin", "cotton"),
    ("twill", "cotton"),
    ("terry", "cotton"),
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

# For GRAPHICAL APPEARANCE: checked against product title.
# Maps to lookup.csv graphical_appearance names.
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

# For PRODUCT TYPE: maps title keywords to lookup.csv product_type names.
# More granular than CATEGORY_KEYWORDS — one entry per lookup type.
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
    ("flip flop", "Flip flop"),
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

# --------------------------------------------------------------------------- #
# Lookup table ID mappings (from trndly/EDA/data/lookup.csv)                  #
# --------------------------------------------------------------------------- #

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

# product_group_id — derived from product_type name
# lookup: 1=Garment Upper body, 2=Garment Lower body, 3=Garment Full body,
#         4=Swimwear, 5=Underwear, 6=Accessories, 7=Shoes, 8=Socks & Tights, 9=Nightwear
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

# color_spectrum_id — derived from shade keywords in the color label
# lookup: 0=Unknown, 1=Dark, 2=Dusty Light, 3=Light, 4=Medium Dusty, 5=Medium, 6=Bright
COLOR_SPECTRUM_KEYWORDS: list[tuple[str, int]] = [
    ("medium dusty", 4),
    ("dusty", 2),
    ("heather", 2),
    ("muted", 2),
    ("washed", 2),
    ("faded", 2),
    ("light", 3),
    ("pale", 3),
    ("soft", 3),
    ("pastel", 3),
    ("cream", 3),
    ("bright", 6),
    ("vivid", 6),
    ("neon", 6),
    ("electric", 6),
    ("dark", 1),
    ("deep", 1),
    ("rich", 1),
    ("medium", 5),
    ("mid", 5),
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
    """Return a lookup.csv graphical_appearance name from the product title.
    Defaults to 'Solid' when no pattern keyword is found."""
    result = _first_match(text, GRAPHICAL_APPEARANCE_KEYWORDS)
    return result if result else GRAPHICAL_APPEARANCE_DEFAULT


def extract_product_type(text: str) -> str | None:
    """Return a lookup.csv product_type name from the product title."""
    return _first_match(text, PRODUCT_TYPE_KEYWORDS)


def extract_color_spectrum_id(color_label: str) -> int:
    """
    Derive color_spectrum_id from shade keywords in the color label.
    e.g. 'Light heather grey' → 3 (Light), 'Dark indigo wash' → 1 (Dark).
    Returns 0 (Unknown) when no shade keyword matches.
    """
    lowered = color_label.lower()
    for keyword, spectrum_id in COLOR_SPECTRUM_KEYWORDS:
        if keyword in lowered:
            return spectrum_id
    return 0


def extract_product_group_id(product_type: str | None) -> int:
    """
    Derive product_group_id from the product_type name.
    e.g. 'T-shirt' → 1 (Garment Upper body), 'Trousers' → 2 (Garment Lower body).
    Returns 0 (Unknown) when product_type is None or not mapped.
    """
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
    Extract color names from Gap swatch label elements.

    Gap uses <label data-testid="fds_selector-swatch"> elements where the
    color name is embedded in the `for` attribute, e.g.:
      for="pdp-buybox-color-swatch--Navy-blue-dots-mergedGroup-0-0"
    → color name: "Navy blue dots"

    Falls back to aria-label / data-color for any generic swatch elements.
    """
    import re

    for selector in COLOR_SWATCH_SELECTORS:
        try:
            elements = page.query_selector_all(selector)
            if not elements:
                continue
            labels: list[str] = []
            for el in elements:
                label = (
                    el.get_attribute("aria-label")
                    or el.get_attribute("data-color")
                    or el.get_attribute("data-color-name")
                    or ""
                )
                # Gap-specific: color name is encoded in the `for` attribute.
                # Pattern: pdp-buybox-color-swatch--{Color-name}-mergedGroup-N-N
                if not label:
                    for_attr = el.get_attribute("for") or ""
                    match = re.search(
                        r"pdp-buybox-color-swatch--(.+?)-mergedGroup", for_attr
                    )
                    if match:
                        label = match.group(1).replace("-", " ")
                if label.strip():
                    labels.append(label.strip())
            if labels:
                return labels
        except Exception:
            continue
    return []


def _extract_product_urls(page: "Page", base_url: str = "https://www.gap.com") -> list[str]:
    """
    Collect unique product detail page URLs from a listing page.

    Gap product cards are wrapped in elements with data-testid='product-card'
    and contain an <a href="/browse/..."> link to the detail page.
    """
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
                # Deduplicate — multiple <a> tags may exist per card
                if href not in seen and "gap.com" in href:
                    seen.add(href)
                    urls.append(href)
            if urls:
                return urls
        except Exception:
            continue
    return urls


def _scrape_product_detail(page: "Page", url: str) -> dict | None:
    """
    Visit one Gap product detail page and return a dict with:
        title       - product name string
        colors      - list of color name strings (from swatches)
        detail_text - raw text of the description/details section (for material)

    Returns None on error.
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(1.0)

        # Product title
        title = ""
        for sel in [
            "[data-testid='product-name']",
            "h1[data-testid='pdp-product-name']",
            "h1[class*='product-name']",
            "h1",
        ]:
            try:
                el = page.query_selector(sel)
                if el:
                    title = el.inner_text().strip()
                    if title:
                        break
            except Exception:
                continue

        # Color swatches (same helper as listing page — PDP uses same markup)
        colors = _extract_swatch_colors(page)

        # Description / details text (for material extraction)
        # Note: accordions are not clicked — material is extracted from
        # the product title keywords instead, which is fast and reliable.
        detail_text = ""
        for sel in PRODUCT_DETAIL_TEXT_SELECTORS:
            try:
                elements = page.query_selector_all(sel)
                texts = [el.inner_text().strip() for el in elements if el.inner_text().strip()]
                if texts:
                    detail_text = " ".join(texts)
                    break
            except Exception:
                continue

        return {"title": title, "colors": colors, "detail_text": detail_text}

    except Exception as exc:
        print(f"      ERROR on {url}: {exc}")
        return None


# --------------------------------------------------------------------------- #
# Main scraping loop                                                            #
# --------------------------------------------------------------------------- #

def scrape_gap(
    sleep_between_pages: float = 3.0,
    headless: bool = True,
) -> tuple[list[str], list[str], list[dict]]:
    """
    Scrape all Gap pages.

    Returns:
        all_titles       - flat list of product title strings (for trend_signals)
        all_swatch_colors - flat list of swatch color labels (for trend_signals)
        raw_items        - list of dicts, one per product title, with keys:
                           title, gender (women/men/unisex from URL label)
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright is not installed.")
        print("  Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    all_titles: list[str] = []
    all_swatch_colors: list[str] = []
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
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        )
        # Mask the webdriver flag that common bot detectors check
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "window.chrome = {runtime: {}};"
        )

        page = context.new_page()

        for page_info in GAP_PAGES:
            url = page_info["url"]
            label = page_info["label"]
            print(f"  [{label}] → {url}")

            # Derive gender from the page label
            gender = "women" if "women" in label else "men" if "men" in label else "unisex"

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                # Wait for the product grid to actually render
                grid_found = _wait_for_products(page, timeout_ms=15_000)
                if not grid_found:
                    page_title = page.title()
                    print(f"    WARNING: product grid not found (page title: '{page_title}')")

                time.sleep(1.5)
                _scroll_to_bottom(page)

                titles = _extract_product_names(page)
                swatches = dedupe_swatch_labels_preserve_order(_extract_swatch_colors(page))

                print(f"    {len(titles)} product titles, {len(swatches)} swatch colors")
                all_titles.extend(titles)
                all_swatch_colors.extend(swatches)

                # Attach page-level swatch labels to each title so
                # build_raw_items_frame can expand to one row per color.
                for title in titles:
                    raw_items.append({"title": title, "gender": gender, "page_swatches": swatches})

            except Exception as exc:
                print(f"    ERROR: {exc}")

            time.sleep(sleep_between_pages)

        browser.close()

    return all_titles, all_swatch_colors, raw_items


def scrape_gap_detailed(
    sleep_between_pages: float = 3.0,
    sleep_between_products: float = 2.0,
    headless: bool = True,
    max_products_per_page: int = 40,
) -> list[dict]:
    """
    Two-step scraper: listing page → product URLs → visit each product page.

    For each product, reads the actual color swatches and description from the
    detail page, then writes one row per color variant. This is more accurate
    than title-keyword extraction because:
      - Colors come from real swatch labels, not guessed from title words
      - Material comes from the product description text
      - Graphical appearance is extracted from the description/title on the PDP

    Returns a list of raw item dicts:
        title, gender, color, material, graphical_appearance,
        product_type, detail_text (for debugging)
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

        for page_info in GAP_PAGES:
            url   = page_info["url"]
            label = page_info["label"]
            gender = "women" if "women" in label else "men" if "men" in label else "unisex"
            print(f"\n  [{label}] → {url}")

            # Step 1: collect product URLs from listing page
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

            # Step 2: visit each product detail page
            for i, prod_url in enumerate(product_urls, 1):
                detail = _scrape_product_detail(page, prod_url)
                if not detail:
                    continue

                title       = detail["title"]
                colors      = detail["colors"]  # list of color name strings from swatches
                detail_text = detail["detail_text"]

                # Use detail_text for material (richer than title alone)
                combined_text = f"{title} {detail_text}"
                category      = extract_category(title)
                material      = extract_material(combined_text, inferred_category=category)
                product_type  = extract_product_type(title)
                # Base graphical appearance from title + description
                base_graphical = extract_graphical_appearance(combined_text)

                colors = dedupe_swatch_labels_preserve_order(colors)
                if colors:
                    # One row per color variant
                    for color_label in colors:
                        color = extract_color(color_label) or extract_color(title)
                        # Color label may reveal pattern (e.g. "Red & light blue stripe")
                        graphical = extract_graphical_appearance(color_label)
                        if graphical == GRAPHICAL_APPEARANCE_DEFAULT:
                            graphical = base_graphical
                        raw_items.append({
                            "title":                    title,
                            "gender":                   gender,
                            "color_raw":                color_label,
                            "color":                    color or "unknown",
                            "product_type_raw":         product_type or "unknown",
                            "material_raw":             material or "unknown",
                            "graphical_appearance_raw": graphical,
                        })
                else:
                    # No swatches found — still write the product with title-based color
                    color = extract_color(title)
                    raw_items.append({
                        "title":                    title,
                        "gender":                   gender,
                        "color_raw":                color or "unknown",
                        "color":                    color or "unknown",
                        "product_type_raw":         product_type or "unknown",
                        "material_raw":             material or "unknown",
                        "graphical_appearance_raw": graphical,
                    })

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

    # Colors from swatches — each swatch label is one occurrence of that color
    for swatch_label in swatch_colors:
        color = extract_color(swatch_label)
        if color:
            counts["color"][color] = counts["color"].get(color, 0) + 1

    # Category, material, and fallback color from product titles
    for title in titles:
        category = extract_category(title)
        if category:
            counts["category"][category] = counts["category"].get(category, 0) + 1

        material = extract_material(title, inferred_category=category)
        if material:
            counts["material"][material] = counts["material"].get(material, 0) + 1

        # Color from title only if swatch extraction was empty
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
    retailer: str = "gap",
) -> pd.DataFrame:
    """
    Build a one-row-per-item-color DataFrame matching the monthly cube schema.

    Accepts raw_items from either scrape_gap() (title+gender+page_swatches) or
    scrape_gap_detailed() (title+gender+color+material+graphical already extracted).

    For listing-page items (fast mode), each product is expanded to one row per
    unique color seen on its page — matching H&M's one-row-per-article-color schema.
    If a swatch label maps to no known color keyword it is still included with
    color_master_id=0 so we preserve the raw label for debugging.

    Each row includes raw text attributes and all 7 lookup-table IDs.
    """
    rows = []
    for item in raw_items:
        title        = item["title"]
        gender       = item["gender"]
        product_type = item.get("product_type_raw") if item.get("product_type_raw", "unknown") != "unknown" \
                       else extract_product_type(title)
        material     = item.get("material_raw") if item.get("material_raw", "unknown") != "unknown" \
                       else extract_material(title, inferred_category=extract_category(title))
        graphical    = item.get("graphical_appearance_raw") or extract_graphical_appearance(title)

        # Determine the list of color labels to expand over:
        # 1. Detailed scrape already has a single color → one row.
        # 2. Listing-page scrape has page_swatches → one row per unique swatch.
        # 3. Fallback: try to extract color from the title → one row (possibly unknown).
        if item.get("color"):
            color_labels = [item["color_raw"] if item.get("color_raw") else item["color"]]
        elif item.get("page_swatches"):
            color_labels = dedupe_swatch_labels_preserve_order(item["page_swatches"])
        else:
            title_color = extract_color(title)
            color_labels = [title_color] if title_color else ["unknown"]

        for color_raw_lbl in color_labels:
            color = extract_color(color_raw_lbl) if color_raw_lbl != "unknown" else None
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
                "gender_id":               GENDER_TO_ID.get(gender, 2),
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
    # Each scraper writes to its own per-retailer file so multiple retailers
    # can be run independently, updated on their own schedule, and then
    # combined later by combine_trend_signals.py.
    _synth = Path(__file__).resolve().parents[1] / "training" / "synthetic_data"
    default_output = _synth / "trend_signals_gap.csv"
    default_items  = _synth / "items_gap.csv"
    parser = argparse.ArgumentParser(
        description="Scrape Gap new arrivals and write trend_signals_gap.csv + items_gap.csv."
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
        help=(
            "Visit each product detail page to get real colors, material, and "
            "graphical appearance. Slower (~30 min) but far more accurate than "
            "title-keyword extraction. Writes items_gap.csv with one row per "
            "product-color variant."
        ),
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
        f"Gap retail scraper\n"
        f"  pages: {len(GAP_PAGES)}  headless: {args.headless}\n"
        f"  detailed mode: {args.detailed}"
        + (f"  (max {args.max_products} products/page)" if args.detailed else "") + "\n"
        f"  output: {output_path}\n"
        f"  items:  {items_path}"
    )

    titles, swatch_colors, raw_items = scrape_gap(
        sleep_between_pages=args.sleep,
        headless=args.headless,
    )

    # In detailed mode, re-scrape product pages for accurate per-item data.
    # NOTE: this is slow (~30 min for 50 products/page). Only use for
    # occasional deep scrapes; the default listing-page approach is fast
    # and good enough for monthly trend signals.
    if args.detailed:
        print("\nRunning detailed per-product scrape for items CSV...")
        raw_items = scrape_gap_detailed(
            sleep_between_pages=args.sleep,
            sleep_between_products=2.0,
            headless=args.headless,
            max_products_per_page=args.max_products,
        )
    # Default: use listing-page titles directly (one row per title, fast)
    # Color comes from title keywords; gender from URL label.

    print(f"\nTotal collected: {len(titles)} product titles, {len(swatch_colors)} swatch colors")

    if not titles and not swatch_colors:
        print(
            "\nWARNING: Nothing was scraped. Gap's bot protection may be\n"
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

    # Write raw items CSV — one row per product title with lookup-table IDs.
    # Used by the live aggregation step to build monthly_fingerprint / monthly_univariate
    # cubes (same schema as notebook 1 outputs, with source='live').
    scraped_date = datetime.date.today().isoformat()
    items_frame = build_raw_items_frame(raw_items, scraped_date=scraped_date, retailer="gap")
    items_frame.to_csv(items_path, index=False)
    print(f"Wrote {len(items_frame)} raw item rows → {items_path}")


if __name__ == "__main__":
    main()
