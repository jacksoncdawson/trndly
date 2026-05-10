// ScreenTrends.jsx — browse all trending features. Filter by category + state.

// Five categories sourced from lookup.csv groupings (color, material, graphical_appearance, product_type, gender).
const CATEGORY_OPTIONS = ['all', 'color', 'material', 'appearance', 'product type', 'gender'];
const STATE_OPTIONS = ['all', 'rising', 'peak', 'flat', 'falling'];

function ScreenTrends() {
  const [activeCategory, setActiveCategory] = React.useState('all');
  const [activeState, setActiveState] = React.useState('all');

  // Trends come from the API via dataProvider (with mock fallback while loading).
  const { trends } = useData();
  const display = trends.filter(t => {
    const catMatch = activeCategory === 'all' || t.category === activeCategory;
    const stateMatch = activeState === 'all' || t.state === activeState;
    return catMatch && stateMatch;
  });

  const labelStyle = {
    fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
    letterSpacing: '.07em', color: '#8a8275',
    width: 72, flexShrink: 0,
  };

  return (
    <div data-screen-label="Trends">
      <TopBar title="Trends"/>

      {/* Filter bar — two aligned rows of identical chips */}
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

      {/* Trend card grid */}
      <div style={{ padding: 32, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
        {display.length > 0
          ? display.map((t, i) => <TrendCard key={i} {...t}/>)
          : <div style={{ gridColumn: '1/-1', textAlign: 'center', padding: '48px 0', fontSize: 15, color: '#8a8275' }}>No features match this filter.</div>
        }
      </div>
    </div>
  );
}

Object.assign(window, { ScreenTrends });
