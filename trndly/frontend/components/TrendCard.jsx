// TrendCard.jsx — feature trend card with sparkline
// Exports to window: TrendCard, TrendChip, ChartSparkline

// ChartSparkline: lines stretch via preserveAspectRatio="none";
// the "now" dot is rendered as an HTML overlay so it stays circular.
function ChartSparkline({ state }) {
  // Time domain: past = 3mo (x: 0 → 67), future = 6mo (x: 67 → 200). NOW marker at x = 67 (1/3).
  const paths = {
    rising:  { past: 'M0,58 L22,55 L44,52 L67,48',  future: 'M67,48 L86,42 L105,36 L124,28 L143,22 L162,16 L181,12 L200,10', futureColor: '#2d5e3e', nowY: 48 },
    peak:    { past: 'M0,40 L22,30 L44,22 L67,18',  future: 'M67,18 L86,20 L105,26 L124,34 L143,42 L162,48 L181,52 L200,54', futureColor: '#2d5e3e', nowY: 18 },
    falling: { past: 'M0,28 L22,34 L44,42 L67,50',  future: 'M67,50 L86,57 L105,63 L124,68 L143,72 L162,75 L181,77 L200,78', futureColor: '#c64a3a', nowY: 50 },
    flat:    { past: 'M0,46 L22,44 L44,47 L67,45',  future: 'M67,45 L86,43 L105,46 L124,44 L143,46 L162,44 L181,45 L200,45', futureColor: '#8a8275', nowY: 45 },
  };
  const p = paths[state] || paths.flat;
  const nowYpct = (p.nowY / 90) * 100;

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <svg viewBox="0 0 200 90" preserveAspectRatio="none"
           style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
        <line x1="67" y1="0" x2="67" y2="90" stroke="#1a1a1a" strokeWidth="1.5" strokeDasharray="3 3" opacity="0.35"/>
        <path d={p.past} fill="none" stroke="#b5ad9c" strokeWidth="2.5" strokeLinecap="round"/>
        <path d={p.future} fill="none" stroke={p.futureColor} strokeWidth="3" strokeLinecap="round"/>
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

function TrendCard({ name, category, state, stat, onClick }) {
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
        <ChartSparkline state={state} />
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
