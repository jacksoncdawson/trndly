"""
Shared feature-extraction lookups for retail scrapers.

Two layers:
  1. Substring keyword maps — tuples of (keyword, bucket). Order matters:
     longest / most specific phrases must appear before their substrings.
  2. ID dicts — map bucket names to integer IDs from the canonical
     trndly/data/reference/lookup.csv (H&M-derived reference). Used by
     every retailer scraper to produce a unified items table that joins
     cleanly with historical fingerprint cubes.

The full universe / source / reachability story lives in
``trndly/data/reference/SCHEMA.md``. If you change an ID or expand the
``_DELIBERATELY_UNREACHABLE_LOOKUP_IDS`` allow-list below, refresh that
doc so the audit table stays accurate.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Keyword maps                                                                  #
# --------------------------------------------------------------------------- #

# COLOR — checked against swatch labels first, product titles as fallback.
# Multi-word phrases must appear before their single-word substrings.
COLOR_KEYWORDS: list[tuple[str, str]] = [
    # Brand-specific names
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
    # ----------------------------------------------------------------------- #
    # AE marketing names — denim washes, color stories, heather variants.     #
    # Placed last so existing canonical names always win; these only fire     #
    # when no other keyword matched. Compound phrases come before simple ones #
    # to avoid e.g. "icy repair" being misclassified as white via "icy".      #
    # ----------------------------------------------------------------------- #
    # Compound denim washes / wash families
    ("vintage wash", "blue"),
    ("vintage destroy", "blue"),
    ("destroy wash", "blue"),
    ("tinted wash", "blue"),
    ("dark waves", "blue"),
    ("dark dreams", "blue"),
    ("dark and stormy", "blue"),
    ("dark atlantic", "blue"),
    ("dark ink", "blue"),
    ("deepest azure", "blue"),
    ("midnight slumber", "blue"),
    ("midnight dream", "blue"),
    ("after midnight", "blue"),
    ("retro night", "blue"),
    ("indie dark", "blue"),
    ("authentic light", "blue"),
    ("classic medium", "blue"),
    ("classic vintage", "blue"),
    ("simply dark", "blue"),
    ("had a cool moment", "blue"),
    ("icy repair", "blue"),
    ("light repair", "blue"),
    ("apricot wash", "blue"),
    ("crystal ice", "white"),
    ("darkness falls", "blue"),
    ("darkest dazzler", "blue"),
    ("starry bright", "blue"),
    ("busted bright", "blue"),
    ("patch me up", "blue"),
    ("beach dune", "beige"),
    # Single-word denim/wash signals
    ("vintage", "blue"),
    ("tinted", "blue"),
    ("destroy", "blue"),
    ("crackle", "blue"),
    ("repair", "blue"),
    ("rinse", "blue"),
    ("faded", "blue"),
    ("midnight", "blue"),
    ("atlantic", "blue"),
    ("azure", "blue"),
    ("skylight", "blue"),
    ("horizon", "blue"),
    ("ocean", "blue"),
    ("waterfall", "blue"),
    # Heather / mono-tone gray families
    ("heather frost", "gray"),
    ("storm heather", "gray"),
    ("iron heather", "gray"),
    ("new ebony heather", "gray"),
    ("ebony heather", "gray"),
    ("smokey cinder", "gray"),
    ("graphite", "gray"),
    ("smokey", "gray"),
    ("cinder", "gray"),
    ("sheet metal", "gray"),
    ("frost", "gray"),
    ("shadow", "gray"),
    ("ebony", "black"),
    # Berries & reds
    ("bordeaux", "red"),
    ("raspberry", "red"),
    ("cherry", "red"),
    ("berry", "red"),
    ("strawberry", "red"),
    # Pinks
    ("hot fuchsia", "pink"),
    ("fuchsia", "pink"),
    ("magenta", "pink"),
    ("lip gloss", "pink"),
    # Greens
    ("mint", "green"),
    ("seafoam", "green"),
    ("jade", "green"),
    ("emerald", "green"),
    ("lime", "green"),
    ("pine", "green"),
    ("palm", "green"),
    ("camo", "green"),
    # Browns
    ("toasted", "brown"),
    ("hazelnut", "brown"),
    ("hazel", "brown"),
    ("cappuccino", "brown"),
    ("coffee", "brown"),
    ("acorn", "brown"),
    ("sienna", "brown"),
    # Beiges
    ("oatmeal", "beige"),
    ("soft oat", "beige"),
    ("soft wheat", "beige"),
    ("wheat", "beige"),
    ("fawn", "beige"),
    # Whites
    ("chalk", "white"),
    ("sea salt", "white"),
    ("vanilla", "white"),
    ("icy", "white"),
]

# CATEGORY — checked against product title.
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

# MATERIAL — checked against product title (and detail-page text where available).
# Bucket strings match canonical lookup.csv names. The collapse this used to do
# (jersey/velvet/cashmere→knit/wool, satin/chiffon→silk, viscose/modal→cotton)
# was widened in 2026-05 to expose the full lookup material universe.
MATERIAL_KEYWORDS: list[tuple[str, str]] = [
    ("denim", "denim"),
    ("jean", "denim"),
    ("jort", "denim"),
    ("cutoff", "denim"),
    ("linen-blend", "linen"),
    ("linen", "linen"),
    ("chiffon", "chiffon"),
    ("georgette", "chiffon"),     # finer chiffon-like
    ("crepe", "crepe"),
    ("silk", "silk"),
    ("satin", "satin"),
    ("lace", "lace"),
    ("cashmere", "cashmere"),
    ("shearling", "shearling"),
    ("sherpa", "fleece"),         # sherpa lining is fleece-family
    ("faux fur", "faux fur"),
    ("wool", "wool"),
    ("fleece", "fleece"),
    # imitation leather / imitation suede are H&M-catalog buckets; retailers
    # surface only "faux"/"vegan" leather → keep them in the leather bucket
    # so we don't fragment the live signal.
    ("imitation leather", "leather"),
    ("imitation suede", "leather"),
    ("faux leather", "leather"),
    ("vegan leather", "leather"),
    ("suede", "suede"),
    ("leather", "leather"),
    ("rib-knit", "knit"),
    ("ribbed", "knit"),
    ("jersey", "jersey"),
    ("velvet", "velvet"),
    ("velour", "velour"),
    ("knit", "knit"),
    ("crochet", "knit"),
    ("waffle", "knit"),
    ("nylon", "nylon"),
    ("acrylic", "acrylic"),
    ("tulle", "tulle"),
    ("mesh", "mesh"),
    ("spandex", "polyester"),
    ("elastane", "polyester"),
    ("polyester", "polyester"),
    ("recycled", "polyester"),
    ("poplin", "cotton"),
    ("twill", "twill"),
    ("terry", "cotton"),
    ("corduroy", "corduroy"),
    ("canvas", "canvas"),
    ("tencel", "tencel"),
    ("lyocell", "lyocell"),
    ("modal", "modal"),
    ("viscose", "viscose"),
    ("rayon", "viscose"),         # rayon is a viscose synonym
    ("cotton", "cotton"),
]

# Used by extract_material when no material keyword is found in the title.
# Pants intentionally omitted — see extract_material below.
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
    # --- Intimates / sleep / swim ---
    # Listed first so multi-word phrases pre-empt generic singletons further
    # below (e.g. "pj pants" must beat "pant", "boxer brief" must beat both
    # "shorts" and the bare "boxer" entry). Bare "bra" / "robe" are excluded:
    # they substring-match safe English words like "Nebraska", "wardrobe".
    # Several entries match Gap's webProductType strings ("womens bras",
    # "body lounge bottoms") which never appear in product titles, so a
    # gender-prefixed full-string match is safe.

    # webProductType-specific (gender-prefixed; effectively never in titles)
    ("womens bras", "Bra"),
    ("mens boxers", "Underwear bottom"),
    ("womens underwear", "Underwear bottom"),
    ("mens underwear", "Underwear bottom"),
    ("womens swimwear", "Swimsuit"),
    ("mens swimwear", "Swimsuit"),
    ("body lounge bottoms", "Pyjama bottom"),

    # Title-friendly multi-word phrases
    ("boxer brief", "Underwear bottom"),
    ("sports bra", "Bra"),
    ("bikini top", "Bikini top"),
    ("bikini bottom", "Swimwear bottom"),
    ("swim brief", "Swimwear bottom"),
    ("swim trunk", "Swimwear bottom"),
    ("swim short", "Swimwear bottom"),
    ("pj pants", "Pyjama bottom"),
    ("pj short", "Pyjama bottom"),
    ("pj bottom", "Pyjama bottom"),
    ("pj set", "Pyjama set"),
    ("pajama pants", "Pyjama bottom"),
    ("pajama short", "Pyjama bottom"),
    ("pajama set", "Pyjama set"),

    # Title-friendly multi-word phrases (continued)
    ("wireless bra", "Bra"),  # Uniqlo "Square Neck Wireless Bra | Striped" etc.
    ("cozy robe", "Robe"),    # bare "robe" would match "wardrobe"
    ("night gown", "Night gown"),
    ("nightgown", "Night gown"),
    ("nightdress", "Night gown"),

    # Title-friendly singletons
    ("loungewear", "Pyjama bottom"),
    ("bathrobe", "Robe"),
    ("swimwear", "Swimsuit"),
    ("swimsuit", "Swimsuit"),
    ("bralette", "Bra"),
    ("bikini", "Underwear bottom"),
    ("underwear", "Underwear bottom"),
    ("shortie", "Underwear bottom"),  # AE-style short underwear; sibling of "shorty"
    ("hipster", "Underwear bottom"),
    ("hiphugger", "Underwear bottom"),
    ("thong", "Underwear bottom"),
    ("boxer", "Underwear bottom"),
    # placed AFTER "swim trunk" (line above) and the swim/pj phrases
    ("trunk", "Underwear bottom"),
    ("brief", "Underwear bottom"),  # "High Rise Briefs", "Lace Briefs"

    # --- Standard apparel ---
    ("polo", "Polo shirt"),
    ("hoodie", "Hoodie"),
    ("sweatshirt", "Hoodie"),
    ("cardigan", "Cardigan"),
    ("poncho", "Cardigan"),  # wrap garment
    ("shrug", "Cardigan"),   # short cropped cardigan-style
    ("turtleneck", "Sweater"),
    ("sweater", "Sweater"),
    ("pullover", "Sweater"),
    ("blazer", "Blazer"),
    ("puffer", "Jacket"),
    ("windbreaker", "Jacket"),
    ("blouson", "Jacket"),  # outerwear blouson jacket
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
    ("culotte", "Trousers"),  # Hollister wide-leg pants
    ("capri", "Trousers"),    # placed before "cap"; "Capris" otherwise hits Cap
    ("jort", "Shorts"),
    ("jegging", "Leggings/Tights"),  # jean+legging hybrid; "legging" doesn't substring-match
    ("legging", "Leggings/Tights"),
    ("dungarees", "Dungarees"),
    ("overalls", "Dungarees"),
    ("pant", "Trousers"),
    ("shorts", "Shorts"),
    ("sarong", "Sarong"),
    ("skort", "Skirt"),  # skirt-shorts hybrid; "skirt" doesn't substring-match
    ("skirt", "Skirt"),
    ("playsuit", "Jumpsuit/Playsuit"),
    ("romper", "Jumpsuit/Playsuit"),
    ("jumpsuit", "Jumpsuit/Playsuit"),
    ("bodysuit", "Bodysuit"),
    ("body suit", "Bodysuit"),
    ("dress", "Dress"),
    ("t-shirt", "T-shirt"),
    ("tee", "T-shirt"),
    ("manga ut", "T-shirt"),       # Uniqlo manga UT graphic tees
    ("ut shueisha", "T-shirt"),    # variant of above
    ("football jersey", "T-shirt"),  # Hollister/AE graphic jerseys
    ("hockey jersey", "T-shirt"),
    ("cami", "Vest top"),
    ("tank", "Vest top"),
    ("vest", "Vest top"),
    ("blouse", "Blouse"),
    ("crop", "Top"),
    ("babydoll", "Top"),  # Hollister flowy babydoll tops
    ("henley", "Top"),    # placed before "shirt" so "Short-Sleeve Henley" wins Top, not Shorts
    ("shirt", "Shirt"),
    ("top", "Top"),
    ("flip flop", "Flip flop"),
    ("sneaker", "Sneakers"),
    # bootie/booties beat "boot" → Boots; ankle-high silhouette is its own ID.
    ("bootie", "Bootie"),
    ("booties", "Bootie"),
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
    ("backpack", "Bag"),
    ("weekender", "Bag"),  # travel bag
    ("bag", "Bag"),
    ("belt", "Belt"),
    ("beanie", "Beanie"),
    ("bucket hat", "Bucket hat"),
    # Hat-family specifics — must come BEFORE the generic "hat" → Hat/beanie.
    # Matches hold for both compound titles ("Wide-Brim Felt Hat") and bare
    # silhouette names ("Fedora", "Panama").
    ("felt hat", "Felt hat"),
    ("fedora", "Felt hat"),
    ("straw hat", "Straw hat"),
    ("panama hat", "Straw hat"),
    ("wide-brim hat", "Hat/brim"),
    ("wide brim hat", "Hat/brim"),
    ("floppy hat", "Hat/brim"),
    ("sun hat", "Hat/brim"),
    ("cap", "Cap"),  # baseball cap, twill cap, UV protection cap
    ("hat", "Hat/beanie"),
    # Headband / hairband — Headband(93) is the canonical lookup name.
    ("headband", "Headband"),
    ("hairband", "Headband"),
    # "necktie" only — bare "tie" substring-matches too many false positives
    # (tie-dye, tie-front, untied) so we never match the generic word.
    ("necktie", "Tie"),
    ("scarves", "Scarf"),  # irregular plural — "scarf" doesn't substring-match
    ("scarf", "Scarf"),
    ("umbrella", "Umbrella"),
    ("tights", "Leggings/Tights"),  # webProductType "womens tights"
    ("footsie", "Socks"),  # no-show socks
    ("sock", "Socks"),
    # Last-resort jersey catch — placed after polo/tee/shirt/etc. so those win.
    ("jersey", "T-shirt"),
    # Last-resort singular — "shorts" already matches plural; this catches AE-style
    # singular titles ("Trekker Short", "Sweat Short"). Placed at the end so any
    # top/shirt/henley/etc. keyword wins for "Short-Sleeve Shirt" and similar.
    ("short", "Shorts"),
]

# Shade keywords used to derive color_spectrum_id from a color label.
# 0=Unknown, 1=Dark, 2=Dusty Light, 3=Light, 4=Medium Dusty, 5=Medium, 6=Bright
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
# ID lookups (canonical IDs from trndly/EDA/data/lookup.csv)                    #
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
    "cotton": 1, "jersey": 2, "denim": 3, "lace": 4, "viscose": 5,
    "knit": 6, "crepe": 8, "wool": 9, "twill": 10, "linen": 12,
    "mesh": 13, "satin": 14, "polyester": 15, "chiffon": 16,
    "faux fur": 17, "leather": 18, "velour": 19, "lyocell": 21,
    "fleece": 22, "modal": 23, "canvas": 24, "corduroy": 25,
    "silk": 26, "cashmere": 27, "nylon": 28, "suede": 29,
    "velvet": 30, "shearling": 31, "tulle": 32, "acrylic": 33,
    "tencel": 34,
    # Deliberately unreachable from live extractors (see
    # _DELIBERATELY_UNREACHABLE_LOOKUP_IDS): id 7 (metal — jewelry hardware),
    # id 11 (imitation leather), id 20 (imitation suede). Retailers surface
    # "faux"/"vegan" leather only, which we keep in the leather bucket.
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
    # Intimates / swim / sleepwear (IDs from data/reference/lookup.csv)
    "Bra": 8, "Bikini top": 9, "Swimwear bottom": 10, "Underwear bottom": 12,
    "Swimsuit": 21, "Pyjama set": 28, "Pyjama bottom": 36, "Underwear body": 38,
    "Robe": 52, "Underwear set": 61, "Swimwear set": 62,
    # Coverage extensions surfaced by cross-retailer audit
    "Umbrella": 81, "Bucket hat": 83, "Cap": 88,
    # Universe-coverage round (2026-05): hat/headwear/footwear variants and
    # sleepwear/accessory IDs that live retailers actually advertise.
    "Night gown": 43, "Hat/brim": 57, "Tie": 72, "Felt hat": 85,
    "Straw hat": 87, "Bootie": 92, "Headband": 93,
}

# 1=Garment Upper body, 2=Garment Lower body, 3=Garment Full body,
# 4=Swimwear, 5=Underwear, 6=Accessories, 7=Shoes, 8=Socks & Tights, 9=Nightwear
PRODUCT_TYPE_TO_GROUP_ID: dict[str, int] = {
    "T-shirt": 1, "Top": 1, "Blouse": 1, "Vest top": 1, "Shirt": 1,
    "Sweater": 1, "Hoodie": 1, "Cardigan": 1, "Polo shirt": 1,
    "Jacket": 1, "Coat": 1, "Blazer": 1,
    "Trousers": 2, "Shorts": 2, "Skirt": 2, "Leggings/Tights": 2,
    "Dungarees": 2, "Sarong": 2,
    "Dress": 3, "Jumpsuit/Playsuit": 3, "Bodysuit": 3,
    "Bag": 6, "Belt": 6, "Scarf": 6, "Hat/beanie": 6, "Beanie": 6,
    "Bucket hat": 6, "Cap": 6, "Umbrella": 6,
    "Hat/brim": 6, "Felt hat": 6, "Straw hat": 6, "Headband": 6, "Tie": 6,
    "Gloves": 6, "Sunglasses": 6, "Eyeglasses": 6, "Watch": 6,
    "Wallet": 6, "Bracelet": 6, "Necklace": 6, "Earring": 6, "Ring": 6,
    "Boots": 7, "Sneakers": 7, "Sandals": 7, "Flat shoe": 7,
    "Ballerinas": 7, "Slippers": 7, "Flip flop": 7, "Wedge": 7,
    "Heels": 7, "Pumps": 7, "Other shoe": 7, "Bootie": 7,
    "Socks": 8,
    # Intimates / swim / sleepwear
    "Bra": 5, "Underwear bottom": 5, "Underwear body": 5, "Underwear set": 5,
    "Bikini top": 4, "Swimwear bottom": 4, "Swimsuit": 4, "Swimwear set": 4,
    "Pyjama set": 9, "Pyjama bottom": 9, "Robe": 9, "Night gown": 9,
}

# --------------------------------------------------------------------------- #
# Pure extractor functions                                                      #
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


# Match a "<digits>% <Name>" component. Lookahead accepts any non-letter
# terminator — comma/semicolon/period/paren/pipe/slash/digit/end — so
# Uniqlo-style strings like "100% Cotton (25% Uses Recycled Cotton Fiber)"
# parse correctly (Gap-style "98% Cotton, 2% Elastane" already worked).
_PCT_BEFORE_NAME_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*%\s*([A-Za-z][A-Za-z\- ]{1,30}?)(?=\s*(?:[,;.()|/]|\d|$))'
)
_NAME_BEFORE_PCT_RE = re.compile(
    r'([A-Za-z][A-Za-z\- ]{1,30}?)\s+(\d+(?:\.\d+)?)\s*%'
)


def _extract_percentage_buckets(text: str) -> dict[str, float]:
    """Parse fabric strings like ``"98% Cotton, 2% Elastane"`` or
    ``"Cotton 98%, Lycra 2%"`` or ``"100% Cotton (25% Recycled Cotton)"``
    and return ``{material_bucket: total_percent}``. Multiple components
    per segment are all extracted (via ``finditer``, not ``search``), so a
    parenthetical sub-clause inside one comma-segment still contributes.

    Both forms (% before name, name before %) coexist in real catalog
    text. Returns an empty dict for text without parsable percentages —
    the caller falls back to keyword-priority extraction.
    """
    buckets: dict[str, float] = {}
    for segment in re.split(r'[,;.]', text):
        seg = segment.strip()
        if not seg or '%' not in seg:
            continue
        for m in _PCT_BEFORE_NAME_RE.finditer(seg):
            name, pct = m.group(2).strip(), float(m.group(1))
            bucket = _first_match(name, MATERIAL_KEYWORDS)
            if bucket and 0 < pct <= 100:
                buckets[bucket] = buckets.get(bucket, 0.0) + pct
        for m in _NAME_BEFORE_PCT_RE.finditer(seg):
            name, pct = m.group(1).strip(), float(m.group(2))
            bucket = _first_match(name, MATERIAL_KEYWORDS)
            if bucket and 0 < pct <= 100:
                buckets[bucket] = buckets.get(bucket, 0.0) + pct
    return buckets


def has_explicit_material_keyword(text: str) -> bool:
    """True iff `text` contains an explicit fabric keyword from
    MATERIAL_KEYWORDS (cotton, denim, linen, etc.). Used by retail
    scrapers to decide whether PDP enrichment is needed: products whose
    title carries an explicit fabric word can skip enrichment; products
    that only resolve via the category-default fallback (e.g. tops →
    cotton) should be enriched so synthetic-fabric tops aren't mis-bucketed.
    """
    return _first_match(text, MATERIAL_KEYWORDS) is not None


def extract_material(text: str, inferred_category: str | None = None) -> str | None:
    """
    Pull a material bucket from text.

    Resolution order:
      1. **Percentage-aware**: if ``text`` contains fabric components with
         explicit percentages (e.g. ``"98% Cotton, 2% Elastane"`` or
         ``"Polyester 23%, Cotton 77%"``), return the bucket whose summed
         percentages dominate. Fixes the long-standing
         ``98% Cotton, 2% Elastane → polyester`` mis-bucketing where
         Elastane outranks Cotton in plain keyword priority.
      2. **Keyword-priority**: first ``MATERIAL_KEYWORDS`` substring wins.
         Used for titles like ``"Linen-Blend Top"`` with no percentages.
      3. **Pants special case**: pants without an explicit fabric word fall
         to ``denim`` only when a denim hint is present (avoids mapping every
         chino/trouser to denim).
      4. **Category default**: for other categories,
         ``CATEGORY_TO_MATERIAL_DEFAULT`` provides a fallback.
    """
    buckets = _extract_percentage_buckets(text)
    if buckets:
        return max(buckets, key=buckets.__getitem__)
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
    """Default to 'Solid' when no pattern keyword is found."""
    result = _first_match(text, GRAPHICAL_APPEARANCE_KEYWORDS)
    return result if result else GRAPHICAL_APPEARANCE_DEFAULT


def extract_product_type(text: str) -> str | None:
    return _first_match(text, PRODUCT_TYPE_KEYWORDS)


def extract_color_spectrum_id(color_label: str) -> int:
    """e.g. 'Light heather grey' -> 3 (Light); returns 0 when no shade matches."""
    lowered = color_label.lower()
    for keyword, spectrum_id in COLOR_SPECTRUM_KEYWORDS:
        if keyword in lowered:
            return spectrum_id
    return 0


def extract_product_group_id(product_type: str | None) -> int:
    """e.g. 'T-shirt' -> 1 (Garment Upper body). Returns 0 when unmapped."""
    if not product_type:
        return 0
    return PRODUCT_TYPE_TO_GROUP_ID.get(product_type, 0)


# --------------------------------------------------------------------------- #
# Lookup-csv consistency validator                                              #
# --------------------------------------------------------------------------- #

# (category_in_lookup_csv, dict_object_in_this_module)
_LOOKUP_DICT_CONTRACTS: tuple[tuple[str, dict[str, int]], ...] = (
    ("color_master",         COLOR_MASTER_TO_ID),
    ("gender",               GENDER_TO_ID),
    ("graphical_appearance", GRAPHICAL_APPEARANCE_TO_ID),
    ("material",             MATERIAL_TO_ID),
    ("product_type",         PRODUCT_TYPE_TO_ID),
)

# Lookup IDs that the live extractors will *never* assign, by design.
# Membership here suppresses the reverse-direction unreachable warning. Adding
# a new ID without justification means we're hiding a real coverage gap — only
# add IDs that fall into one of these categories:
#   - H&M-catalog artifacts retailers don't surface (imitation leather/suede)
#   - duplicates / near-duplicates of an already-mapped ID (singular vs.
#     plural, peaked-cap vs. cap, alice band vs. headband)
#   - non-apparel (Waterbottle, Giftbox)
#   - ultra-niche items no live retailer in scope sells (Dog Wear, Costumes)
#   - a separate, larger ticket (Unisex gender — see TODO.md "Pinned").
_DELIBERATELY_UNREACHABLE_LOOKUP_IDS: dict[str, set[int]] = {
    # gender id=2 (Unisex) was previously deferred; build_live_cube.py
    # now collapses same-SKU-in-both-catalogs pairs into unisex rows
    # (see ``collapse_unisex``), so the live cube reaches it.
    "material": {
        7,   # metal (jewelry hardware, not a garment fabric)
        11,  # imitation leather (HM-catalog bucket; we keep faux/vegan in `leather`)
        20,  # imitation suede (HM-catalog bucket; same reasoning)
    },
    "product_type": {
        25,  # Underwear Tights — overlaps with Leggings/Tights (15)
        37,  # Other accessories — catch-all
        40,  # Hair/alice band — duplicate of Headband (93)
        42,  # Heeled sandals — overlaps with Sandals (32) / Heels (68)
        45,  # Hair ties — H&M-catalog only
        48,  # Hair string — H&M-catalog only
        50,  # Hair clip — H&M-catalog only
        56,  # Cap/peaked — overlaps with Cap (88)
        64,  # Earrings (plural duplicate of Earring=26)
        65,  # Underdress — niche
        66,  # Costumes — H&M-catalog only
        67,  # Outdoor Waistcoat — H&M-catalog only
        69,  # Outdoor trousers — H&M-catalog only
        71,  # Nipple covers — niche
        75,  # Tailored Waistcoat — H&M-catalog only
        76,  # Dog Wear — H&M-catalog only
        77,  # Pyjama jumpsuit/playsuit — niche
        78,  # Hairband — duplicate of Headband (93)
        79,  # Underwear corset — niche
        80,  # Waterbottle — non-apparel
        82,  # Braces (suspenders) — niche
        84,  # Bra extender — accessory niche
        86,  # Garment Set — catch-all
        89,  # Flat shoes (plural duplicate of Flat shoe=44)
        90,  # Alice band — duplicate of Headband
        91,  # Long John — niche thermals
        94,  # Giftbox — non-apparel
    },
    # color_master, color_spectrum, graphical_appearance, product_group all
    # have full coverage; the empty allow-list is the implicit invariant.
}


def _parse_lookup_csv(
    lookup_csv_path: "Path",
) -> tuple[dict[str, set[int]], dict[str, set[tuple[str, int]]]]:
    """Parse lookup.csv into (valid_ids_per_cat, valid_pairs_per_cat).

    Done without pandas so this stays an import-time dependency-free check.
    """
    valid_ids_per_cat: dict[str, set[int]] = {}
    valid_pairs_per_cat: dict[str, set[tuple[str, int]]] = {}
    with open(lookup_csv_path, encoding="utf-8") as fh:
        next(fh)  # header
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",", 2)
            if len(parts) != 3:
                continue
            category, id_str, name = parts
            try:
                id_int = int(id_str)
            except ValueError:
                continue
            valid_ids_per_cat.setdefault(category, set()).add(id_int)
            valid_pairs_per_cat.setdefault(category, set()).add((name.lower(), id_int))
    return valid_ids_per_cat, valid_pairs_per_cat


def _assert_lookup_csv_matches_dicts(lookup_csv_path: str | None = None) -> None:
    """Assert every (name, id) pair in our hand-written *_TO_ID dicts is
    present in data/reference/lookup.csv. Synonyms (multiple keys → same id)
    are allowed if the canonical name for that id exists in lookup.csv.

    The reverse direction — lookup IDs not reachable via any dict — is checked
    by ``_warn_unreachable_lookup_ids`` (a separate, non-fatal warning so that
    documenting deliberately-unreachable IDs doesn't break imports).

    No-ops silently if lookup.csv is absent (fresh-checkout safe; the
    serving and notebook code already use the same guard pattern).

    Raises ValueError on drift, with a diff-style message.
    """
    from pathlib import Path

    if lookup_csv_path is None:
        from pipelines.paths import LOOKUP_CSV
        lookup_csv_path = LOOKUP_CSV
    lookup_csv_path = Path(lookup_csv_path)
    if not lookup_csv_path.exists():
        return

    valid_ids_per_cat, valid_pairs_per_cat = _parse_lookup_csv(lookup_csv_path)

    drift: list[str] = []
    for category, dct in _LOOKUP_DICT_CONTRACTS:
        valid_ids = valid_ids_per_cat.get(category, set())
        valid_pairs = valid_pairs_per_cat.get(category, set())
        if not valid_ids:
            drift.append(f"  [{category}] missing from lookup.csv entirely")
            continue
        for name, id_int in dct.items():
            if id_int not in valid_ids:
                drift.append(
                    f"  [{category}] dict has ({name!r} -> {id_int}); "
                    f"id={id_int} not in lookup.csv for this category"
                )
                continue
            # Allow synonyms: dict can have keys like 'navy' that map to id=2
            # even if lookup.csv only lists 'Blue' for id=2. Just require the
            # id is present, OR the (name, id) is exact.
            if (name.lower(), id_int) in valid_pairs:
                continue
            # synonym path: id is valid, dict name is a non-canonical alias.
            # No drift.

    if drift:
        raise ValueError(
            f"feature_lookups.py drift vs {lookup_csv_path}:\n"
            + "\n".join(drift)
            + "\nFix the dict, or update lookup.csv if the canonical universe changed."
        )


def _compute_unreachable_lookup_ids(
    lookup_csv_path: str | None = None,
) -> dict[str, set[int]]:
    """Return ``{category: ids}`` where ``ids`` are lookup.csv IDs that
    cannot be produced by any *_TO_ID dict and are NOT in
    ``_DELIBERATELY_UNREACHABLE_LOOKUP_IDS``. ID 0 (Unknown sentinel) is
    always considered reachable since every scraper falls back to it.

    Returns an empty dict if every dimension is fully covered.
    Returns ``None`` if lookup.csv is absent (fresh-checkout safe).
    """
    from pathlib import Path

    if lookup_csv_path is None:
        from pipelines.paths import LOOKUP_CSV
        lookup_csv_path = LOOKUP_CSV
    lookup_csv_path = Path(lookup_csv_path)
    if not lookup_csv_path.exists():
        return {}

    valid_ids_per_cat, _ = _parse_lookup_csv(lookup_csv_path)

    unreachable: dict[str, set[int]] = {}
    for category, dct in _LOOKUP_DICT_CONTRACTS:
        lookup_ids = valid_ids_per_cat.get(category, set())
        reachable_ids = set(dct.values()) | {0}  # Unknown sentinel
        allowed = _DELIBERATELY_UNREACHABLE_LOOKUP_IDS.get(category, set())
        gap = lookup_ids - reachable_ids - allowed
        if gap:
            unreachable[category] = gap
    return unreachable


class UnreachableLookupIDWarning(UserWarning):
    """A lookup.csv ID is in neither a *_TO_ID dict nor the allow-list."""


def _warn_unreachable_lookup_ids(lookup_csv_path: str | None = None) -> None:
    """Emit a UserWarning for any lookup.csv ID not reachable from any dict
    and not on the ``_DELIBERATELY_UNREACHABLE_LOOKUP_IDS`` allow-list.

    Soft (warning, not raise) so that adding a new lookup ID later doesn't
    break every importer until a keyword/dict entry is added. CI / tests can
    convert this to a hard error via ``warnings.simplefilter('error')`` for
    the ``UnreachableLookupIDWarning`` category.
    """
    import warnings

    unreachable = _compute_unreachable_lookup_ids(lookup_csv_path)
    if not unreachable:
        return
    lines = [
        f"  [{cat}] unreachable: {sorted(ids)}"
        for cat, ids in sorted(unreachable.items())
    ]
    warnings.warn(
        "feature_lookups.py: lookup.csv IDs not reachable from any *_TO_ID "
        "dict (and not in _DELIBERATELY_UNREACHABLE_LOOKUP_IDS). Add keyword "
        "coverage or document the reason in the allow-list:\n"
        + "\n".join(lines),
        category=UnreachableLookupIDWarning,
        stacklevel=2,
    )


_assert_lookup_csv_matches_dicts()
_warn_unreachable_lookup_ids()
