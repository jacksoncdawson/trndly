// ScreenInventory.jsx — user's inventory grouped by recommended listing window.
//
// Five groups, mapping the recommendation outcomes from
// `deriveRecommendationFromSeries` in data.js:
//   List now             ← state ∈ {list now, falling}
//   List in 1 month      ← state = hold 1mo  (forecast peaks at h1)
//   List in 2 months     ← state = hold 2mo  (forecast peaks at h2)
//   Hold 3+ months       ← state = hold 3+   (forecast peaks at h3..h6)
//   More data needed     ← state = more data needed  (no series available)
//
// Empty groups collapse so the screen never shows an empty section header.
// Click a tile → Item Detail.

const TIMELINE_GROUPS = [
  { key: 'now',  title: 'List now',         states: ['list now', 'falling'], accent: '#f8ebc9', glyph: '★' },
  { key: '1mo',  title: 'List in 1 month',  states: ['hold 1mo'],            accent: '#d8e7dc', glyph: '↗' },
  { key: '2mo',  title: 'List in 2 months', states: ['hold 2mo'],            accent: '#d8e7dc', glyph: '↗' },
  { key: '3+',   title: 'Hold 3+ months',   states: ['hold 3+'],             accent: '#ece2cf', glyph: '→' },
  { key: 'tbd',  title: 'More data needed', states: ['more data needed'],    accent: '#fdfaf3', glyph: '?' },
];

// Single tile in the inventory grid. Falls back to a placeholder garment SVG
// when no real image was uploaded (the seed inventory items).
function InventoryTile({ name, type, image, onClick, justAdded }) {
  const [hov, setHov] = React.useState(false);
  const ref = React.useRef(null);

  // Apply the entrance animation imperatively via the DOM class. We do this
  // instead of a render-time `className` because the prop-driven path was
  // unreliable under Babel-standalone in this codebase — the className would
  // not always make it onto the DOM despite React's fiber holding it.
  React.useEffect(() => {
    if (justAdded && ref.current) {
      ref.current.classList.add('tile-just-added');
      const tid = setTimeout(() => {
        if (ref.current) ref.current.classList.remove('tile-just-added');
      }, 900);
      return () => clearTimeout(tid);
    }
  }, [justAdded]);

  return (
    <div ref={ref} onClick={onClick} onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      style={{
        display: 'flex', flexDirection: 'column',
        background: hov ? '#fdfaf3' : '#fff', border: '2px solid #1a1a1a', borderRadius: 14,
        padding: 12, boxShadow: hov ? '4px 4px 0 0 #1a1a1a' : '2px 2px 0 0 #1a1a1a',
        cursor: 'pointer', transition: 'all 160ms cubic-bezier(0.34,1.4,0.64,1)',
        transform: hov ? 'translate(-1px,-1px)' : 'none',
        gap: 10,
      }}>
      <div style={{ aspectRatio: '1 / 1', width: '100%', border: '2px solid #1a1a1a', borderRadius: 10, overflow: 'hidden', background: '#f5ede0', position: 'relative' }}>
        {image ? (
          <img src={image} alt={name} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}/>
        ) : (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ width: '70%', height: '70%' }}>
              <ItemGraphicSvg type={type}/>
            </div>
          </div>
        )}
      </div>
      <div style={{ fontWeight: 600, fontSize: 14, lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{name}</div>
    </div>
  );
}

function ScreenInventory({ onNav, onSelectItem }) {
  const { inventory, recentlyAddedName } = useData();
  // Build groups but keep the original index of each item so we can drill into
  // the right detail on click.
  const groups = TIMELINE_GROUPS.map(g => ({
    ...g,
    items: inventory
      .map((it, idx) => ({ ...it, idx }))
      .filter(it => g.states.includes(it.state)),
  })).filter(g => g.items.length > 0);

  return (
    <div data-screen-label="Inventory">
      <TopBar title="Inventory"
        action={<Button variant="primary" size="sm" onClick={() => onNav('add')}>+ Add Item</Button>}
      />
      <div style={{ padding: 32, display: 'flex', flexDirection: 'column', gap: 32 }}>
        {inventory.length === 0 ? (
          <div style={{
            background: '#fff', border: '2px solid #1a1a1a', borderRadius: 14,
            boxShadow: '2px 2px 0 0 #1a1a1a', padding: '20px 22px', maxWidth: 560,
          }}>
            <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 6 }}>No items yet.</div>
            <div style={{ color: '#5a544a', fontSize: 14, marginBottom: 14 }}>
              Add your first piece to start getting listing recommendations.
            </div>
            <Button variant="primary" onClick={() => onNav('add')}>+ Add Item</Button>
          </div>
        ) : groups.map(g => (
          <section key={g.key}>
            {/* Group header — glyph, title, divider */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14, paddingBottom: 10, borderBottom: '2px solid #1a1a1a' }}>
              <span style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 28, height: 28, borderRadius: 9999,
                background: g.accent, border: '2px solid #1a1a1a',
                fontSize: 13, fontWeight: 700,
              }}>{g.glyph}</span>
              <h2 style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 22, letterSpacing: '-0.02em', fontVariationSettings: '"SOFT" 50', lineHeight: 1.1 }}>{g.title}</h2>
            </div>
            {/* Tile grid */}
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
              gap: 16,
            }}>
              {g.items.map((item) => (
                <InventoryTile
                  key={item.idx}
                  {...item}
                  justAdded={recentlyAddedName === item.name}
                  onClick={() => { onSelectItem && onSelectItem(item.idx); onNav('item'); }}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { ScreenInventory });
