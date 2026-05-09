// Chart.jsx — chart primitives shared across screens
// Exports to window: HighlightSparkline, ItemPopularityChart, ChartLegend, SectionLabel
//
// Time domain across all charts:
//   past   = 3 months  (left third of the chart)
//   now    = vertical dashed marker at x = 1/3
//   future = 6 months  (right two-thirds)
// Past line uses muted grey, predicted line uses forest green.

// Mini sparkline used by HighlightCard + ItemSignal cards.
// Coordinate system: 0..160 wide, 60 tall. NOW at x = 53.
function HighlightSparkline({ state }) {
  // Each path is 4 points across past (x: 0, 18, 36, 53)
  // and 7 points across future (x: 53, 71, 89, 107, 125, 143, 160).
  const paths = {
    rising: {
      past: 'M0,46 L18,42 L36,36 L53,30',
      pred: 'M53,30 L71,24 L89,20 L107,16 L125,14 L143,13 L160,13',
      fill: 'M53,30 L71,24 L89,20 L107,16 L125,14 L143,13 L160,13 L160,60 L53,60Z',
    },
    peak: {
      past: 'M53,18 L36,28 L18,40 L0,48'.split(' ').reverse().join(' '), // visual only
      pred: 'M53,18 L71,22 L89,30 L107,40 L125,46 L143,48 L160,48',
      fill: 'M53,18 L71,22 L89,30 L107,40 L125,46 L143,48 L160,48 L160,60 L53,60Z',
    },
    flat: {
      past: 'M0,40 L18,42 L36,38 L53,40',
      pred: 'M53,40 L71,40 L89,42 L107,40 L125,41 L143,40 L160,40',
      fill: 'M53,40 L71,40 L89,42 L107,40 L125,41 L143,40 L160,40 L160,60 L53,60Z',
    },
    falling: {
      past: 'M0,18 L18,22 L36,28 L53,34',
      pred: 'M53,34 L71,40 L89,46 L107,50 L125,54 L143,56 L160,57',
      fill: 'M53,34 L71,40 L89,46 L107,50 L125,54 L143,56 L160,57 L160,60 L53,60Z',
    },
  };
  // Fix the peak past path (reverse trick above is fragile — write it directly).
  paths.peak.past = 'M0,48 L18,40 L36,28 L53,18';

  const p = paths[state] || paths.flat;
  return (
    <svg width="100%" height="60" viewBox="0 0 160 60" preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }}>
      <path d={p.fill} fill="#2d5e3e" opacity="0.07"/>
      <path d={p.past} fill="none" stroke="#b5ad9c" strokeWidth="2" strokeLinecap="round"/>
      <path d={p.pred} fill="none" stroke="#2d5e3e" strokeWidth="2" strokeLinecap="round"/>
      <line x1="53" y1="0" x2="53" y2="60" stroke="#1a1a1a" strokeWidth="1" strokeDasharray="3 2" opacity="0.3"/>
    </svg>
  );
}

// Larger chart used on the item detail screen.
// Coordinate system: 0..480 wide, 140 tall. NOW at x = 160 (1/3 of the way across).
// Past covers 3 months, future covers 6 months.
//
// Curves are state-driven so the chart shape always matches the dominant
// trend signal of the item (passed in by ScreenItem via dominantTrendState()).
// Lower y = higher popularity.
const POPULARITY_PATHS = {
  rising: {
    past: 'M0,104 L53,94 L107,82 L160,68',
    pred: 'M160,68 L213,52 L267,40 L320,32 L373,28 L427,28 L480,30',
    fill: 'M160,68 L213,52 L267,40 L320,32 L373,28 L427,28 L480,30 L480,120 L160,120Z',
    nowY: 68,
  },
  peak: {
    past: 'M0,96 L53,82 L107,66 L160,52',
    pred: 'M160,52 L213,40 L267,38 L320,46 L373,62 L427,80 L480,94',
    fill: 'M160,52 L213,40 L267,38 L320,46 L373,62 L427,80 L480,94 L480,120 L160,120Z',
    nowY: 52,
  },
  flat: {
    past: 'M0,72 L53,68 L107,72 L160,70',
    pred: 'M160,70 L213,72 L267,68 L320,72 L373,70 L427,72 L480,70',
    fill: 'M160,70 L213,72 L267,68 L320,72 L373,70 L427,72 L480,70 L480,120 L160,120Z',
    nowY: 70,
  },
  falling: {
    past: 'M0,38 L53,46 L107,58 L160,72',
    pred: 'M160,72 L213,82 L267,92 L320,100 L373,106 L427,112 L480,116',
    fill: 'M160,72 L213,82 L267,92 L320,100 L373,106 L427,112 L480,116 L480,120 L160,120Z',
    nowY: 72,
  },
};

function ItemPopularityChart({ state = 'rising' }) {
  const p = POPULARITY_PATHS[state] || POPULARITY_PATHS.flat;
  return (
    <svg width="100%" height="140" viewBox="0 0 480 140" preserveAspectRatio="xMidYMid meet"
         style={{ display: 'block', overflow: 'visible' }}>
      {/* horizontal grid */}
      {[0.25, 0.5, 0.75].map((t, i) => (
        <line key={i} x1="0" y1={140 * t} x2="480" y2={140 * t} stroke="#ece2cf" strokeWidth="1"/>
      ))}
      {/* dashed "now" marker at x = 160 (1/3) */}
      <line x1="160" y1="0" x2="160" y2="120" stroke="#1a1a1a" strokeWidth="1.5" strokeDasharray="4 3" opacity="0.4"/>
      {/* past — 3 months in muted grey */}
      <path d={p.past} fill="none" stroke="#b5ad9c" strokeWidth="2.5" strokeLinecap="round"/>
      {/* predicted — 6 months in forest green, with soft fill */}
      <path d={p.fill} fill="#2d5e3e" opacity="0.07"/>
      <path d={p.pred} fill="none" stroke="#2d5e3e" strokeWidth="2.5" strokeLinecap="round"/>
      {/* now dot */}
      <circle cx="160" cy={p.nowY} r="5" fill="#1a1a1a"/>
      {/* date labels — 3 past months, NOW, then future months at 1/3/6 */}
      <text x="6"   y="135" fontSize="10" fill="#b5ad9c" fontFamily="monospace">FEB</text>
      <text x="92"  y="135" fontSize="10" fill="#b5ad9c" fontFamily="monospace">MAR</text>
      <text x="148" y="135" fontSize="10" fill="#1a1a1a" fontFamily="monospace" fontWeight="700">NOW</text>
      <text x="208" y="135" fontSize="10" fill="#2d5e3e" fontFamily="monospace">+1mo</text>
      <text x="315" y="135" fontSize="10" fill="#2d5e3e" fontFamily="monospace">+3mo</text>
      <text x="455" y="135" fontSize="10" fill="#2d5e3e" fontFamily="monospace">+6mo</text>
    </svg>
  );
}

// Legend strip — sits beneath any past/now/predicted chart.
function ChartLegend() {
  return (
    <div style={{ display: 'flex', gap: 16, paddingTop: 10, borderTop: '1px solid #ece2cf', fontSize: 11, color: '#5a544a' }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><span style={{ width: 16, height: 3, background: '#b5ad9c', display: 'inline-block', borderRadius: 2 }}></span>past 3mo</span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><span style={{ width: 7, height: 7, background: '#1a1a1a', display: 'inline-block', borderRadius: '50%' }}></span>now</span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><span style={{ width: 16, height: 3, background: '#2d5e3e', display: 'inline-block', borderRadius: 2 }}></span>predicted (next 6mo)</span>
    </div>
  );
}

// Caps eyebrow label used to head sections within a screen.
function SectionLabel({ children, style }) {
  return <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.07em', color: '#8a8275', marginBottom: 10, ...style }}>{children}</div>;
}

Object.assign(window, { HighlightSparkline, ItemPopularityChart, ChartLegend, SectionLabel });
