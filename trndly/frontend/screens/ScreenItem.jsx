// ScreenItem.jsx — item detail: identity hero, popularity chart, signal breakdown.
//
// Series source priority:
//   1. fingerprint.json lookup ← gold standard, used when the 5-D combo is precomputed
//   2. synthesizeFingerprintSeries(tags, trends)   ← multiplicative joint fallback
//   3. null   ← chart hidden, recommendation = "More data needed"
//
// The same series drives THREE things: the Overall Popularity chart, the
// state badge on the chart header, and the recommendation pill in the
// hero. Per-feature signal cards below are labels only — they show each
// dimension's individual trend but don't aggregate into the recommendation.

// Recommendation pill — maps an internal state (returned by
// deriveRecommendationFromSeries) to the user-facing label.
const RECOMMENDATION_PILL = {
  'list now':         { glyph: '★', label: 'List now',         bg: '#f8ebc9' },
  'hold 1mo':         { glyph: '★', label: '1 month',          bg: '#f8ebc9' },
  'hold 2mo':         { glyph: '★', label: '2 months',         bg: '#d8e7dc' },
  'hold 3+':          { glyph: '★', label: 'Hold 3+ months',   bg: '#d8e7dc' },
  'more data needed': { glyph: '?', label: 'More data needed', bg: '#ece2cf' },
  // Legacy session-state aliases (in case an item was added before this change).
  'falling':          { glyph: '★', label: 'List now',         bg: '#f5d6d0' },
};

// Color tag style picker — keeps tag pill colors consistent with item colors.
const COLOR_TAG_VARIANT = {
  beige: 'mustard', burgundy: 'rust', red: 'rust', tan: 'mustard',
  olive: 'sage', denim: 'sky', indigo: 'sky', cotton: 'sage',
};

// Reusable back-to-inventory header so the empty-state and the real screen share chrome.
function ItemHeader({ onNav }) {
  return (
    <header style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '16px 32px 12px', background: '#fbf6ee', position: 'sticky', top: 0, zIndex: 50 }}>
      <button
        onClick={() => onNav('inventory')}
        style={{ width: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#fff', border: '2px solid #1a1a1a', borderRadius: 9999, cursor: 'pointer', boxShadow: '2px 2px 0 0 #1a1a1a', flexShrink: 0, transition: 'all 160ms cubic-bezier(0.34,1.4,0.64,1)' }}
        onMouseEnter={e => { e.currentTarget.style.transform = 'translate(-1px,-1px)'; e.currentTarget.style.boxShadow = '4px 4px 0 0 #1a1a1a'; }}
        onMouseLeave={e => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = '2px 2px 0 0 #1a1a1a'; }}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#1a1a1a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
      </button>
      <span style={{ fontSize: 12, fontWeight: 600, color: '#8a8275', letterSpacing: '.02em' }}>Inventory</span>
    </header>
  );
}

// Look up the trend row that matches a signal so the per-feature mini
// sparkline can plot real numbers. Match is (category, name) — both
// case-insensitive. Returns null when the trend isn't loaded or the value
// isn't tracked; the sparkline then renders its skeleton.
function findTrendSeriesFor(trends, signal) {
  if (!Array.isArray(trends) || !signal) return null;
  const wanted = String(signal.value || '').toLowerCase();
  const match = trends.find(
    t => t.category === signal.category && String(t.name).toLowerCase() === wanted,
  );
  return match ? match.series : null;
}

// Resolve an item's stored tags to the 5-D fingerprint lookup key (also the
// useFetch cache key; apiFetcher parses the ids back out for the static
// fingerprint.json lookup). Returns null if any tag is missing or unknown to
// the loaded options vocabulary.
function buildFingerprintKey(item, lookupIds) {
  if (!item || !item.tags || !lookupIds) return null;
  const t = item.tags;
  const ids = {
    product_type_id:         lookupIds.productType?.[t.productType],
    gender_id:               lookupIds.gender?.[t.gender],
    color_master_id:         lookupIds.color?.[t.color],
    graphical_appearance_id: lookupIds.appearance?.[t.appearance],
    material_id:             lookupIds.material?.[t.material],
  };
  if (Object.values(ids).some(v => v === undefined || v === null)) return null;
  return `/forecast/fingerprint?${new URLSearchParams(ids).toString()}`;
}

// Map a recommendation state to a visual treatment for the chart's
// near-anchor state badge. We don't have the trend-state vocab on the
// recommendation; just use a neutral pill that matches the pill color.
const RECOMMENDATION_BADGE = {
  'list now':         { glyph: '↘', label: 'list now',         bg: '#f5d6d0', color: '#1a1a1a' },
  'hold 1mo':         { glyph: '↗', label: '1mo',              bg: '#f8ebc9', color: '#1a1a1a' },
  'hold 2mo':         { glyph: '↗', label: '2mo',              bg: '#d8e7dc', color: '#1a1a1a' },
  'hold 3+':          { glyph: '↗', label: '3+mo',             bg: '#d8e7dc', color: '#1a1a1a' },
  'more data needed': { glyph: '?', label: 'no data',          bg: '#ece2cf', color: '#5a544a' },
};

function ScreenItem({ onNav, index = 0 }) {
  const { inventory, signals: signalsMap, trends, lookupIds, health } = useData();
  const item    = inventory[index] || inventory[0];

  // useFetch must be called unconditionally to obey the rules of hooks —
  // we pass `null` for the key when there's no item to render, and the hook
  // short-circuits inside its own effect.
  const fingerprintKey = buildFingerprintKey(item, lookupIds);
  const fpRes = useFetch(fingerprintKey, apiFetcher);

  if (!item) {
    return (
      <div data-screen-label="Item Detail">
        <ItemHeader onNav={onNav}/>
        <div style={{ padding: 32 }}>
          <div style={{
            background: '#fff', border: '2px solid #1a1a1a', borderRadius: 14,
            boxShadow: '2px 2px 0 0 #1a1a1a', padding: '20px 22px', maxWidth: 560,
          }}>
            <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 6 }}>No item selected.</div>
            <div style={{ color: '#5a544a', fontSize: 14, marginBottom: 14 }}>
              Go back to inventory and pick a piece, or add one to get started.
            </div>
            <Button variant="primary" onClick={() => onNav('inventory')}>Back to inventory</Button>
          </div>
        </div>
      </div>
    );
  }

  const signals = signalsMap[item.name] || [];

  // ── Series source: fingerprint → synthesis → null ──────────────────
  // Synthesis kicks in when the precomputed cube doesn't carry this exact
  // 5-D combination (lookup miss → null). It multiplies per-dimension
  // univariate motions — see api.js::synthesizeFingerprintSeries.
  const fingerprintSeries = fpRes.data ? seriesFromRow(fpRes.data) : null;
  const synthesizedSeries = !fingerprintSeries
    ? synthesizeFingerprintSeries(item.tags, trends)
    : null;
  const overallSeries = fingerprintSeries || synthesizedSeries;
  const isSynthesized = !!synthesizedSeries;

  // Recommendation derived directly from the same series the chart shows.
  const recState = deriveRecommendationFromSeries(overallSeries);
  const rec      = RECOMMENDATION_PILL[recState] || RECOMMENDATION_PILL['more data needed'];
  const overallBadge = RECOMMENDATION_BADGE[recState] || RECOMMENDATION_BADGE['more data needed'];

  const anchorMonth = health && health.predictions_anchor_month;
  const lagsSynthetic = !!(health && health.lags_synthetic);

  // Tag pills derived from signals (color, material, type).
  const colorSig = signals.find(s => s.category === 'color');
  const matSig   = signals.find(s => s.category === 'material');
  const typeSig  = signals.find(s => s.category === 'product type') || { value: item.type };

  return (
    <div data-screen-label="Item Detail">
      <ItemHeader onNav={onNav}/>

      <div style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: 32 }}>

        {/* Hero: graphic, name, tags, cost, recommendation */}
        <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 24, alignItems: 'center', paddingBottom: 28, borderBottom: '2px solid #1a1a1a' }}>
          {item.image
            ? <div style={{ width: 96, height: 96, border: '2px solid #1a1a1a', borderRadius: 12, overflow: 'hidden', background: '#f5ede0', boxShadow: '2px 2px 0 0 #1a1a1a' }}>
                <img src={item.image} alt={item.name} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}/>
              </div>
            : <ItemGraphic type={item.type} size={96}/>
          }

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 26, letterSpacing: '-0.02em', fontVariationSettings: '"SOFT" 50', lineHeight: 1.1 }}>{item.name}</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {colorSig && <Tag variant={COLOR_TAG_VARIANT[item.color] || 'rust'}>{colorSig.value.toLowerCase()}</Tag>}
              {matSig   && <Tag variant="sky">{matSig.value.toLowerCase()}</Tag>}
              <Tag variant="plum">{(typeSig.value || item.type || 'piece').toLowerCase()}</Tag>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 12 }}>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.06em', color: '#8a8275' }}>Your cost</div>
              <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 34, fontVariationSettings: '"SOFT" 50', lineHeight: 1 }}>{item.cost}</div>
            </div>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 14px', background: rec.bg, border: '2px solid #1a1a1a', borderRadius: 9999, fontSize: 12, fontWeight: 700, boxShadow: '2px 2px 0 0 #1a1a1a', whiteSpace: 'nowrap' }}>
              {rec.glyph} {rec.label}
            </div>
          </div>
        </div>

        {/* Overall popularity chart */}
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
            <SectionLabel style={{ marginBottom: 0 }}>Overall popularity</SectionLabel>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 10px', borderRadius: 9999, fontSize: 11, fontWeight: 700, border: '2px solid #1a1a1a', background: overallBadge.bg, color: overallBadge.color }}>{overallBadge.glyph} {overallBadge.label}</span>
          </div>
          {overallSeries ? (
            <ItemPopularityChart state={recState === 'list now' ? 'falling' : (recState === 'more data needed' ? 'flat' : 'rising')} series={overallSeries} anchorMonth={anchorMonth}/>
          ) : (
            <div style={{
              padding: '24px', textAlign: 'center', color: '#5a544a',
              background: '#fdfaf3', border: '2px dashed #ece2cf', borderRadius: 12,
            }}>
              Forecast not available yet — pick at least one tag on this item to get a prediction.
            </div>
          )}
          {overallSeries && <div style={{ marginTop: 6 }}><ChartLegend lagsSynthetic={lagsSynthetic} synthesized={isSynthesized}/></div>}
        </div>

        {/* Signal breakdown — feature trend cards (labels only; not aggregated) */}
        <div>
          <SectionLabel>Signal breakdown</SectionLabel>
          {signals.length === 0 ? (
            <div style={{
              padding: '16px 18px', textAlign: 'center', color: '#5a544a',
              background: '#fdfaf3', border: '2px solid #ece2cf', borderRadius: 12,
            }}>
              No feature signals recorded for this item.
            </div>
          ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
            {signals.map(sig => {
              const meta = STATE_META[sig.state] || STATE_META.flat;
              return (
                <div key={sig.label} style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '14px 16px', background: '#fff', border: '2px solid #1a1a1a', borderRadius: 12, boxShadow: '2px 2px 0 0 #1a1a1a' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.07em', color: '#8a8275' }}>{sig.label}</div>
                      <div style={{ fontWeight: 700, fontSize: 14, marginTop: 2 }}>{sig.value}</div>
                    </div>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, padding: '3px 8px', borderRadius: 9999, fontSize: 11, fontWeight: 700, border: '1.5px solid #1a1a1a', background: meta.bg, color: meta.color || '#1a1a1a', flexShrink: 0 }}>{meta.glyph} {sig.state}</span>
                  </div>
                  <HighlightSparkline state={sig.state} series={findTrendSeriesFor(trends, sig)}/>
                </div>
              );
            })}
          </div>
          )}
        </div>

      </div>
    </div>
  );
}

Object.assign(window, { ScreenItem });
