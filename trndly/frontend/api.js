// api.js — fetch + reshape adapter between the FastAPI service and the
// frontend's UI contract.
//
// ─────────────────────────────────────────────────────────────────
// API endpoints (served by backend/services/scheduleServer.py):
//   GET /options                  → vocabularies for dropdowns
//   GET /trends                   → univariate predictions (every dim/level)
//   GET /forecast/fingerprint     → single fingerprint forecast (5-D query)
//   GET /health                   → liveness
//
// Frontend contracts (fields the React screens consume):
//   TREND_DATA[]:    { name, category, state, stat }
//   LOOKUP_OPTIONS:  { color, gender, productType, material, appearance }
//   INVENTORY_SIGNALS[name]: [{ label, value, state, category }]
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

function mapTrendsToTrendData(apiRows) {
  if (!Array.isArray(apiRows)) return [];
  const out = [];
  for (const r of apiRows) {
    const category = DIMENSION_TO_CATEGORY[r.dimension];
    if (!category) continue;
    out.push({
      name: r.level_name,
      category,
      state: r.state,
      stat: r.stat,
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

// ─── /forecast/fingerprint → INVENTORY_SIGNALS row[] ───────────────────
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
  // each dimension against /trends (cached by SWR upstream) and pick the
  // matching row. dataProvider does that wiring; here we just return the
  // single fingerprint row.
  return data;
}

Object.assign(window, {
  API_BASE,
  apiFetcher,
  DIMENSION_TO_CATEGORY,
  mapTrendsToTrendData,
  mapOptionsToLookupOptions,
  indexOptionsById,
  fetchFingerprintSignals,
});
