// TrendCard.jsx — feature trend card with sparkline
// Exports to window: TrendCard, TrendChip, ChartSparkline

// Future-line color keyed to trend state (visual cue layered on top of the
// real shape). Keep in sync with the same map in Chart.jsx.
const _FUTURE_COLOR = {
  rising:  '#2d5e3e',
  peak:    '#c98e1f',
  flat:    '#8a8275',
  falling: '#c64a3a',
};

// Convert a {past[4], future[6]} series into past + future SVG path strings
// over the given viewBox. NOW sits at x = viewW * 1/3 (the past[3] / share_t
// point). Auto-scales Y to the series min/max with 10% padding.
function _seriesToPaths(series, viewW, viewH) {
  if (!series || !Array.isArray(series.past) || !Array.isArray(series.future)) return null;
  if (series.past.length !== 4 || series.future.length !== 6) return null;
  const all = [...series.past, ...series.future];
  let lo = Math.min(...all), hi = Math.max(...all);
  if (!isFinite(lo) || !isFinite(hi)) return null;
  if (hi - lo < 1e-12) {
    const eps = Math.max(Math.abs(hi) * 0.05, 1e-9);
    lo -= eps; hi += eps;
  }
  const padY = (hi - lo) * 0.1;
  lo -= padY; hi += padY;

  const cellW = viewW / 9;
  const xOf = i => i * cellW;
  const yOf = v => viewH - ((v - lo) / (hi - lo)) * viewH;
  const nowX = xOf(3), nowY = yOf(series.past[3]);

  const pastPath = series.past
    .map((v, i) => `${i === 0 ? 'M' : 'L'}${xOf(i).toFixed(2)},${yOf(v).toFixed(2)}`)
    .join(' ');
  const futurePoints = [
    [nowX, nowY],
    ...series.future.map((v, i) => [xOf(4 + i), yOf(v)]),
  ];
  const futurePath = futurePoints
    .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`)
    .join(' ');

  return { pastPath, futurePath, nowX, nowY, viewH };
}

// ChartSparkline: lines stretch via preserveAspectRatio="none"; the "now"
// dot is rendered as an HTML overlay so it stays circular. Shows a faint
// dashed baseline when the series isn't available (no real data).
function ChartSparkline({ state, series }) {
  const paths = _seriesToPaths(series, 200, 90);
  const futureColor = _FUTURE_COLOR[state] || _FUTURE_COLOR.flat;

  if (!paths) {
    return (
      <div style={{ position: 'relative', width: '100%', height: '100%' }}>
        <svg viewBox="0 0 200 90" preserveAspectRatio="none"
             style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
          <line x1="0" y1="45" x2="200" y2="45" stroke="#ece2cf" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="3 5"/>
        </svg>
      </div>
    );
  }

  const nowYpct = (paths.nowY / 90) * 100;
  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <svg viewBox="0 0 200 90" preserveAspectRatio="none"
           style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
        <line x1={paths.nowX} y1="0" x2={paths.nowX} y2="90" stroke="#1a1a1a" strokeWidth="1.5" strokeDasharray="3 3" opacity="0.35"/>
        <path d={paths.pastPath}   fill="none" stroke="#b5ad9c"    strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
        <path d={paths.futurePath} fill="none" stroke={futureColor} strokeWidth="3"   strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
      <div style={{
        position: 'absolute',
        left: '33.5%', top: `${nowYpct}%`,
        transform: 'translate(-50%, -50%)',
        width: 8, height: 8,
        borderRadius: '50%',
        background: '#1a1a1a',
        pointerEvents: 'none',
      }}/>
    </div>
  );
}

const TREND_META = {
  rising:  { glyph: '↗', label: 'rising',  bg: '#d8e7dc' },
  peak:    { glyph: '●', label: 'peak',    bg: '#f8ebc9' },
  falling: { glyph: '↘', label: 'falling', bg: '#f5d6d0' },
  flat:    { glyph: '→', label: 'flat',    bg: '#ece2cf', color: '#5a544a' },
};

function TrendCard({ name, category, state, stat, series, onClick }) {
  const [hovered, setHovered] = React.useState(false);
  const meta = TREND_META[state] || TREND_META.flat;
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: '#ffffff', border: '2px solid #1a1a1a',
        borderRadius: 16, padding: 16,
        boxShadow: hovered ? '4px 4px 0 0 #1a1a1a' : '2px 2px 0 0 #1a1a1a',
        transform: hovered ? 'translate(-1px,-1px)' : 'none',
        transition: 'all 160ms cubic-bezier(0.34,1.4,0.64,1)',
        cursor: onClick ? 'pointer' : 'default',
      }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#8a8275' }}>{category}</div>
          <div style={{ fontSize: 16, fontWeight: 700, marginTop: 2 }}>{name}</div>
        </div>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 8px', borderRadius: 9999, fontSize: 12, fontWeight: 700, border: '2px solid #1a1a1a', background: meta.bg, color: meta.color || '#1a1a1a', whiteSpace: 'nowrap' }}>
          {meta.glyph} {meta.label}
        </span>
      </div>
      <div style={{ height: 80, position: 'relative' }}>
        <ChartSparkline state={state} series={series} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 11, color: '#8a8275', fontFamily: 'var(--font-mono)' }}>
        <span>past 3mo</span>
        <span>{stat}</span>
      </div>
    </div>
  );
}

const CHIP_COLORS = {
  // categories
  color:            '#d96941',
  material:         '#6ba8c9',
  'product type':   '#8b5a8c',
  appearance:       '#e8b840',
  gender:           '#8fb085',
  // states
  rising:  '#2d5e3e',
  peak:    '#e8b840',
  flat:    '#8a8275',
  falling: '#c64a3a',
  all:     '#8a8275',
};

function TrendChip({ label, active, onClick }) {
  const dot = CHIP_COLORS[label] || '#8a8275';
  return (
    <button onClick={onClick} style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      background: active ? '#2d5e3e' : '#fdfaf3',
      border: '2px solid #1a1a1a', borderRadius: 9999,
      padding: '6px 14px', fontSize: 13, fontWeight: 600, lineHeight: 1.2,
      cursor: 'pointer', color: active ? '#fbf6ee' : '#1a1a1a',
      fontFamily: 'var(--font-sans)',
      boxShadow: '2px 2px 0 0 #1a1a1a',
      transition: 'transform 160ms cubic-bezier(0.34,1.4,0.64,1), box-shadow 160ms cubic-bezier(0.2,0,0,1), background 120ms ease',
      whiteSpace: 'nowrap',
    }}
      onMouseEnter={e => { e.currentTarget.style.transform = 'translate(-1px,-1px)'; e.currentTarget.style.boxShadow = '4px 4px 0 0 #1a1a1a'; }}
      onMouseLeave={e => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = '2px 2px 0 0 #1a1a1a'; }}
      onMouseDown={e => { e.currentTarget.style.transform = 'translate(1px,1px)'; e.currentTarget.style.boxShadow = '1px 1px 0 0 #1a1a1a'; }}
      onMouseUp={e => { e.currentTarget.style.transform = 'translate(-1px,-1px)'; e.currentTarget.style.boxShadow = '4px 4px 0 0 #1a1a1a'; }}
    >
      {label !== 'all' && (
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          background: active ? '#fbf6ee' : dot,
          border: active ? '1.5px solid rgba(255,255,255,.4)' : '1.5px solid #1a1a1a',
          flexShrink: 0,
        }}/>
      )}
      {label}
    </button>
  );
}

Object.assign(window, { TrendCard, TrendChip, ChartSparkline });
