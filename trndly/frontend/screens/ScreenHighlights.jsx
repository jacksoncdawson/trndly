// ScreenHighlights.jsx — landing screen, editorial-style "what's moving this week."
//
// Same four curated signals as before (biggest mover / at peak / sleeping low /
// sharpest drop), but rendered as a numbered editorial list rather than four
// cards, so it actually reads like a forecast and sets context up front.
//
// Each row references a real feature in TREND_DATA — keep the `state` field in
// sync with TREND_DATA so the sparkline + commentary stay coherent.
const HIGHLIGHTS = [
  { kind: 'biggest mover', headline: 'Trousers', state: 'rising',  chartState: 'rising',  detail: 'Fastest-rising product type right now. Forecast +62% over the next six months — stock what you can find.' },
  { kind: 'at peak',       headline: 'Linen',    state: 'peak',    chartState: 'peak',    detail: 'Linen has hit its seasonal ceiling. If you have a piece, list it now — the curve starts to taper after this month.' },
  { kind: 'sleeping low',  headline: 'Hoodie',   state: 'flat',    chartState: 'flat',    detail: 'Flat for two seasons running. Quietly underpriced — could be a buy opportunity before fall.' },
  { kind: 'sharpest drop', headline: 'Sequin',   state: 'falling', chartState: 'falling', detail: 'Sequin fatigue is setting in across the resale market. Clear sequined pieces before summer demand softens further.' },
];

// One row in the editorial list. No card chrome — just the number, content,
// and a generous sparkline column, separated from neighbors by a hairline.
function HighlightRow({ index, kind, headline, state, chartState, isLast }) {
  const meta = STATE_META[state] || STATE_META.flat;
  const [hov, setHov] = React.useState(false);
  // Number color tracks the state — visual through-line between the giant
  // numeral on the left and the sparkline + pill on the right.
  const numColor = {
    rising:  '#2d5e3e',
    peak:    '#c98e1f',
    falling: '#c64a3a',
    flat:    '#b5ad9c',
  }[state] || '#b5ad9c';

  return (
    <div
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(96px, auto) 1fr minmax(260px, 360px)',
        gap: 40, alignItems: 'center',
        padding: '40px 0',
        borderBottom: isLast ? 'none' : '1px solid #e6dcc8',
        transform: hov ? 'translateX(4px)' : 'none',
        transition: 'transform 240ms cubic-bezier(0.34,1.4,0.64,1)',
      }}
    >
      {/* Numeral */}
      <div style={{
        fontFamily: 'var(--font-display)', fontWeight: 800,
        fontSize: 96, fontVariationSettings: '"SOFT" 50',
        color: numColor, lineHeight: 0.85, letterSpacing: '-0.04em',
        userSelect: 'none',
      }}>{String(index + 1).padStart(2, '0')}</div>

      {/* Headline column */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
        <div style={{
          fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
          letterSpacing: '.12em', color: '#8a8275',
        }}>{kind}</div>
        <div style={{
          fontFamily: 'var(--font-display)', fontWeight: 800,
          fontSize: 'clamp(40px, 4.5vw, 56px)', fontVariationSettings: '"SOFT" 50',
          letterSpacing: '-0.025em', lineHeight: 1.0,
        }}>{headline}</div>
      </div>

      {/* Chart column — pill above, large sparkline below */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, alignItems: 'flex-end', minWidth: 0 }}>
        <span style={{
          display: 'inline-flex', alignItems: 'center', gap: 5,
          padding: '5px 14px', borderRadius: 9999, fontSize: 12, fontWeight: 700,
          border: '2px solid #1a1a1a', background: meta.bg, color: meta.color || '#1a1a1a',
          boxShadow: '2px 2px 0 0 #1a1a1a',
        }}>{meta.glyph} {state}</span>
        <div style={{ width: '100%', height: 96 }}>
          <HighlightSparkline state={chartState}/>
        </div>
      </div>
    </div>
  );
}

function ScreenHighlights() {
  return (
    <div data-screen-label="Highlights">
      <TopBar title="Highlights"/>
      <div style={{ padding: '40px 48px 80px', maxWidth: 1320, margin: '0 auto' }}>

        {/* Editorial intro */}
        <div style={{ marginBottom: 12, paddingBottom: 32, borderBottom: '2px solid #1a1a1a' }}>
          <h2 style={{
            fontFamily: 'var(--font-display)', fontWeight: 800,
            fontSize: 'clamp(56px, 6vw, 84px)', fontVariationSettings: '"SOFT" 50',
            letterSpacing: '-0.035em', lineHeight: 0.92,
          }}>What's moving.</h2>
        </div>

        {/* Editorial rows */}
        {HIGHLIGHTS.map((h, i) => (
          <HighlightRow key={i} index={i} {...h} isLast={i === HIGHLIGHTS.length - 1}/>
        ))}

      </div>
    </div>
  );
}

Object.assign(window, { ScreenHighlights });
