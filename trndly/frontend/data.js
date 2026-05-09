// data.js — shared mock data + state metadata for trndly screens.
// All exports go on window so .jsx siblings can read them.
//
// ─────────────────────────────────────────────────────────────────
// DEMO DATA — replace with API responses when wiring real backend.
// Everything in this file is hand-authored for the demo build. The
// shapes (TREND_DATA rows, INVENTORY_DATA rows, STATE_META, LOOKUP_OPTIONS)
// are the contract the UI consumes. Keep the field names stable when
// you swap to live data; only the source changes.
// ─────────────────────────────────────────────────────────────────

// Trends shown on the Trends screen + referenced by Highlights and Item Detail.
// Category values: color | material | appearance | product type | gender.
// Names are sourced from LOOKUP_OPTIONS (kept consistent with lookup.csv).
const TREND_DATA = [
  { name: 'Green',      category: 'color',        state: 'rising',  stat: '+38% next 6mo' },
  { name: 'Red',        category: 'color',        state: 'peak',    stat: 'at peak' },
  { name: 'Beige',      category: 'color',        state: 'rising',  stat: '+22% next 6mo' },
  { name: 'Black',      category: 'color',        state: 'flat',    stat: 'stable' },
  { name: 'linen',      category: 'material',     state: 'peak',    stat: 'at peak — sell now' },
  { name: 'corduroy',   category: 'material',     state: 'rising',  stat: '+41% next 6mo' },
  { name: 'leather',    category: 'material',     state: 'flat',    stat: 'stable' },
  { name: 'polyester',  category: 'material',     state: 'flat',    stat: 'stable' },
  { name: 'denim',      category: 'material',     state: 'falling', stat: '−18% next 6mo' },
  { name: 'Stripe',     category: 'appearance',   state: 'rising',  stat: '+29% next 6mo' },
  { name: 'Solid',      category: 'appearance',   state: 'flat',    stat: 'stable' },
  { name: 'Embroidery', category: 'appearance',   state: 'rising',  stat: '+34% next 6mo' },
  { name: 'Sequin',     category: 'appearance',   state: 'falling', stat: '−24% next 6mo' },
  { name: 'Trousers',   category: 'product type', state: 'rising',  stat: '+62% next 6mo' },
  { name: 'Blazer',     category: 'product type', state: 'peak',    stat: 'at peak' },
  { name: 'Hoodie',     category: 'product type', state: 'flat',    stat: 'stable' },
  { name: 'Skirt',      category: 'product type', state: 'falling', stat: '−12% next 6mo' },
  { name: 'Women',      category: 'gender',       state: 'flat',    stat: 'stable' },
  { name: 'Men',        category: 'gender',       state: 'rising',  stat: '+9% next 6mo' },
  { name: 'Unisex',     category: 'gender',       state: 'rising',  stat: '+18% next 6mo' },
];

// User's inventory shown on the Inventory screen + drilled into in Item Detail.
// `state` values map to TIMELINE_GROUPS in ScreenInventory.jsx:
//   list now | falling  → "List now"
//   hold 1mo            → "List in 1 month"
//   hold 2mo            → "List in 2+ months"
//
// Recommendations track the trend states of each item's features (see signals
// per item below). The story each row tells:
//
//   1. Vintage denim jacket  — denim is FALLING → list now (clear before drop)
//   2. Tan suede mini skirt  — skirt is FALLING → list now
//   3. Beige corduroy blazer — beige + corduroy RISING, blazer at PEAK
//                              → hold ~1 month (color/material climbing toward peak)
//   4. Olive cargo trousers  — trousers RISING (early), still climbing
//                              → hold 2+ months until peak
const INVENTORY_DATA = [
  { name: 'Vintage denim jacket',  color: 'denim',  type: 'jacket',  cost: '$32', added: 'added last week',   state: 'list now' },
  { name: 'Tan suede mini skirt',  color: 'tan',    type: 'skirt',   cost: '$14', added: 'added 2 weeks ago', state: 'falling'  },
  { name: 'Beige corduroy blazer', color: 'beige',  type: 'blazer',  cost: '$28', added: 'added 3 days ago',  state: 'hold 1mo' },
  { name: 'Olive cargo trousers',  color: 'olive',  type: 'pants',   cost: '$18', added: 'added today',       state: 'hold 2mo' },
];

// Per-item feature signal breakdowns (drives ScreenItem detail view).
// Indexed by inventory item name. Keep state values consistent with TREND_DATA.
const INVENTORY_SIGNALS = {
  'Vintage denim jacket': [
    { label: 'Color',      value: 'Indigo',     state: 'flat',    category: 'color' },
    { label: 'Material',   value: 'Denim',      state: 'falling', category: 'material' },
    { label: 'Appearance', value: 'Solid',      state: 'flat',    category: 'appearance' },
    { label: 'Gender',     value: 'Unisex',     state: 'rising',  category: 'gender' },
  ],
  'Tan suede mini skirt': [
    { label: 'Color',      value: 'Tan',        state: 'flat',    category: 'color' },
    { label: 'Material',   value: 'Suede',      state: 'flat',    category: 'material' },
    { label: 'Appearance', value: 'Solid',      state: 'flat',    category: 'appearance' },
    { label: 'Type',       value: 'Skirt',      state: 'falling', category: 'product type' },
  ],
  'Beige corduroy blazer': [
    { label: 'Color',      value: 'Beige',      state: 'rising',  category: 'color' },
    { label: 'Material',   value: 'Corduroy',   state: 'rising',  category: 'material' },
    { label: 'Type',       value: 'Blazer',     state: 'peak',    category: 'product type' },
    { label: 'Gender',     value: 'Womenswear', state: 'flat',    category: 'gender' },
  ],
  'Olive cargo trousers': [
    { label: 'Color',      value: 'Olive',      state: 'flat',    category: 'color' },
    { label: 'Material',   value: 'Cotton',     state: 'flat',    category: 'material' },
    { label: 'Appearance', value: 'Solid',      state: 'flat',    category: 'appearance' },
    { label: 'Type',       value: 'Trousers',   state: 'rising',  category: 'product type' },
  ],
};

// ─────────────────────────────────────────────────────────────────
// DEMO ADD-ITEM RECORD
// Item the user "adds" during the recorded demo. Not in inventory until they
// fill out the form. Tag set is designed so the dominant signal is `rising`
// (no peak, no falling) → recommendation lands in "list in 2+ months".
//
// Recording flow: Add Item → drop image → name "Dicky's Pants", $20 →
//   tags: Trousers / Beige / polyester / Solid / Men → submit →
//   flash → redirect → new tile shows in the "List in 2+ months" group.
// ─────────────────────────────────────────────────────────────────
const DEMO_ADD_ITEM = {
  name: "Dicky's Pants",
  cost: '$20',
  tags: { productType: 'Trousers', color: 'Beige', material: 'polyester', appearance: 'Solid', gender: 'Men' },
  expectedRecommendation: 'hold 2mo',
};

// ─────────────────────────────────────────────────────────────────
// HELPERS — pure functions used by ScreenAdd to derive signals + a
// recommendation from a freshly-submitted tag set. Centralized here so
// when real ML predictions land, only this file changes.
// ─────────────────────────────────────────────────────────────────

// Map a raw lookup value (e.g. "Beige", "polyester", "Trousers") to the
// trend state in TREND_DATA. Defaults to 'flat' when not tracked.
function lookupTrendState(name) {
  if (!name) return 'flat';
  const lower = String(name).toLowerCase();
  const match = TREND_DATA.find(t => t.name.toLowerCase() === lower);
  return match ? match.state : 'flat';
}

// Build the per-feature signal cards (Color/Material/Appearance/Type/Gender)
// from a `tags` map produced by the Add Item form.
function buildSignalsFromTags(tags) {
  const out = [];
  if (tags.color)       out.push({ label: 'Color',      value: tags.color,                       state: lookupTrendState(tags.color),       category: 'color' });
  if (tags.material)    out.push({ label: 'Material',   value: tags.material[0].toUpperCase() + tags.material.slice(1), state: lookupTrendState(tags.material),    category: 'material' });
  if (tags.appearance)  out.push({ label: 'Appearance', value: tags.appearance,                  state: lookupTrendState(tags.appearance),  category: 'appearance' });
  if (tags.productType) out.push({ label: 'Type',       value: tags.productType,                 state: lookupTrendState(tags.productType), category: 'product type' });
  if (tags.gender)      out.push({ label: 'Gender',     value: tags.gender,                      state: lookupTrendState(tags.gender),      category: 'gender' });
  return out;
}

// Reduce the per-feature states down to one of the four inventory states
// used by ScreenInventory's TIMELINE_GROUPS. Priority:
//   any falling → list now (clear it before further drop)
//   any peak    → hold 1mo (peak is here for one feature, others may still climb)
//   any rising  → hold 2mo (wait for things to peak)
//   else        → list now (everything flat — no upside in waiting)
function deriveRecommendation(signals) {
  const states = signals.map(s => s.state);
  if (states.includes('falling')) return 'list now';
  if (states.includes('peak'))    return 'hold 1mo';
  if (states.includes('rising'))  return 'hold 2mo';
  return 'list now';
}

// Pick the dominant trend state for an item — drives the "Overall popularity"
// chart shape + pill on the Item Detail screen. Same priority as recommendation.
function dominantTrendState(signals) {
  const states = signals.map(s => s.state);
  if (states.includes('falling')) return 'falling';
  if (states.includes('peak'))    return 'peak';
  if (states.includes('rising'))  return 'rising';
  return 'flat';
}

// Visual metadata for trend states. RESERVED — these colors mean these states.
const STATE_META = {
  rising:  { glyph: '↗', bg: '#d8e7dc' },
  peak:    { glyph: '●', bg: '#f8ebc9' },
  falling: { glyph: '↘', bg: '#f5d6d0' },
  flat:    { glyph: '→', bg: '#ece2cf', color: '#5a544a' },
};

// Real catalog options sourced from trndly/data/processed/lookup.csv.
// Keep in sync with that file — these power the Add Item form, filters, etc.
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
  TREND_DATA, INVENTORY_DATA, INVENTORY_SIGNALS, DEMO_ADD_ITEM, STATE_META, LOOKUP_OPTIONS,
  lookupTrendState, buildSignalsFromTags, deriveRecommendation, dominantTrendState,
});
