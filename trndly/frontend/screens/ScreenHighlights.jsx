// ScreenHighlights.jsx — landing screen, editorial-style "what's moving."
//
// Picks four rows from the live /trends data at render time:
//   biggest mover  — state==='rising', max parsed pct from `stat`
//   at peak        — state==='peak', first encountered
//   sleeping low   — state==='flat',  first encountered
//   sharpest drop  — state==='falling', most negative parsed pct from `stat`
//
// `stat` is the human-readable forecast string ("+38% next 6mo", "at peak",
// "−18% next 6mo"). We parse it just for ordering inside the rising/falling
// buckets; categorical bucketing comes from the `state` field.
//
// A bucket with no candidate is silently omitted — so the editorial list
// may be 1–4 rows depending on what the API returns. No fallbacks, no
// hand-authored copy.

function parsePctFromStat(stat) {
  if (!stat) return null;
  // Handle both ASCII hyphen-minus and Unicode minus (the API emits whatever
  // the parquet contains, and both forms appear in real data).
  const m = String(stat).match(/([+\-−])\s*(\d+(?:\.\d+)?)\s*%/);
  if (!m) return null;
  return (m[1] === '+' ? 1 : -1) * parseFloat(m[2]);
}

// Forward decline magnitude: (share_t − y_h6) / share_t. Positive = drop.
// Used to rank "sharpest drop" candidates when we fall back from falling → peak.
function forwardDecline(t) {
  const s = t && t.series;
  if (!s || !Array.isArray(s.past) || !Array.isArray(s.future)) return 0;
  const share_t = s.past[s.past.length - 1];
  const y_h6 = s.future[s.future.length - 1];
  if (!share_t) return 0;
  return (share_t - y_h6) / share_t;
}

function pickHighlights(trends) {
  if (!Array.isArray(trends)) return [];
  const rising  = trends.filter(t => t.state === 'rising');
  const peak    = trends.filter(t => t.state === 'peak');
  const flat    = trends.filter(t => t.state === 'flat');
  const falling = trends.filter(t => t.state === 'falling');

  const biggestMover = rising
    .map(t => ({ t, pct: parsePctFromStat(t.stat) ?? -Infinity }))
    .sort((a, b) => b.pct - a.pct)[0]?.t;

  // Sharpest drop: prefer a `falling` row by most-negative stat; if no
  // falling rows exist (classifier is conservative; this is currently the
  // case on the production cube), fall back to the `peak` row with the
  // largest forward decline.
  let sharpestDrop = falling
    .map(t => ({ t, pct: parsePctFromStat(t.stat) ?? Infinity }))
    .sort((a, b) => a.pct - b.pct)[0]?.t;
  if (!sharpestDrop && peak.length) {
    sharpestDrop = [...peak].sort((a, b) => forwardDecline(b) - forwardDecline(a))[0];
  }

  // "At peak" prefers a peak row that isn't already the sharpest-drop pick,
  // so the two slots tell different stories.
  const atPeak = peak.find(t => t !== sharpestDrop) || peak[0];

  const sleepingLow = flat[0];

  const out = [];
  if (biggestMover) out.push({ kind: 'biggest mover', headline: biggestMover.name, state: biggestMover.state, chartState: biggestMover.state, series: biggestMover.series });
  if (atPeak && atPeak !== sharpestDrop)
                    out.push({ kind: 'at peak',       headline: atPeak.name,       state: atPeak.state,       chartState: atPeak.state,       series: atPeak.series       });
  if (sleepingLow)  out.push({ kind: 'sleeping low',  headline: sleepingLow.name,  state: sleepingLow.state,  chartState: sleepingLow.state,  series: sleepingLow.series  });
  if (sharpestDrop) out.push({ kind: 'sharpest drop', headline: sharpestDrop.name, state: sharpestDrop.state, chartState: sharpestDrop.state, series: sharpestDrop.series });
  return out;
}

// One row in the editorial list. No card chrome — just the number, content,
// and a generous sparkline column, separated from neighbors by a hairline.
function HighlightRow({ index, kind, headline, state, chartState, series, isLast }) {
  const [hov, setHov] = React.useState(false);
  // For the "sharpest drop" slot we always render in the brand-red falling
  // treatment — even when the slot is filled from a peak row (the case
  // today, since the cube currently has no strictly-falling entries).
  // Semantically the slot IS about a drop; the visual should match.
  const visualState = kind === 'sharpest drop' ? 'falling' : state;
  const meta = STATE_META[visualState] || STATE_META.flat;
  const numColor = {
    rising:  '#2d5e3e',
    peak:    '#c98e1f',
    falling: '#c64a3a',
    flat:    '#b5ad9c',
  }[visualState] || '#b5ad9c';

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
        }}>{meta.glyph} {visualState}</span>
        <div style={{ width: '100%', height: 96 }}>
          <HighlightSparkline state={visualState} series={series}/>
        </div>
      </div>
    </div>
  );
}

// Skeleton row shown while /trends is loading.
function HighlightRowSkeleton({ index, isLast }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'minmax(96px, auto) 1fr minmax(260px, 360px)',
      gap: 40, alignItems: 'center',
      padding: '40px 0',
      borderBottom: isLast ? 'none' : '1px solid #e6dcc8',
    }}>
      <div style={{
        fontFamily: 'var(--font-display)', fontWeight: 800,
        fontSize: 96, fontVariationSettings: '"SOFT" 50',
        color: '#ece2cf', lineHeight: 0.85, letterSpacing: '-0.04em',
        userSelect: 'none',
      }}>{String(index + 1).padStart(2, '0')}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ height: 10, width: 100, background: '#ece2cf', borderRadius: 4, animation: 'hl-pulse 1.4s ease-in-out infinite' }}/>
        <div style={{ height: 44, width: '60%',  background: '#ece2cf', borderRadius: 6, animation: 'hl-pulse 1.4s ease-in-out infinite' }}/>
      </div>
      <div/>
    </div>
  );
}

// Same card chrome used by inventory / item-detail empty states — 2px black
// border, 2px shadow offset, 14px radius — so the visual language stays consistent.
function MessageCard({ title, body, variant = 'neutral' }) {
  const bg = variant === 'error' ? '#fdfaf3' : '#fff';
  const borderColor = variant === 'error' ? '#c64a3a' : '#1a1a1a';
  return (
    <div style={{
      background: bg, border: `2px solid ${borderColor}`, borderRadius: 14,
      boxShadow: '2px 2px 0 0 #1a1a1a', padding: '20px 22px', maxWidth: 720,
    }}>
      <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 8 }}>{title}</div>
      <div style={{ color: '#5a544a', fontSize: 14, whiteSpace: 'pre-wrap' }}>{body}</div>
    </div>
  );
}

function ApiErrorCard({ error }) {
  const origin = (typeof window !== 'undefined' && window.API_BASE) || (typeof window !== 'undefined' ? window.location.origin : '');
  const msg = error && (error.message || String(error));
  const truncated = msg ? msg.slice(0, 200) : 'Unknown error';
  return (
    <MessageCard
      variant="error"
      title="Can't reach the forecast service."
      body={`The UI is hitting ${origin} and got an error. Make sure the API is running and the predictions bundle is loaded (check /health).\n\nError: ${truncated}`}
    />
  );
}

function ScreenHighlights() {
  const { trends, trendsLoading, trendsError } = useData();

  let body;
  if (trendsError) {
    body = <ApiErrorCard error={trendsError}/>;
  } else if (trendsLoading || !Array.isArray(trends)) {
    const skeletons = [0, 1, 2, 3];
    body = skeletons.map(i => (
      <HighlightRowSkeleton key={i} index={i} isLast={i === skeletons.length - 1}/>
    ));
  } else {
    const rows = pickHighlights(trends);
    if (rows.length === 0) {
      body = (
        <MessageCard
          title="No trends to highlight yet."
          body="The forecast service returned no rising/peak/flat/falling rows. Run the latest tick to refresh predictions, then reload."
        />
      );
    } else {
      body = rows.map((h, i) => (
        <HighlightRow key={i} index={i} {...h} isLast={i === rows.length - 1}/>
      ));
    }
  }

  return (
    <div data-screen-label="Highlights">
      <style>{`@keyframes hl-pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.45 } }`}</style>
      <div style={{ padding: '48px 48px 80px', maxWidth: 1320, margin: '0 auto' }}>

        {/* Editorial intro — replaces the TopBar entirely on this screen */}
        <div style={{ marginBottom: 12, paddingBottom: 32, borderBottom: '2px solid #1a1a1a' }}>
          <h2 style={{
            fontFamily: 'var(--font-display)', fontWeight: 800,
            fontSize: 'clamp(56px, 6vw, 84px)', fontVariationSettings: '"SOFT" 50',
            letterSpacing: '-0.035em', lineHeight: 0.92,
          }}>What's moving.</h2>
        </div>

        {body}

      </div>
    </div>
  );
}

Object.assign(window, { ScreenHighlights });
