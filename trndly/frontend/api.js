// api.js — fetch + reshape adapter between the published static JSON and the
// frontend's UI contract.
//
// ─────────────────────────────────────────────────────────────────
// Static JSON (emitted by pipelines.monthly.publish, served same-origin under
// ./data/ on Firebase Hosting / the dev server's /ui):
//   ./data/trends.json        → univariate predictions (every dim/level)
//   ./data/options.json       → vocabularies for dropdowns
//   ./data/health.json        → bundle status + lags_synthetic flag
//   ./data/fingerprint.json   → { "pt|g|cm|ga|m": forecast } — ONE 5-D-keyed
//                               bundle; the client does the lookup (a miss
//                               returns null, not a 404)
//
// Override window.API_BASE = 'http://localhost:8000' to hit a live
// scheduleServer instead (the /forecast/fingerprint endpoint 404s on a miss).
//
// Shapes the React screens consume (produced by the adapters below):
//   Trend row:   { name, category, state, stat }
//   Options:     { color, productType, material, appearance, gender,
//                  colorSpectrum, productGroup }      // last two seeded from data.js
//   Signal card: { label, value, state, category }    // built locally by
//                                                       buildSignalsFromTags
//
// All exports go on `window` so .jsx siblings can read them.
// ─────────────────────────────────────────────────────────────────

const API_BASE = (typeof window !== 'undefined' && window.API_BASE) || '';

// API path → published static file (used in the default static mode).
const STATIC_JSON = {
  '/trends':  './data/trends.json',
  '/options': './data/options.json',
  '/health':  './data/health.json',
};

async function _fetchJson(url) {
  const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    const err = new Error(`fetch ${res.status} ${url}: ${body}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// ─── Fetcher ────────────────────────────────────────────────────────────
// Default: serve the published static JSON. With window.API_BASE set, proxy to
// a live scheduleServer instead (handy for local API development).
async function apiFetcher(path) {
  if (API_BASE) return _fetchJson(API_BASE + path);

  const base = path.split('?')[0];
  if (base === '/forecast/fingerprint') {
    const p = new URLSearchParams(path.split('?')[1] || '');
    return fetchFingerprintSignals({
      product_type_id:         p.get('product_type_id'),
      gender_id:               p.get('gender_id'),
      color_master_id:         p.get('color_master_id'),
      graphical_appearance_id: p.get('graphical_appearance_id'),
      material_id:             p.get('material_id'),
    });
  }
  const file = STATIC_JSON[base];
  if (!file) throw new Error(`no static route for ${path}`);
  return _fetchJson(file);
}

// ─── /trends → TREND_DATA shape ────────────────────────────────────────
// API dimension → UI category. Dimensions not in this map are filtered out
// (e.g., color_spectrum, product_group — the UI doesn't display them).
const DIMENSION_TO_CATEGORY = {
  color_master: 'color',
  material: 'material',
  graphical_appearance: 'appearance',
  product_type: 'product type',
  gender: 'gender',
};

// Build a [past4, future6] series from a TrendRow. Used by the chart components
// to draw real data. Returns null if any of the 4 past points is null (the API
// schema permits nulls, but in practice the predictions cube only emits rows
// with complete lag history).
function seriesFromRow(r) {
  const past   = [r.share_lag3, r.share_lag2, r.share_lag1, r.share_t];
  const future = [r.y_h1, r.y_h2, r.y_h3, r.y_h4, r.y_h5, r.y_h6];
  if (past.some(v => v == null) || future.some(v => v == null)) return null;
  return { past, future };
}

function mapTrendsToTrendData(apiRows) {
  if (!Array.isArray(apiRows)) return [];
  const out = [];
  for (const r of apiRows) {
    const category = DIMENSION_TO_CATEGORY[r.dimension];
    if (!category) continue;
    // "Unknown" buckets exist for items the scraper couldn't classify into
    // a specific level (id=0 in every dimension's lookup). They're real data
    // but they're not actionable for a reseller — drop them from the UI.
    if (String(r.level_name).toLowerCase() === 'unknown') continue;
    out.push({
      name: r.level_name,
      category,
      state: r.state,
      stat: r.stat,
      series: seriesFromRow(r),
    });
  }
  return out;
}

// ─── /options → LOOKUP_OPTIONS shape ───────────────────────────────────
// API key (plural) → LOOKUP_OPTIONS key (singular, mixed case).
function mapOptionsToLookupOptions(apiOptions) {
  if (!apiOptions || typeof apiOptions !== 'object') return null;
  const namesOf = (arr) => Array.isArray(arr) ? arr.map(o => o.name) : [];
  return {
    color: namesOf(apiOptions.colors),
    productType: namesOf(apiOptions.categories),
    material: namesOf(apiOptions.materials),
    appearance: namesOf(apiOptions.appearances),
    gender: namesOf(apiOptions.genders),
    // colorSpectrum / productGroup are not exposed by /options yet; keep
    // them seeded from data.js's LOOKUP_OPTIONS fallback so existing UI
    // code that touches them still works.
    colorSpectrum: (window.LOOKUP_OPTIONS && window.LOOKUP_OPTIONS.colorSpectrum) || [],
    productGroup:  (window.LOOKUP_OPTIONS && window.LOOKUP_OPTIONS.productGroup)  || [],
  };
}

// Also expose the {name, id} pairs untouched — needed when wiring the
// fingerprint forecast lookup, since that endpoint takes IDs.
function indexOptionsById(apiOptions) {
  if (!apiOptions || typeof apiOptions !== 'object') return {};
  const idxOf = (arr) => {
    const m = {};
    if (Array.isArray(arr)) for (const o of arr) m[o.name] = o.id;
    return m;
  };
  return {
    color: idxOf(apiOptions.colors),
    productType: idxOf(apiOptions.categories),
    material: idxOf(apiOptions.materials),
    appearance: idxOf(apiOptions.appearances),
    gender: idxOf(apiOptions.genders),
  };
}

// ─── fingerprint.json (single 5-D-keyed bundle) ────────────────────────
// Locked design: ONE fingerprint.json (not sharded); the client loads it once
// and does the 5-D lookup. The key matches pipelines.serving.fingerprint_key:
// "product_type_id|gender_id|color_master_id|graphical_appearance_id|material_id".
let _fingerprintBundle = null;   // Promise<{ key: forecastRow }>

function fingerprintKey({
  product_type_id, gender_id, color_master_id, graphical_appearance_id, material_id,
}) {
  return [
    product_type_id, gender_id, color_master_id, graphical_appearance_id, material_id,
  ].map(v => String(Number(v))).join('|');
}

function _loadFingerprintBundle() {
  if (!_fingerprintBundle) {
    _fingerprintBundle = _fetchJson('./data/fingerprint.json').catch(err => {
      _fingerprintBundle = null;   // allow a retry on the next call
      throw err;
    });
  }
  return _fingerprintBundle;
}

// Returns the precomputed forecast row for the 5-D fingerprint, or null on a
// miss. The caller routes null into synthesizeFingerprintSeries (this replaces
// the old /forecast/fingerprint 404 catch — the miss→synthesis fallback).
async function fetchFingerprintSignals(ids) {
  const bundle = await _loadFingerprintBundle();
  return bundle[fingerprintKey(ids)] || null;
}

// ─── synthesizeFingerprintSeries(tags, trends) ─────────────────────────
// When the fingerprint lookup misses (the 5-D combination isn't precomputed —
// common for niche combos), we fall back to a joint forecast built from the
// per-dimension univariate trends already in memory.
//
// Method: multiplicative independence. For each populated tag, look up
// the trend row (by category + name) and take its relative motion
// `series[i] / share_t`. Multiply across the populated dimensions to get
// a joint relative-motion series. Then anchor at share_t = 1.0 — only
// the relative motion matters for the recommendation rule (absolute
// catalog share for a never-seen-combo isn't well-defined anyway).
//
// Returns { past:[4], future:[6], synthesized:true } in the same shape
// trends rows use, plus a `synthesized` flag so the UI can label the
// chart in plain language.
//
// If no tag → trend matches exist, returns null. The caller can render
// "More data needed" in that case.
function synthesizeFingerprintSeries(tags, trends) {
  if (!tags || typeof tags !== 'object') return null;
  if (!Array.isArray(trends) || trends.length === 0) return null;

  // Pair each populated tag with its UI category (matches DIMENSION_TO_CATEGORY
  // values, which is what trend rows carry).
  const lookups = [
    ['color',        tags.color],
    ['material',     tags.material],
    ['appearance',   tags.appearance],
    ['product type', tags.productType],
    ['gender',       tags.gender],
  ];

  // For each populated tag, take the series of relative ratios (point / share_t).
  // Only dimensions that resolve to a trend row contribute to the joint —
  // missing tags are treated as a factor of 1.0 (no motion).
  const factors = []; // each: [r_lag3, r_lag2, r_lag1, r_anchor=1, r_h1..r_h6]
  for (const [category, value] of lookups) {
    if (!value) continue;
    const lower = String(value).toLowerCase();
    const row = trends.find(
      t => t.category === category && String(t.name).toLowerCase() === lower,
    );
    if (!row || !row.series) continue;
    const past = row.series.past, fut = row.series.future;
    const shareT = past[past.length - 1];
    if (!isFinite(shareT) || shareT <= 0) continue;
    const ratios = [
      past[0] / shareT, past[1] / shareT, past[2] / shareT, 1.0,
      fut[0] / shareT, fut[1] / shareT, fut[2] / shareT,
      fut[3] / shareT, fut[4] / shareT, fut[5] / shareT,
    ];
    factors.push(ratios);
  }
  if (factors.length === 0) return null;

  // Multiply factors element-wise to get the joint relative-motion series.
  const joint = new Array(10).fill(1.0);
  for (const f of factors) {
    for (let i = 0; i < 10; i++) joint[i] *= f[i];
  }

  return {
    past:   joint.slice(0, 4),
    future: joint.slice(4),     // h1..h6
    synthesized: true,
  };
}

Object.assign(window, {
  API_BASE,
  apiFetcher,
  DIMENSION_TO_CATEGORY,
  mapTrendsToTrendData,
  mapOptionsToLookupOptions,
  indexOptionsById,
  fetchFingerprintSignals,
  seriesFromRow,
  synthesizeFingerprintSeries,
});
