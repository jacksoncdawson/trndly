// api.js — fetch + reshape adapter between the FastAPI service and the
// frontend's UI contract.
//
// ─────────────────────────────────────────────────────────────────
// API endpoints (served by backend/services/scheduleServer.py):
//   GET /options                  → vocabularies for dropdowns
//   GET /trends                   → univariate predictions (every dim/level)
//   GET /forecast/fingerprint     → single fingerprint forecast (5-D query)
//   GET /health                   → liveness + bundle status (fetched directly
//                                   by dataProvider; no reshape needed)
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

// Same-origin (FastAPI mounts /ui statically, so the API is at /).
// Override with window.API_BASE = '...' if running the UI from a different host.
const API_BASE = (typeof window !== 'undefined' && window.API_BASE) || '';

// ─── Fetcher ────────────────────────────────────────────────────────────
async function apiFetcher(path) {
  const res = await fetch(API_BASE + path, { headers: { 'Accept': 'application/json' } });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    const err = new Error(`API ${res.status} ${path}: ${body}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
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

// ─── /forecast/fingerprint → per-item signal row ───────────────────────
// One API call returns the full 5-D fingerprint forecast; we project it into
// the per-feature signal-card shape the Item Detail screen renders.
async function fetchFingerprintSignals({
  product_type_id, gender_id, color_master_id, graphical_appearance_id, material_id,
}) {
  const qs = new URLSearchParams({
    product_type_id, gender_id, color_master_id, graphical_appearance_id, material_id,
  }).toString();
  const data = await apiFetcher(`/forecast/fingerprint?${qs}`);
  // The fingerprint endpoint returns ONE forecast for the whole 5-tuple,
  // not per-feature. To populate the four signal cards, we cross-reference
  // each dimension against /trends (cached upstream by dataProvider's
  // useFetch hook) and pick the matching row. dataProvider does that
  // wiring; here we just return the single fingerprint row.
  return data;
}

// ─── synthesizeFingerprintSeries(tags, trends) ─────────────────────────
// When `/forecast/fingerprint` returns 404 (the 5-D combination isn't
// precomputed — common for niche combos), we fall back to a joint forecast
// built from the per-dimension univariate trends already in memory.
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
