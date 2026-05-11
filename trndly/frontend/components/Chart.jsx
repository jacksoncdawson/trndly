// Chart.jsx — chart primitives shared across screens.
// Exports to window: HighlightSparkline, ItemPopularityChart, ChartLegend, SectionLabel
//
// All charts share a time domain:
//   past   = 3 months observed (share_lag3, share_lag2, share_lag1, share_t)
//   now    = vertical dashed marker at x = 1/3 (between past[3] and future[0])
//   future = 6 months forecast (y_h1 … y_h6)
//
// Each "month" cell is `viewW / 9` wide — 3 intervals over the past third,
// 6 intervals over the future two-thirds, and the NOW point sits at the join.
// Past line uses muted grey; predicted line uses forest green by default
// (callers can override the future color for falling/flat-state framings).
//
// When `series` is missing (loading or schema mismatch), components render a
// flat skeleton so the layout doesn't collapse.

// ─── Series → SVG paths helper ──────────────────────────────────────────
// Auto-scales Y based on the actual values' min/max with 10% padding so a
// nearly-flat series still has visible vertical motion. Higher value = lower
// y coordinate (SVG y axis is inverted).
function _seriesToPaths(series, viewW, viewH) {
  if (!series || !Array.isArray(series.past) || !Array.isArray(series.future)) return null;
  const past = series.past;
  const future = series.future;
  if (past.length !== 4 || future.length !== 6) return null;

  const all = [...past, ...future];
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  if (!isFinite(lo) || !isFinite(hi)) return null;
  // Guarantee a non-zero range so a perfectly flat series doesn't divide by zero.
  if (hi - lo < 1e-12) {
    const eps = Math.max(Math.abs(hi) * 0.05, 1e-9);
    lo -= eps; hi += eps;
  }
  const padY = (hi - lo) * 0.1;
  lo -= padY; hi += padY;

  const cellW = viewW / 9;
  const yOf = v => viewH - ((v - lo) / (hi - lo)) * viewH;
  const xOf = i => i * cellW;

  const nowIdx = 3;            // past[3] sits at the NOW boundary
  const nowX = xOf(nowIdx);
  const nowY = yOf(past[nowIdx]);

  // Past polyline: indices 0..3.
  const pastPath = past
    .map((v, i) => `${i === 0 ? 'M' : 'L'}${xOf(i).toFixed(2)},${yOf(v).toFixed(2)}`)
    .join(' ');

  // Future polyline: starts at NOW (past[3]) so the curve visually connects,
  // then goes through future[0]..future[5] at indices 4..9.
  const futurePoints = [
    [nowX, nowY],
    ...future.map((v, i) => [xOf(nowIdx + 1 + i), yOf(v)]),
  ];
  const futurePath = futurePoints
    .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`)
    .join(' ');

  // Filled area under the future line, anchored at the chart bottom.
  const fillPath = `${futurePath} L${viewW.toFixed(2)},${viewH.toFixed(2)} L${nowX.toFixed(2)},${viewH.toFixed(2)} Z`;

  return { pastPath, futurePath, fillPath, nowX, nowY };
}

// Pick a future-line color based on trend state — keeps the visual cue from
// the categorical state while the actual shape is driven by data.
const _FUTURE_COLOR = {
  rising:  '#2d5e3e',
  peak:    '#c98e1f',
  flat:    '#8a8275',
  falling: '#c64a3a',
};

// ─── HighlightSparkline ─────────────────────────────────────────────────
// Mini sparkline used by HighlightRow + per-feature signal cards.
// Coordinate system: 0..160 wide, 60 tall. NOW at x = 53.
function HighlightSparkline({ state, series }) {
  const paths = _seriesToPaths(series, 160, 60);
  const futureColor = _FUTURE_COLOR[state] || _FUTURE_COLOR.flat;

  if (!paths) {
    // Skeleton: faint horizontal hint so the slot still has a presence.
    return (
      <svg width="100%" height="60" viewBox="0 0 160 60" preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }}>
        <line x1="0" y1="30" x2="160" y2="30" stroke="#ece2cf" strokeWidth="2" strokeLinecap="round" strokeDasharray="2 4"/>
      </svg>
    );
  }
  return (
    <svg width="100%" height="60" viewBox="0 0 160 60" preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }}>
      <path d={paths.fillPath} fill={futureColor} opacity="0.08"/>
      <path d={paths.pastPath} fill="none" stroke="#b5ad9c" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
      <path d={paths.futurePath} fill="none" stroke={futureColor} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
      <line x1={paths.nowX} y1="0" x2={paths.nowX} y2="60" stroke="#1a1a1a" strokeWidth="1" strokeDasharray="3 2" opacity="0.3"/>
    </svg>
  );
}

// ─── ItemPopularityChart ────────────────────────────────────────────────
// Larger chart used on the item detail screen.
// Coordinate system: 0..480 wide, 140 tall (drawing region 0..120; bottom 20 reserved for date labels).
// NOW at x = 160 (1/3 of the way across).
function ItemPopularityChart({ state = 'rising', series, anchorMonth }) {
  const paths = _seriesToPaths(series, 480, 120);
  const futureColor = _FUTURE_COLOR[state] || _FUTURE_COLOR.flat;

  // Month labels relative to the anchor (t-3, t-2, t-1, NOW, +1, +3, +6).
  // When the caller doesn't pass `anchorMonth`, we fall back to generic
  // "-3 / NOW / +6mo" labels.
  const months = _shortMonthLabels(anchorMonth);

  return (
    <svg width="100%" height="140" viewBox="0 0 480 140" preserveAspectRatio="xMidYMid meet"
         style={{ display: 'block', overflow: 'visible' }}>
      {/* horizontal grid */}
      {[0.25, 0.5, 0.75].map((t, i) => (
        <line key={i} x1="0" y1={120 * t} x2="480" y2={120 * t} stroke="#ece2cf" strokeWidth="1"/>
      ))}

      {!paths ? (
        <line x1="0" y1="60" x2="480" y2="60" stroke="#ece2cf" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="3 5"/>
      ) : (
        <>
          {/* dashed "now" marker */}
          <line x1={paths.nowX} y1="0" x2={paths.nowX} y2="120" stroke="#1a1a1a" strokeWidth="1.5" strokeDasharray="4 3" opacity="0.4"/>
          {/* past — 3 months in muted grey */}
          <path d={paths.pastPath} fill="none" stroke="#b5ad9c" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
          {/* predicted — 6 months in state color, with soft fill */}
          <path d={paths.fillPath} fill={futureColor} opacity="0.07"/>
          <path d={paths.futurePath} fill="none" stroke={futureColor} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/>
          {/* now dot */}
          <circle cx={paths.nowX} cy={paths.nowY} r="5" fill="#1a1a1a"/>
        </>
      )}

      {/* date labels (t-3, t-1, NOW, t+1, t+3, t+6) */}
      <text x="6"   y="135" fontSize="10" fill="#b5ad9c" fontFamily="monospace">{months.past[0]}</text>
      <text x="92"  y="135" fontSize="10" fill="#b5ad9c" fontFamily="monospace">{months.past[2]}</text>
      <text x="148" y="135" fontSize="10" fill="#1a1a1a" fontFamily="monospace" fontWeight="700">NOW</text>
      <text x="208" y="135" fontSize="10" fill="#2d5e3e" fontFamily="monospace">{months.future[0]}</text>
      <text x="315" y="135" fontSize="10" fill="#2d5e3e" fontFamily="monospace">{months.future[1]}</text>
      <text x="455" y="135" fontSize="10" fill="#2d5e3e" fontFamily="monospace">{months.future[2]}</text>
    </svg>
  );
}

// Given an "YYYY-MM" anchor string, return short month labels for past[-3..-1]
// and (informally) the future offsets. Falls back to generic strings when
// anchor is unknown.
function _shortMonthLabels(anchor) {
  const SHORT = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  if (!anchor || typeof anchor !== 'string') {
    return { past: ['-3', '-2', '-1'], future: ['+1','+3','+6'] };
  }
  const m = anchor.match(/^(\d{4})-(\d{2})/);
  if (!m) return { past: ['-3','-2','-1'], future: ['+1','+3','+6'] };
  const year = parseInt(m[1], 10);
  const month = parseInt(m[2], 10);
  const past = [3, 2, 1].map(off => {
    const mo = ((month - 1 - off) % 12 + 12) % 12;
    return SHORT[mo];
  });
  return { past, future: ['+1mo','+3mo','+6mo'] };
}

// ─── Legend strip ───────────────────────────────────────────────────────
// Two optional flags surface different kinds of "this chart isn't pure
// observed data" disclosure:
//   - `lagsSynthetic`: the past-3mo line was backfilled from seasonal
//     history because only one live month is on hand (see the cube
//     backfill script). Affects ALL items uniformly.
//   - `synthesized`: the WHOLE chart (past + future) is a multiplicative
//     joint of per-dimension univariate forecasts because the exact 5-D
//     fingerprint isn't precomputed for this item. Per-item.
function ChartLegend({ lagsSynthetic = false, synthesized = false } = {}) {
  return (
    <div style={{ paddingTop: 10, borderTop: '1px solid #ece2cf' }}>
      <div style={{ display: 'flex', gap: 16, fontSize: 11, color: '#5a544a' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 16, height: 3, background: '#b5ad9c', display: 'inline-block', borderRadius: 2 }}></span>
          past 3mo{lagsSynthetic ? '*' : ''}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 7, height: 7, background: '#1a1a1a', display: 'inline-block', borderRadius: '50%' }}></span>now
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 16, height: 3, background: '#2d5e3e', display: 'inline-block', borderRadius: 2 }}></span>predicted (next 6mo)
        </span>
      </div>
      {synthesized && (
        <div style={{ marginTop: 8, fontSize: 11, color: '#5a544a' }}>
          <strong>We've never seen this item before!</strong> Predicting based on this item's distinct characteristics.
        </div>
      )}
      {lagsSynthetic && (
        <div style={{ marginTop: 6, fontSize: 10, color: '#8a8275', fontStyle: 'italic' }}>
          * Past 3mo estimated from seasonal history — only one month of live data is on hand. Real observations will replace these once more months are scraped.
        </div>
      )}
    </div>
  );
}

// Caps eyebrow label used to head sections within a screen.
function SectionLabel({ children, style }) {
  return <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.07em', color: '#8a8275', marginBottom: 10, ...style }}>{children}</div>;
}

Object.assign(window, { HighlightSparkline, ItemPopularityChart, ChartLegend, SectionLabel });
