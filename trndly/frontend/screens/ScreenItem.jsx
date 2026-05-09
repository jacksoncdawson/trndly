// ScreenItem.jsx — item detail: identity hero, popularity chart, signal breakdown.
//
// Currently the kit demo always drills into INVENTORY_DATA[2] (the headline item
// shown in the recording). Signals come from window.INVENTORY_SIGNALS keyed by
// item name. To make this clickable per-tile later, accept an `index` prop here
// and have ScreenInventory pass it on tile click.

// Map an inventory `state` to the recommendation pill surfaced in the hero.
const RECOMMENDATION_PILL = {
  'list now': { glyph: '★', label: 'List now',     bg: '#f8ebc9' },
  'falling':  { glyph: '★', label: 'List now',     bg: '#f5d6d0' },
  'hold 1mo': { glyph: '★', label: 'Hold 1 month', bg: '#f8ebc9' },
  'hold 2mo': { glyph: '★', label: 'Hold 2 months', bg: '#d8e7dc' },
};

// Color tag style picker — keeps tag pill colors consistent with item colors.
const COLOR_TAG_VARIANT = {
  beige: 'mustard', burgundy: 'rust', red: 'rust', tan: 'mustard',
  olive: 'sage', denim: 'sky', indigo: 'sky', cotton: 'sage',
};

function ScreenItem({ onNav, index = 0 }) {
  const { inventory, signals: signalsMap } = useData();
  const item    = inventory[index] || inventory[0];
  const signals = (item && signalsMap[item.name]) || [];
  const rec     = RECOMMENDATION_PILL[item.state] || RECOMMENDATION_PILL['hold 1mo'];
  const overall = dominantTrendState(signals);
  const overallMeta = STATE_META[overall];

  // Tag pills derived from signals (color, material, type).
  const colorSig = signals.find(s => s.category === 'color');
  const matSig   = signals.find(s => s.category === 'material');
  const typeSig  = signals.find(s => s.category === 'product type') || { value: item.type };

  return (
    <div data-screen-label="Item Detail">
      {/* Breadcrumb-style sticky header */}
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
              <Tag variant="plum">{(typeSig.value || item.type).toLowerCase()}</Tag>
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
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 10px', borderRadius: 9999, fontSize: 11, fontWeight: 700, border: '2px solid #1a1a1a', background: overallMeta.bg, color: overallMeta.color || '#1a1a1a' }}>{overallMeta.glyph} {overall}</span>
          </div>
          <ItemPopularityChart state={overall}/>
          <div style={{ marginTop: 6 }}><ChartLegend/></div>
        </div>

        {/* Signal breakdown — feature trend cards */}
        <div>
          <SectionLabel>Signal breakdown</SectionLabel>
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
                  <HighlightSparkline state={sig.state}/>
                </div>
              );
            })}
          </div>
        </div>

      </div>
    </div>
  );
}

Object.assign(window, { ScreenItem });
