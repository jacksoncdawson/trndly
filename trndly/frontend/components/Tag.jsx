// Tag.jsx — small pill badge for categories, tags, and chips
// Variants: trend states (rising/peak/falling/flat) + pop palette (rust/sky/plum/sage/mustard)
// Exports to window: Tag

const tagStyles = {
  base: {
    display: 'inline-flex', alignItems: 'center', gap: 4,
    padding: '4px 10px', borderRadius: 9999,
    fontSize: 13, fontWeight: 600,
    border: '2px solid #1a1a1a',
    background: '#ffffff', color: '#1a1a1a',
    fontFamily: 'var(--font-sans)',
    lineHeight: 1.2,
  },
  rising:  { background: '#d8e7dc' },
  peak:    { background: '#f8ebc9' },
  falling: { background: '#f5d6d0' },
  flat:    { background: '#ece2cf', color: '#5a544a' },
  rust:    { background: '#f5dccd' },
  sky:     { background: '#d9e6ed' },
  plum:    { background: '#e3d3e4' },
  sage:    { background: '#dde7d6' },
  mustard: { background: '#f8ebc9' },
};

function Tag({ variant = 'base', glyph, children, style }) {
  const s = { ...tagStyles.base, ...(tagStyles[variant] || {}), ...style };
  return (
    <span style={s}>
      {glyph && <span style={{ fontWeight: 700 }}>{glyph}</span>}
      {children}
    </span>
  );
}

Object.assign(window, { Tag });
