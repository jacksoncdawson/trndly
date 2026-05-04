"""
American Eagle retail scraper for trndly trend signals.

Scrapes American Eagle's "New Arrivals" and category pages using a real
browser (Playwright) to count how often each color, category, and material
attribute appears across featured product listings. Normalizes those counts
to 0–1 and writes the result as trend_signals_american_eagle.csv.

WHERE EACH ATTRIBUTE COMES FROM
--------------------------------
- category : product title keywords  ("jeans" → pants, "hoodie" → tops, etc.)
- material  : product title keywords  ("linen", "denim", "knit", etc.)
             + category inference when no material keyword is in the title
- color     : color swatch aria-labels  (American Eagle puts the color name
             in the aria-label / data-color of each swatch button, NOT in
             the product title text)
             + product title keywords as a fallback

ZERO TITLES ON SUBSEQUENT PAGES
--------------------------------
ae.com is a Next.js SPA. After navigating to a new URL the product grid
re-renders asynchronously, so the scraper calls page.wait_for_selector() to
block until at least one product tile is visible before extracting.

BOT PROTECTION
--------------
ae.com does not currently serve the kind of interstitial bot challenge that
Hollister does, so this scraper skips the Akamai-style waiter. Some
de-fingerprinting init scripts are still applied to reduce the chance of
headless Chrome being served an empty / simplified page. If you hit
consistent empty pages, try --headless false to open a visible browser.

SETUP (one-time)
----------------
  pip install playwright
  playwright install chromium

Usage:
  python american_eagle_scraper.py
  python american_eagle_scraper.py --output-path path/to/trend_signals_american_eagle.csv
  python american_eagle_scraper.py --existing-path trend_signals_american_eagle.csv --blend-weight 0.5
  python american_eagle_scraper.py --headless false   # visible browser
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
#                                                                               #
# ae.com occasionally renames category paths. The plain paths below redirect   #
# correctly in a browser; if a page returns 0 titles, update the URL here.     #
# --------------------------------------------------------------------------- #

AMERICAN_EAGLE_PAGES = [
    {"url": "https://www.ae.com/us/en/c/women/new-arrivals/brg_dyn_hqya6u718b",    "label": "women new arrivals"},
    {"url": "https://www.ae.com/us/en/c/men/new-arrivals/brg_dyn_fiqvft6w17",      "label": "men new arrivals"},
    {"url": "https://www.ae.com/us/en/c/women/tops/cat10049",                      "label": "women tops"},
    {"url": "https://www.ae.com/us/en/c/men/tops/cat10025",                        "label": "men tops"},
    {"url": "https://www.ae.com/us/en/c/women/bottoms/cat10051",                   "label": "women bottoms"},
    {"url": "https://www.ae.com/us/en/c/men/bottoms/cat10027",                     "label": "men bottoms"},
    {"url": "https://www.ae.com/us/en/c/women/dresses/cat1320034",                 "label": "women dresses"},
    {"url": "https://www.ae.com/us/en/c/women/tops/jackets/cat4260032",            "label": "women outerwear"},
    {"url": "https://www.ae.com/us/en/c/men/tops/jackets/cat380145",               "label": "men outerwear"},
]

# --------------------------------------------------------------------------- #
# Selectors                                                                     #
#                                                                               #
# PRODUCT NAME selectors — tried in order, first non-empty match wins.         #
# AE's product tiles expose `data-testid="product-tile"` with a nested title   #
# element; we fall back to more generic selectors if that changes.             #
# --------------------------------------------------------------------------- #

PRODUCT_NAME_SELECTORS = [
    # Confirmed from live inspection:
    # <h3 class="product-name ..." data-product-name="AE Double Take Tube Top" data-testid="name">
    "[data-testid='name']",
    "h3[data-testid='name']",
    "[class*='product-name'][data-testid='name']",
    "[data-product-name]",
    # Generic fallbacks
    "[class*='product-name']",
    "[class*='ProductName']",
    "[class*='ProductCard'] h3",
    "[class*='product-card'] h3",
    "article h2",
    "article h3",
    "li[class*='product'] h2",
    "li[class*='product'] h3",
]

# COLOR SWATCH selectors — AE shows a small row of color swatches on each
# tile; the color name is exposed as aria-label or data-color on the swatch.
COLOR_SWATCH_SELECTORS = [
    # Confirmed from live inspection:
    # <img class="_swatch-img_..." alt="Gatsby Green" title="Gatsby Green">
    # Color name lives in the `alt` (and `title`) attribute of the swatch image.
    "img[class*='swatch-img']",
    "img[class*='swatch_img']",
    "[data-test-color-swatch] img",
    "[class*='_swatch_'] img",
    "[class*='swatch'] img[alt]",
    # Button wrappers — AE sometimes puts the color name on the parent button
    "button[class*='swatch'][aria-label]",
    "button[class*='color'][aria-label]",
    "[class*='swatch-container'] button[aria-label]",
    "[data-qa-color-swatch]",
    "[data-qa-color-swatch] img",
    # Generic fallbacks
    "[class*='ColorSwatch'] [aria-label]",
    "[class*='color-swatch'] [aria-label]",
    "[class*='Swatch'] button[aria-label]",
    "button[aria-label][class*='color']",
    "[data-color]",
    "[data-color-name]",
]

# Selector to wait for before extracting — signals the product grid is ready
PRODUCT_GRID_WAIT_SELECTORS = [
    # Confirmed — wait for the product name element to appear
    "[data-testid='name']",
    "[class*='product-name']",
    # Generic fallbacks
    "[class*='ProductGrid']",
    "[class*='product-grid']",
    "[class*='ProductTile']",
    "article",
    "li[class*='product']",
]

# --------------------------------------------------------------------------- #
# Attribute keyword maps                                                        #
# --------------------------------------------------------------------------- #

# For COLOR: checked against swatch aria-labels first, then product title.
# AE uses brand color names like "Twilight", "Stormy", "Cognac" — these are
# listed up-front so they match before generic fallbacks.
# Canonical strings align with trndly/EDA/data/lookup.csv color_master (incl.
# yellow, orange, metal) for color_master_id + trend_signals.
COLOR_KEYWORDS: list[tuple[str, str]] = [
    # AE brand-specific color names (checked before generics)
    ("twilight", "blue"),
    ("stormy", "gray"),
    ("onyx", "black"),
    ("wisteria", "purple"),
    ("currant", "red"),
    ("marigold", "beige"),
    ("dusty jade", "green"),
    ("dark rinse", "blue"),
    ("medium rinse", "blue"),
    ("light rinse", "blue"),
    ("rinse", "blue"),
    ("dark wash", "blue"),
    ("medium wash", "blue"),
    ("light wash", "blue"),
    ("acid wash", "blue"),
    ("faded black", "black"),
    ("true black", "black"),
    ("vintage black", "black"),
    ("washed black", "black"),
    ("rinse black", "black"),
    ("midnight", "navy"),
    ("chambray", "blue"),
    ("heritage", "beige"),
    ("clay", "beige"),
    ("wheat", "beige"),
    ("parchment", "white"),
    ("birch", "white"),
    ("bone", "white"),
    ("eggshell", "white"),
    ("chalk", "white"),
    ("oatmeal", "beige"),
    ("cognac", "brown"),
    ("slate", "gray"),
    ("orange flare", "orange"),
    ("burnt orange", "orange"),
    ("dark orange", "orange"),
    ("orange", "orange"),
    ("tangerine", "orange"),
    ("mandarin", "orange"),
    ("citrus", "orange"),
    ("pumpkin", "orange"),
    ("spice", "brown"),
    ("magenta", "pink"),
    ("fuchsia", "pink"),
    ("salmon", "pink"),
    ("watermelon", "pink"),
    ("yellow", "yellow"),
    ("lemon", "yellow"),
    ("canary", "yellow"),
    ("buttercup", "yellow"),
    ("gold", "yellow"),
    ("bronze", "brown"),
    ("silver", "metal"),
    ("metallic", "metal"),
    ("chrome", "metal"),
    ("turquoise", "blue"),
    ("aqua", "blue"),
    ("mint", "green"),
    ("seafoam", "green"),
    ("chartreuse", "green"),
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
    ("mustard", "beige"),
    ("butter", "beige"),
    ("golden", "beige"),
    ("amber", "brown"),
    # Standard generics
    ("navy", "navy"),
    ("black", "black"),
    ("off white", "white"),
    ("cloud white", "white"),
    ("white", "white"),
    ("cream", "white"),
    ("ivory", "white"),
    ("burgundy", "red"),
    ("maroon", "red"),
    ("wine", "red"),
    ("coral", "pink"),
    ("rust", "red"),
    ("terracotta", "red"),
    ("brick", "red"),
    ("red", "red"),
    ("sage", "green"),
    ("olive", "green"),
    ("khaki green", "green"),
    ("forest", "green"),
    ("moss", "green"),
    ("green", "green"),
    ("sky blue", "blue"),
    ("cobalt", "blue"),
    ("indigo", "blue"),
    ("teal", "blue"),
    ("blue", "blue"),
    ("light beige", "beige"),
    ("dark beige", "beige"),
    ("beige", "beige"),
    ("tan", "beige"),
    ("camel", "beige"),
    ("sand", "beige"),
    ("taupe", "beige"),
    ("khaki", "beige"),
    ("ecru", "beige"),
    ("mocha", "brown"),
    ("chocolate", "brown"),
    ("espresso", "brown"),
    ("brown", "brown"),
    ("blush", "pink"),
    ("dusty pink", "pink"),
    ("mauve", "pink"),
    ("rose", "pink"),
    ("peach", "pink"),
    ("pink", "pink"),
    ("lavender", "purple"),
    ("lilac", "purple"),
    ("plum", "purple"),
    ("violet", "purple"),
    ("purple", "purple"),
    ("charcoal", "gray"),
    ("heather gray", "gray"),
    ("heather grey", "gray"),
    ("light gray", "gray"),
    ("dark gray", "gray"),
    ("stone", "gray"),
    ("grey", "gray"),
    ("gray", "gray"),
]

# For CATEGORY: checked against product title.
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
    ("trekker short", "shorts"),
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

# For MATERIAL: checked against product title.
# Sourced from lookup.csv material list + common retail keyword variants.
MATERIAL_KEYWORDS: list[tuple[str, str]] = [
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
    ("corduroy", "cotton"),
    ("canvas", "cotton"),
    ("tencel", "cotton"),
    ("lyocell", "cotton"),
    ("modal", "cotton"),
    ("viscose", "cotton"),
    ("rayon", "cotton"),
    ("cotton", "cotton"),
]

# When no material keyword is in the title, infer from category
CATEGORY_TO_MATERIAL_DEFAULT: dict[str, str] = {
    "pants": "denim",
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
    ("trekker short", "Shorts"),
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
    Extract color names from AE swatch image elements.

    AE uses <img class="_swatch-img_..." alt="Gatsby Green" title="Gatsby Green">
    inside each swatch button. The color name can be in the `alt` / `title`
    attribute of the image, or in the `aria-label` of the wrapping button.
    We try all variants so that selector drift on one path still finds the other.
    """
    all_labels: list[str] = []
    seen: set[str] = set()

    for selector in COLOR_SWATCH_SELECTORS:
        try:
            elements = page.query_selector_all(selector)
            if not elements:
                continue
            for el in elements:
                label = (
                    el.get_attribute("alt")
                    or el.get_attribute("aria-label")
                    or el.get_attribute("title")
                    or el.get_attribute("data-color")
                    or el.get_attribute("data-color-name")
                    or ""
                ).strip()
                if (
                    label
                    and _ae_looks_like_retail_color_label(label)
                    and label not in seen
                ):
                    seen.add(label)
                    all_labels.append(label)
        except Exception:
            continue

    return all_labels


# Selectors for product card links on AE listing pages
PRODUCT_CARD_LINK_SELECTORS = [
    "a[href*='/p/']",
    "[class*='product-card'] a[href]",
    "[class*='ProductCard'] a[href]",
    "[class*='product-tile'] a[href]",
    "article a[href]",
    "li[class*='product'] a[href]",
]


# --------------------------------------------------------------------------- #
# API / page-data color extraction                                              #
# --------------------------------------------------------------------------- #

def _ae_looks_like_retail_color_label(s: str) -> bool:
    """
    Reject footer/nav, pricing tiers, and promos that share JSON keys like
    colorName-adjacent fields or generic 'label' text on ae.com.
    """
    t = (s or "").strip()
    if not t or len(t) > 72:
        return False
    low = t.lower()
    if "$" in t or re.search(r"\b\d{1,2}%?\s*off\b", low):
        return False
    if "real rewards" in low or "gift card" in low or "credit card" in low:
        return False
    if "level 1" in low or "level 2" in low or "level 3" in low:
        return False
    if low in (
        "bras", "swim", "shoes", "shop", "sale", "men", "women", "kids", "aerie",
        "third party shoes", "jeans", "clearance",
    ):
        return False
    if "third party" in low:
        return False
    return True


def _dedupe_ae_colors_preserve_order(labels: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lab in labels:
        if not _ae_looks_like_retail_color_label(lab):
            continue
        k = lab.strip().lower()
        if k not in seen:
            seen.add(k)
            out.append(lab.strip())
    return out


# Field names (lowercased) whose string values are likely color names.
# Avoid generic keys like "label" / "displayName" — they match nav and pricing.
_COLOR_FIELD_NAMES: frozenset[str] = frozenset([
    "colorname", "color_name", "colordisplayname", "color_display_name",
    "swatchname", "swatch_name", "swatchlabel", "swatch_label",
    "colorlabel", "color_label", "colortitle", "color_title",
])

# Parent-key names that contain arrays of color/swatch objects.
_COLOR_ARRAY_FIELD_NAMES: frozenset[str] = frozenset([
    "colors", "colour", "colours", "swatches", "coloroptions",
    "color_options", "colorfacets", "color_facets", "colorways",
])

# Keys on objects *inside* color/swatch arrays (ae.com often uses "name", not "colorName").
_SWATCH_OBJECT_LABEL_KEYS: tuple[str, ...] = (
    "colorname",
    "color_name",
    "colordisplayname",
    "color_display_name",
    "swatchname",
    "swatch_name",
    "swatchlabel",
    "swatch_label",
    "merchantcolorname",
    "displayname",
    "title",
    "name",
    "label",
)


def _ae_color_label_from_swatch_dict(d: dict) -> str | None:
    """Pick a retail color string from a single swatch / color-variant object."""
    dl = {str(k).lower(): v for k, v in d.items()}
    for sk in _SWATCH_OBJECT_LABEL_KEYS:
        v = dl.get(sk)
        if isinstance(v, str):
            s = v.strip()
            if s and _ae_looks_like_retail_color_label(s):
                return s
    return None


def _walk_json_for_colors(data: object, found: list[str], depth: int = 0) -> None:
    """
    Recursively walk a JSON structure looking for color name strings.

    Targets fields whose keys suggest they hold a color name (e.g.
    "colorName", "swatchName", "label" inside a swatches array).
    Avoids going deeper than 10 levels to stay fast on large payloads.
    """
    if depth > 10:
        return
    if isinstance(data, dict):
        for key, value in data.items():
            key_lower = key.lower()
            if key_lower in _COLOR_FIELD_NAMES and isinstance(value, str) and value.strip():
                v = value.strip()
                if _ae_looks_like_retail_color_label(v):
                    found.append(v)
            elif key_lower in _COLOR_ARRAY_FIELD_NAMES and isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        v = item.strip()
                        if _ae_looks_like_retail_color_label(v):
                            found.append(v)
                    elif isinstance(item, dict):
                        lbl = _ae_color_label_from_swatch_dict(item)
                        if lbl:
                            found.append(lbl)
                        _walk_json_for_colors(item, found, depth + 1)
            elif isinstance(value, (dict, list)):
                _walk_json_for_colors(value, found, depth + 1)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                _walk_json_for_colors(item, found, depth + 1)


def _extract_colors_from_next_data(page: "Page") -> list[str]:
    """
    Try to extract color names from the Next.js __NEXT_DATA__ JSON block
    embedded in the page HTML. This is server-side rendered and available
    immediately after domcontentloaded, before any XHR fires.
    """
    try:
        script_text: str = page.evaluate(
            "() => { const el = document.getElementById('__NEXT_DATA__'); "
            "return el ? el.textContent : ''; }"
        )
        if not script_text:
            return []
        data = json.loads(script_text)
        colors: list[str] = []
        _walk_json_for_colors(data, colors)
        return colors
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Main scraping loop                                                            #
# --------------------------------------------------------------------------- #

def scrape_american_eagle(
    sleep_between_pages: float = 3.0,
    headless: bool = True,
) -> tuple[list[str], list[str], list[dict]]:
    """
    Scrape all American Eagle pages.
    Returns (product_titles, color_label_occurrences, raw_items).

    ``color_label_occurrences`` is a flat list (same label may repeat once per
    page / response) for ``count_attribute_frequencies`` — not globally deduped.

    Colors come from three sources tried in priority order:
      1. Network response interception — AE's Next.js app fires XHR/fetch
         calls to load products; we walk those JSON payloads for color fields.
      2. __NEXT_DATA__ extraction — the server-rendered JSON block embedded
         in the HTML often contains the first page of products with colors.
      3. DOM swatch elements — kept as a last-resort fallback in case the
         above two find nothing on a given page.
    raw_items is a list of dicts with 'title' and 'gender' for each product.
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
    aggregated_page_colors: list[str] = []
    raw_items: list[dict] = []

    def _handle_response(response: object) -> None:
        """Intercept AE XHR/fetch responses and walk JSON for color names."""
        try:
            url: str = response.url  # type: ignore[attr-defined]
            if "ae.com" not in url:
                return
            content_type: str = response.headers.get(  # type: ignore[attr-defined]
                "content-type", ""
            )
            if "json" not in content_type:
                return
            if response.status != 200:  # type: ignore[attr-defined]
                return
            data = response.json()  # type: ignore[attr-defined]
            _walk_json_for_colors(data, api_colors)
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

        for page_info in AMERICAN_EAGLE_PAGES:
            url = page_info["url"]
            label = page_info["label"]
            print(f"  [{label}] → {url}")
            api_colors.clear()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                # Wait for the product grid to actually render (Next.js hydration)
                grid_found = _wait_for_products(page, timeout_ms=15_000)
                if not grid_found:
                    page_title = page.title()
                    print(f"    WARNING: product grid not found (page title: '{page_title}')")

                # Pull colors from __NEXT_DATA__ (server-rendered, no XHR needed)
                next_data_colors = _extract_colors_from_next_data(page)
                api_colors.extend(next_data_colors)

                time.sleep(1.5)
                _scroll_to_bottom(page)

                titles = _extract_product_names(page)
                swatches = dedupe_swatch_labels_preserve_order(_extract_swatch_colors(page))

                print(
                    f"    {len(titles)} product titles, "
                    f"{len(swatches)} DOM swatches, "
                    f"{len(next_data_colors)} __NEXT_DATA__ colors, "
                    f"{len(api_colors)} API colors so far"
                )
                all_titles.extend(titles)
                all_swatch_colors.extend(swatches)

                gender = "women" if label.startswith("women") else "men" if label.startswith("men") else "unisex"
                merged = _dedupe_ae_colors_preserve_order(api_colors)
                page_colors = dedupe_swatch_labels_preserve_order(merged if merged else swatches)
                aggregated_page_colors.extend(page_colors)
                for t in titles:
                    raw_items.append({"title": t, "gender": gender, "page_swatches": page_colors})

            except Exception as exc:
                print(f"    ERROR: {exc}")

            time.sleep(sleep_between_pages)

        browser.close()

    # Keep repeats across pages for trend counting (do not globally dedupe here).
    color_occurrences = (
        aggregated_page_colors if aggregated_page_colors else list(all_swatch_colors)
    )
    unique_labels = _dedupe_ae_colors_preserve_order(color_occurrences)
    print(
        f"  Listing color labels for trends: {len(color_occurrences)} occurrences, "
        f"{len(unique_labels)} unique raw strings"
    )

    return all_titles, color_occurrences, raw_items


def _extract_product_urls(page: "Page", base_url: str = "https://www.ae.com") -> list[str]:
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
                if href not in seen and "ae.com" in href:
                    seen.add(href)
                    urls.append(href)
            if urls:
                return urls
        except Exception:
            continue
    return urls


def scrape_american_eagle_detailed(
    sleep_between_pages: float = 3.0,
    sleep_between_products: float = 2.0,
    headless: bool = True,
    max_products_per_page: int = 50,
) -> list[dict]:
    """
    Two-step scraper: listing page → product URLs → visit each product page.

    Visiting an AE product page triggers the same API/next-data responses we
    intercept in scrape_american_eagle(), but scoped to one product — giving
    accurate colors per item. Returns one dict per product-color variant.
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

        for page_info in AMERICAN_EAGLE_PAGES:
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
                product_colors: list[str] = []

                def _handle_product_response(response: object) -> None:
                    try:
                        r_url: str = response.url  # type: ignore[attr-defined]
                        if "ae.com" not in r_url:
                            return
                        ct: str = response.headers.get("content-type", "")  # type: ignore[attr-defined]
                        if "json" not in ct:
                            return
                        if response.status != 200:  # type: ignore[attr-defined]
                            return
                        data = response.json()  # type: ignore[attr-defined]
                        _walk_json_for_colors(data, product_colors)
                    except Exception:
                        pass

                page.on("response", _handle_product_response)
                try:
                    page.goto(prod_url, wait_until="domcontentloaded", timeout=30_000)
                    time.sleep(1.5)

                    # Also try __NEXT_DATA__ from the PDP
                    next_data_colors = _extract_colors_from_next_data(page)
                    all_page_colors = _dedupe_ae_colors_preserve_order(
                        product_colors + next_data_colors
                    )

                    # Title
                    title = ""
                    for sel in ["h1[class*='product-name']", "h1[class*='ProductName']",
                                "[data-auto-id='product-title']", "h1"]:
                        try:
                            el = page.query_selector(sel)
                            if el:
                                title = el.inner_text().strip()
                                if title:
                                    break
                        except Exception:
                            continue

                    colors = all_page_colors if all_page_colors else _extract_swatch_colors(page)
                    colors = dedupe_swatch_labels_preserve_order(colors)
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

    Color comes from API/page-data interception (swatch_colors), supplemented
    by title keyword extraction. Category and material come from title keywords.
    """
    counts: dict[str, dict[str, int]] = {ft: {} for ft in FEATURE_TYPES}

    # Colors from swatches — each swatch label is one occurrence of that color
    for swatch_label in swatch_colors:
        color = extract_color(swatch_label)
        if color:
            counts["color"][color] = counts["color"].get(color, 0) + 1

    # Category, material, and supplementary color from product titles
    for title in titles:
        category = extract_category(title)
        if category:
            counts["category"][category] = counts["category"].get(category, 0) + 1

        material = extract_material(title, inferred_category=category)
        if material:
            counts["material"][material] = counts["material"].get(material, 0) + 1

        # Color from title: always extracted as a supplement because AE swatch
        # alt attributes use brand-specific names (e.g. "Twilight", "Onyx") that
        # may not match a keyword, while titles sometimes include plain color
        # words ("AE Black Denim Jacket", "Blue Plaid Flannel Shirt").
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
    retailer: str = "american_eagle",
) -> pd.DataFrame:
    """Build a one-row-per-item-color DataFrame with lookup-table IDs.

    Each product is expanded to one row per unique swatch/API color seen on
    its page, matching the H&M one-row-per-article-color schema.
    """
    rows = []
    for item in raw_items:
        title        = item["title"]
        gender       = item["gender"]
        product_type = (
            item["product_type_raw"]
            if item.get("product_type_raw") and item["product_type_raw"] != "unknown"
            else extract_product_type(title)
        )
        material = (
            item["material_raw"]
            if item.get("material_raw") and item["material_raw"] != "unknown"
            else extract_material(title, inferred_category=extract_category(title))
        )

        if item.get("color"):
            color_labels = [item.get("color_raw") or item["color"]]
        elif item.get("page_swatches"):
            color_labels = dedupe_swatch_labels_preserve_order(item["page_swatches"])
        else:
            title_color = extract_color(title)
            color_labels = [title_color] if title_color else ["unknown"]

        for color_raw_lbl in color_labels:
            if "graphical_appearance_raw" in item:
                graphical = item["graphical_appearance_raw"]
            else:
                graphical = extract_graphical_appearance(f"{title} {color_raw_lbl}".strip())
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
    default_output = _synth / "trend_signals_american_eagle.csv"
    default_items  = _synth / "items_american_eagle.csv"
    parser = argparse.ArgumentParser(
        description="Scrape American Eagle new arrivals and write trend_signals_american_eagle.csv + items_american_eagle.csv."
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
        f"American Eagle retail scraper\n"
        f"  pages: {len(AMERICAN_EAGLE_PAGES)}  headless: {args.headless}\n"
        f"  color source: swatch alt/aria-label + title keyword supplement\n"
        f"  detailed mode: {args.detailed}"
        + (f"  (max {args.max_products} products/page)" if args.detailed else "") + "\n"
        f"  output: {output_path}\n"
        f"  items:  {items_path}"
    )

    titles, swatch_colors, raw_items = scrape_american_eagle(
        sleep_between_pages=args.sleep,
        headless=args.headless,
    )

    if args.detailed:
        print("\nRunning detailed per-product scrape for items CSV...")
        raw_items = scrape_american_eagle_detailed(
            sleep_between_pages=args.sleep,
            sleep_between_products=2.0,
            headless=args.headless,
            max_products_per_page=args.max_products,
        )

    swatch_colors_for_trends = list(swatch_colors)
    if args.detailed and raw_items:
        for item in raw_items:
            cr = item.get("color_raw")
            if not isinstance(cr, str):
                continue
            s = cr.strip()
            if not s or s.lower() == "unknown":
                continue
            if _ae_looks_like_retail_color_label(s):
                swatch_colors_for_trends.append(s)

    print(
        f"\nTotal collected: {len(titles)} product titles, "
        f"{len(swatch_colors)} listing color occurrences"
        + (
            f", {len(swatch_colors_for_trends) - len(swatch_colors)} added from detailed PDPs"
            if args.detailed and len(swatch_colors_for_trends) > len(swatch_colors)
            else ""
        )
    )

    if not titles and not swatch_colors_for_trends:
        print(
            "\nWARNING: Nothing was scraped. American Eagle's anti-bot measures\n"
            "may be blocking headless Chrome. Try running with --headless false\n"
            "to open a visible browser window, which is harder to detect."
        )

    counts = count_attribute_frequencies(titles, swatch_colors_for_trends)
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
    items_frame = build_raw_items_frame(raw_items, scraped_date=scraped_date, retailer="american_eagle")
    items_frame.to_csv(items_path, index=False)
    print(f"Wrote {len(items_frame)} raw item rows → {items_path}")


if __name__ == "__main__":
    main()
