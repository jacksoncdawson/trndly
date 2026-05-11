// data.js — helpers + STATE_META + LOOKUP_OPTIONS seed for trndly screens.
// All exports go on window so .jsx siblings can read them.
//
// ─────────────────────────────────────────────────────────────────
// What this file ships:
//   - `STATE_META`: visual metadata (glyph, color) for the four trend states.
//   - `lookupTrendState(name, category)`: per-feature trend label lookup,
//     category-aware to disambiguate names that appear in multiple
//     dimensions (e.g. "denim" exists as both a material AND a graphical
//     appearance — see Phase 0 collision scan).
//   - `buildSignalsFromTags`: builds the per-feature signal cards for the
//     Item Detail screen. These are LABELS only — they do not derive the
//     item-level recommendation. (See `deriveRecommendationFromSeries`
//     in this file + the synthesis path in api.js for how the actual
//     recommendation is produced from the fingerprint forecast.)
//   - `deriveRecommendationFromSeries(series)`: maps a 10-point series
//     (past3 + anchor + 6 forecast OR a synthesized joint) to one of
//     five recommendation outcomes. Operates on the same series the
//     chart displays, so the pill always agrees with what's on screen.
//   - `LOOKUP_OPTIONS`: seed for `colorSpectrum`/`productGroup` only —
//     the API `/options` endpoint covers the other five dimensions.
//
// Trends and inventory are NOT seeded here — they come from `/trends`
// (via dataProvider) and from in-session adds, respectively.
// ─────────────────────────────────────────────────────────────────


// ─────────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────────

// Category-aware lookup. Several names appear in multiple dimensions
// (denim, lace, mesh, metal, Unknown across most dims). A name-only
// lookup would return whichever one happens to land first in trends[],
// causing per-feature labels to disagree with the API. Always pass a
// category when looking up a tag value.
function lookupTrendState(name, category) {
  if (!name) return 'flat';
  const trends = Array.isArray(window.TREND_DATA) ? window.TREND_DATA : [];
  const lower = String(name).toLowerCase();
  const match = trends.find(
    t => t.name.toLowerCase() === lower && (!category || t.category === category),
  );
  return match ? match.state : 'flat';
}

// Build the per-feature signal cards (Color/Material/Appearance/Type/Gender)
// from a `tags` map produced by the Add Item form. The `category` arg to
// `lookupTrendState` matches the dimension this signal represents — keep
// it in sync with `DIMENSION_TO_CATEGORY` in api.js.
function buildSignalsFromTags(tags) {
  const out = [];
  if (tags.color)       out.push({ label: 'Color',      value: tags.color,                                                  state: lookupTrendState(tags.color,       'color'),        category: 'color' });
  if (tags.material)    out.push({ label: 'Material',   value: tags.material[0].toUpperCase() + tags.material.slice(1),     state: lookupTrendState(tags.material,    'material'),     category: 'material' });
  if (tags.appearance)  out.push({ label: 'Appearance', value: tags.appearance,                                             state: lookupTrendState(tags.appearance,  'appearance'),   category: 'appearance' });
  if (tags.productType) out.push({ label: 'Type',       value: tags.productType,                                            state: lookupTrendState(tags.productType, 'product type'), category: 'product type' });
  if (tags.gender)      out.push({ label: 'Gender',     value: tags.gender,                                                 state: lookupTrendState(tags.gender,      'gender'),       category: 'gender' });
  return out;
}

// ─────────────────────────────────────────────────────────────────
// RECOMMENDATION — derives "when to list" from a single 10-point series.
// The series is either the precomputed fingerprint forecast (the gold
// standard) or the synthesized joint built from the per-dimension
// univariate forecasts (when the fingerprint isn't precomputed; see
// `synthesizeFingerprintSeries` in api.js).
//
// Rule (read off the forward window {share_t, h1..h6}):
//   no series                 → 'more data needed'
//   max upside < 2.5%         → 'list now'   (no point holding)
//   max at h1                 → 'hold 1mo'
//   max at h2                 → 'hold 2mo'
//   max at h3..h6             → 'hold 3+'    (longer-term hold)
//
// Threshold is conservative: if the best forward-window value is barely
// above anchor, the storage cost / risk outweighs the upside.
// ─────────────────────────────────────────────────────────────────

const UPSIDE_THRESHOLD = 0.025;

function deriveRecommendationFromSeries(series) {
  if (!series || !Array.isArray(series.past) || !Array.isArray(series.future)) {
    return 'more data needed';
  }
  const past = series.past;
  const fut  = series.future;
  if (past.length !== 4 || fut.length !== 6) return 'more data needed';

  const shareT = past[3];
  if (!isFinite(shareT) || shareT <= 0) return 'more data needed';

  // Forward window: index 0 = anchor (share_t), 1..6 = h1..h6.
  const forward = [shareT, ...fut];
  let peakIdx = 0, peakVal = forward[0];
  for (let i = 1; i < forward.length; i++) {
    if (forward[i] > peakVal) { peakVal = forward[i]; peakIdx = i; }
  }
  const upside = peakVal / shareT - 1;
  if (upside < UPSIDE_THRESHOLD) return 'list now';
  if (peakIdx === 1) return 'hold 1mo';
  if (peakIdx === 2) return 'hold 2mo';
  return 'hold 3+';      // peakIdx ∈ {3,4,5,6}
}

// Visual metadata for trend states. RESERVED — these colors mean these states.
const STATE_META = {
  rising:  { glyph: '↗', bg: '#d8e7dc' },
  peak:    { glyph: '●', bg: '#f8ebc9' },
  falling: { glyph: '↘', bg: '#f5d6d0' },
  flat:    { glyph: '→', bg: '#ece2cf', color: '#5a544a' },
};

// Seed catalog options sourced from trndly/data/processed/lookup.csv.
// dataProvider replaces color/material/appearance/gender/productType with
// values from `/options` once it loads. `colorSpectrum` and `productGroup`
// are not exposed by `/options` yet — until they are, this seed is what the
// Add Item form uses for those two fields.
const LOOKUP_OPTIONS = {
  color:        ["Beige","Black","Blue","Brown","Green","Grey","Metal","Orange","Pink","Purple","Red","White","Yellow"],
  colorSpectrum:["Bright","Dark","Dusty Light","Light","Medium","Medium Dusty"],
  gender:       ["Men","Unisex","Women"],
  appearance:   ["All over pattern","Argyle","Chambray","Check","Colour blocking","Contrast","Denim","Dot","Embroidery","Front print","Glittering/Metallic","Hologram","Jacquardf","Lace","Melange","Mesh","Metallic","Mixed solid/pattern","Neps","Placement print","Sequin","Slub","Solid","Stripe","Transparent","Treatment"],
  material:     ["acrylic","canvas","cashmere","chiffon","corduroy","cotton","crepe","denim","faux fur","fleece","imitation leather","imitation suede","jersey","knit","lace","leather","linen","lyocell","mesh","metal","modal","nylon","polyester","satin","shearling","silk","suede","tencel","tulle","twill","velour","velvet","viscose","wool"],
  productGroup: ["Accessories","Garment Full body","Garment Lower body","Garment Upper body","Nightwear","Shoes","Socks & Tights","Swimwear","Underwear"],
  productType:  ["Alice band","Bag","Ballerinas","Beanie","Belt","Bikini top","Blazer","Blouse","Bodysuit","Bootie","Boots","Bra","Bra extender","Bracelet","Braces","Bucket hat","Cap","Cap/peaked","Cardigan","Coat","Costumes","Dog Wear","Dress","Dungarees","Earring","Earrings","Eyeglasses","Felt hat","Flat shoe","Flat shoes","Flip flop","Garment Set","Giftbox","Gloves","Hair clip","Hair string","Hair ties","Hair/alice band","Hairband","Hat/beanie","Hat/brim","Headband","Heeled sandals","Heels","Hoodie","Jacket","Jumpsuit/Playsuit","Leggings/Tights","Long John","Necklace","Night gown","Nipple covers","Other accessories","Other shoe","Outdoor Waistcoat","Outdoor trousers","Polo shirt","Pumps","Pyjama bottom","Pyjama jumpsuit/playsuit","Pyjama set","Sandals","Sarong","Scarf","Shirt","Shorts","Skirt","Slippers","Sneakers","Socks","Straw hat","Sunglasses","Sweater","Swimsuit","Swimwear bottom","Swimwear set","T-shirt","Tailored Waistcoat","Tie","Top","Trousers","Umbrella","Underdress","Underwear Tights","Underwear body","Underwear bottom","Underwear corset","Underwear set","Vest top","Wallet","Watch","Waterbottle","Wedge"],
};

Object.assign(window, {
  STATE_META, LOOKUP_OPTIONS,
  lookupTrendState, buildSignalsFromTags, deriveRecommendationFromSeries,
  UPSIDE_THRESHOLD,
});
