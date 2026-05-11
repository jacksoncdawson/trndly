// ScreenTrends.jsx — browse all trending features.
//
// Two layouts driven by viewport width:
//
//   ≥ COLUMNS_BREAKPOINT (1500px)
//     Five fixed columns, one per category (color / material / appearance /
//     product type / gender). Each column scrolls independently below the
//     header. No category/state filter chips — the columns ARE the
//     categorization, and within each column rows sort by state importance
//     (rising → peak → falling → flat).
//
//   < COLUMNS_BREAKPOINT
//     Single auto-fill grid with the category + state filter chips above
//     it (the original layout). When the viewport isn't wide enough for
//     five readable columns, this falls back to the chip-based filter so
//     nothing's cramped.
//
// The breakpoint is `5 × ~280px card + 5 × 16px gap + 2 × 32px page padding`
// ≈ 1480px; we round to 1500 for headroom.

const COLUMNS_BREAKPOINT = 1500;

const CATEGORY_COLUMNS = ['color', 'material', 'appearance', 'product type', 'gender'];
const CATEGORY_OPTIONS = ['all', ...CATEGORY_COLUMNS];
const STATE_OPTIONS = ['all', 'rising', 'peak', 'flat', 'falling'];

// Within a column, sort by state importance so the most actionable rows
// land at the top. Order: rising, peak, falling, flat.
const STATE_SORT_ORDER = { rising: 0, peak: 1, falling: 2, flat: 3 };

// Placeholder card shown while /trends is loading.
function TrendCardSkeleton() {
  return (
    <div style={{
      background: '#fff', border: '2px solid #ece2cf', borderRadius: 14,
      padding: 18, height: 180, display: 'flex', flexDirection: 'column', gap: 14,
      animation: 'hl-pulse 1.4s ease-in-out infinite',
    }}>
      <div style={{ height: 10, width: 70,  background: '#ece2cf', borderRadius: 4 }}/>
      <div style={{ height: 22, width: '70%', background: '#ece2cf', borderRadius: 6 }}/>
      <div style={{ flex: 1, background: '#f4ecdc', borderRadius: 8 }}/>
    </div>
  );
}

function TrendsApiErrorCard({ error, fullWidth = true }) {
  const origin = (typeof window !== 'undefined' && window.API_BASE) || (typeof window !== 'undefined' ? window.location.origin : '');
  const msg = error && (error.message || String(error));
  const truncated = msg ? msg.slice(0, 200) : 'Unknown error';
  return (
    <div style={fullWidth ? { gridColumn: '1/-1' } : undefined}>
      <div style={{
        background: '#fdfaf3', border: '2px solid #c64a3a', borderRadius: 14,
        boxShadow: '2px 2px 0 0 #1a1a1a', padding: '20px 22px', maxWidth: 720,
      }}>
        <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 8 }}>Can't reach the forecast service.</div>
        <div style={{ color: '#5a544a', fontSize: 14, whiteSpace: 'pre-wrap' }}>
          {`The UI is hitting ${origin} and got an error. Make sure the API is running and the predictions bundle is loaded (check /health).\n\nError: ${truncated}`}
        </div>
      </div>
    </div>
  );
}

// Hook: returns true when the viewport is wide enough for the 5-column layout.
function useColumnsLayout() {
  const [wide, setWide] = React.useState(
    () => typeof window !== 'undefined' && window.innerWidth >= COLUMNS_BREAKPOINT
  );
  React.useEffect(() => {
    const onResize = () => setWide(window.innerWidth >= COLUMNS_BREAKPOINT);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return wide;
}

// ─── Filter-chip layout (narrow viewport / fallback) ────────────────────
function TrendsChipLayout({ trends, trendsLoading, trendsError }) {
  const [activeCategory, setActiveCategory] = React.useState('all');
  const [activeState, setActiveState] = React.useState('all');

  const display = Array.isArray(trends)
    ? trends.filter(t => {
        const catMatch = activeCategory === 'all' || t.category === activeCategory;
        const stateMatch = activeState === 'all' || t.state === activeState;
        return catMatch && stateMatch;
      })
    : [];

  const labelStyle = {
    fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
    letterSpacing: '.07em', color: '#8a8275',
    width: 72, flexShrink: 0,
  };

  let body;
  if (trendsError) {
    body = <TrendsApiErrorCard error={trendsError}/>;
  } else if (trendsLoading || !Array.isArray(trends)) {
    body = Array.from({ length: 6 }).map((_, i) => <TrendCardSkeleton key={i}/>);
  } else if (display.length === 0) {
    body = <div style={{ gridColumn: '1/-1', textAlign: 'center', padding: '48px 0', fontSize: 15, color: '#8a8275' }}>No features match this filter.</div>;
  } else {
    body = display.map((t, i) => <TrendCard key={i} {...t}/>);
  }

  return (
    <>
      <div style={{ padding: '20px 32px', borderBottom: '2px solid #1a1a1a', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <span style={{ ...labelStyle, paddingTop: 8 }}>Category</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, flex: 1, minWidth: 0 }}>
            {CATEGORY_OPTIONS.map(f => (
              <TrendChip key={f} label={f} active={activeCategory === f} onClick={() => setActiveCategory(f)}/>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <span style={{ ...labelStyle, paddingTop: 8 }}>State</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, flex: 1, minWidth: 0 }}>
            {STATE_OPTIONS.map(s => (
              <TrendChip key={s} label={s} active={activeState === s} onClick={() => setActiveState(s)}/>
            ))}
          </div>
        </div>
      </div>
      <div style={{ padding: 32, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
        {body}
      </div>
    </>
  );
}

// ─── 5-column layout (wide viewport) ────────────────────────────────────
// Visual treatment: no per-column card chrome. Columns are separated by a
// 1px hairline divider. Each column has a small uppercase category label
// followed by a hairline under it; the body below scrolls independently.
// A state-filter chip row sits above the columns and filters within each.
function TrendsColumnsLayout({ trends, trendsLoading, trendsError }) {
  const [activeState, setActiveState] = React.useState('all');

  const labelStyle = {
    fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
    letterSpacing: '.07em', color: '#8a8275',
    width: 72, flexShrink: 0,
  };

  if (trendsError) {
    return (
      <div style={{ padding: 32 }}>
        <TrendsApiErrorCard error={trendsError} fullWidth={false}/>
      </div>
    );
  }

  return (
    <>
      {/* State filter chips — apply within every column */}
      <div style={{ padding: '16px 32px 14px', borderBottom: '1px solid #ece2cf', display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <span style={{ ...labelStyle, paddingTop: 8 }}>State</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, flex: 1, minWidth: 0 }}>
          {STATE_OPTIONS.map(s => (
            <TrendChip key={s} label={s} active={activeState === s} onClick={() => setActiveState(s)}/>
          ))}
        </div>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(5, 1fr)',
        gap: 0,
        padding: '20px 24px 32px',
        height: 'calc(100vh - 64px - 60px)',   // TopBar + state chip row
        minHeight: 0,
      }}>
        {CATEGORY_COLUMNS.map((cat, idx) => {
          let columnBody;
          if (trendsLoading || !Array.isArray(trends)) {
            columnBody = Array.from({ length: 4 }).map((_, i) => <TrendCardSkeleton key={i}/>);
          } else {
            const rows = trends
              .filter(t => t.category === cat)
              .filter(t => activeState === 'all' || t.state === activeState)
              .slice()
              .sort((a, b) => (STATE_SORT_ORDER[a.state] ?? 99) - (STATE_SORT_ORDER[b.state] ?? 99));
            if (rows.length === 0) {
              columnBody = <div style={{ padding: '24px 8px', textAlign: 'center', fontSize: 12, color: '#8a8275' }}>
                {activeState === 'all' ? 'No features.' : `No ${activeState} features.`}
              </div>;
            } else {
              columnBody = rows.map((t, i) => <TrendCard key={i} {...t}/>);
            }
          }
          const isLast = idx === CATEGORY_COLUMNS.length - 1;
          return (
            <section
              key={cat}
              style={{
                display: 'flex', flexDirection: 'column', minHeight: 0,
                padding: '0 14px',
                borderRight: isLast ? 'none' : '1px solid #ece2cf',
              }}
            >
              {/* Column header — small uppercase label, hairline under */}
              <div style={{
                padding: '2px 0 10px',
                borderBottom: '1px solid #ece2cf',
                fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                letterSpacing: '.08em', color: '#5a544a',
                flexShrink: 0,
              }}>
                {cat}
              </div>
              {/* Scroll body — internal padding gives the card hover shadow
                  (4px down/right) room to render without overflow clipping. */}
              <div style={{
                flex: 1, overflowY: 'auto', minHeight: 0,
                padding: '4px 8px 8px 2px',
                display: 'flex', flexDirection: 'column', gap: 12,
              }}>
                {columnBody}
              </div>
            </section>
          );
        })}
      </div>
    </>
  );
}

function ScreenTrends() {
  const { trends, trendsLoading, trendsError } = useData();
  const useColumns = useColumnsLayout();

  return (
    <div data-screen-label="Trends" style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      <TopBar title="Trends"/>
      <style>{`@keyframes hl-pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.45 } }`}</style>
      {useColumns ? (
        <TrendsColumnsLayout trends={trends} trendsLoading={trendsLoading} trendsError={trendsError}/>
      ) : (
        <TrendsChipLayout trends={trends} trendsLoading={trendsLoading} trendsError={trendsError}/>
      )}
    </div>
  );
}

Object.assign(window, { ScreenTrends });
