// ScreenAdd.jsx — onboarding form for adding a new item to inventory.
//
// Demo flow: drop/upload an image → fill in name + cost + tags → submit
// → flash "Added! Getting prediction…" → auto-redirect to Inventory.
// The image upload is purely visual for the recording — the file is read
// into a local data-URL, never uploaded anywhere.

// Options sourced from window.LOOKUP_OPTIONS (loaded from data.js — mirrors lookup.csv).
const CATEGORY_CHOICES = {
  productType: LOOKUP_OPTIONS.productType,
  color:       LOOKUP_OPTIONS.color,
  material:    LOOKUP_OPTIONS.material,
  appearance:  LOOKUP_OPTIONS.appearance,
  gender:      LOOKUP_OPTIONS.gender,
};

const TAG_LABELS = {
  productType: 'Type', color: 'Color', material: 'Material', appearance: 'Appearance', gender: 'Gender',
};

// ItemGraphic only ships placeholder SVGs for a handful of types. Map the
// rich LOOKUP_OPTIONS.productType vocabulary onto the supported graphics so
// the inventory tile fallback always renders (for items without a photo).
const GRAPHIC_TYPE_MAP = {
  'Trousers': 'pants', 'Outdoor trousers': 'pants', 'Shorts': 'pants',
  'Leggings/Tights': 'pants', 'Jeans': 'pants',
  'Jacket': 'jacket', 'Coat': 'jacket',
  'Blazer': 'blazer', 'Tailored Waistcoat': 'blazer',
  'Skirt': 'skirt', 'Dress': 'skirt',
};
function graphicTypeFor(productType) {
  return GRAPHIC_TYPE_MAP[productType] || 'pants';
}

// Drag-and-drop image dropzone. Holds local data-URL preview only.
function ImageDropzone({ image, onChange }) {
  const inputRef = React.useRef(null);
  const [dragOver, setDragOver] = React.useState(false);

  const readFile = (file) => {
    if (!file || !file.type.startsWith('image/')) return;
    const reader = new FileReader();
    reader.onload = (e) => onChange(e.target.result);
    reader.readAsDataURL(file);
  };

  const onDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) readFile(e.dataTransfer.files[0]);
  };
  const onPick = (e) => { if (e.target.files && e.target.files[0]) readFile(e.target.files[0]); };

  return (
    <div
      onClick={() => inputRef.current && inputRef.current.click()}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
      role="button" tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current && inputRef.current.click(); }}}
      style={{
        position: 'relative', cursor: 'pointer',
        background: image ? '#fff' : (dragOver ? '#fdfaf3' : '#f5ede0'),
        border: '2px solid #1a1a1a', borderRadius: 14,
        boxShadow: dragOver ? '4px 4px 0 0 #2d5e3e' : '2px 2px 0 0 #1a1a1a',
        padding: image ? 0 : 28,
        minHeight: 160, display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', gap: 8,
        textAlign: 'center', overflow: 'hidden',
        transition: 'background 120ms ease, box-shadow 160ms cubic-bezier(0.34,1.4,0.64,1)',
      }}
    >
      <input ref={inputRef} type="file" accept="image/*" onChange={onPick} style={{ display: 'none' }}/>

      {image ? (
        <>
          <img src={image} alt="Uploaded item"
               style={{ display: 'block', width: '100%', maxHeight: 280, objectFit: 'contain', background: '#fbf6ee' }}/>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onChange(null); }}
            style={{
              position: 'absolute', top: 10, right: 10,
              background: '#fff', border: '2px solid #1a1a1a', borderRadius: 9999,
              padding: '4px 10px', fontSize: 11, fontWeight: 700, cursor: 'pointer',
              boxShadow: '2px 2px 0 0 #1a1a1a', fontFamily: 'var(--font-sans)',
            }}
          >Replace</button>
        </>
      ) : (
        <>
          <div style={{
            width: 44, height: 44, display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: '#fff', border: '2px solid #1a1a1a', borderRadius: 9999, boxShadow: '2px 2px 0 0 #1a1a1a',
          }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#1a1a1a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 5v14"/><path d="M5 12h14"/>
            </svg>
          </div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Drop a photo here</div>
          <div style={{ fontSize: 12, color: '#8a8275' }}>or click to choose a file</div>
        </>
      )}
    </div>
  );
}

function ScreenAdd({ onNav }) {
  const { addItem }           = useData();
  const [name, setName]       = React.useState('');
  const [cost, setCost]       = React.useState('');
  const [tags, setTags]       = React.useState({});
  const [image, setImage]     = React.useState(null);     // data-URL or null
  const [submitted, setSubmitted] = React.useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!name || !cost) return;

    // Derive the item's per-feature signals + recommendation from the picked
    // tags, looking each value up against TREND_DATA. This is the seam where
    // a real ML prediction would replace the static lookup.
    const signals        = buildSignalsFromTags(tags);
    const recommendation = deriveRecommendation(signals);

    addItem({
      name,
      color: tags.color || '',
      type:  graphicTypeFor(tags.productType),
      cost:  cost.startsWith('$') ? cost : `$${cost}`,
      added: 'added today',
      state: recommendation,
      image,
      signals,
    });

    setSubmitted(true);
    setTimeout(() => {
      setSubmitted(false); setName(''); setCost(''); setTags({}); setImage(null);
      onNav('inventory');
    }, 1200);
  };

  const inputStyle = {
    background: '#f0e7d5', border: '2px solid #1a1a1a', borderRadius: 12,
    padding: '12px 14px', fontSize: 15, fontFamily: 'var(--font-sans)',
    color: '#1a1a1a', outline: 'none', width: '100%',
  };

  return (
    <div data-screen-label="Add Piece">
      {/* Header with back-button breadcrumb */}
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '20px 32px 16px', borderBottom: '2px solid #1a1a1a', background: '#fbf6ee', position: 'sticky', top: 0, zIndex: 50 }}>
        <button
          onClick={() => onNav('inventory')}
          style={{ width: 34, height: 34, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#fff', border: '2px solid #1a1a1a', borderRadius: 9999, cursor: 'pointer', boxShadow: '2px 2px 0 0 #1a1a1a', flexShrink: 0, transition: 'all 160ms cubic-bezier(0.34,1.4,0.64,1)' }}
          onMouseEnter={e => { e.currentTarget.style.transform = 'translate(-1px,-1px)'; e.currentTarget.style.boxShadow = '4px 4px 0 0 #1a1a1a'; }}
          onMouseLeave={e => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = '2px 2px 0 0 #1a1a1a'; }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#1a1a1a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
        </button>
        <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 28, letterSpacing: '-0.02em', fontVariationSettings: '"SOFT" 50' }}>Add Item</h1>
      </header>

      <form onSubmit={handleSubmit} style={{ padding: 32, maxWidth: 600 }}>
        {submitted && (
          <div style={{ background: '#d8e7dc', border: '2px solid #1a1a1a', borderRadius: 14, padding: '14px 16px', marginBottom: 24, fontWeight: 600, color: '#2d5e3e', boxShadow: '2px 2px 0 0 #1a1a1a' }}>
            ↗ Added! Getting prediction…
          </div>
        )}

        {/* Image dropzone */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 24 }}>
          <label style={{ fontSize: 15, fontWeight: 600 }}>Photo</label>
          <ImageDropzone image={image} onChange={setImage}/>
        </div>

        {/* Top-line fields */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 24 }}>
          <div style={{ gridColumn: '1/-1', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label style={{ fontSize: 15, fontWeight: 600 }}>Item name</label>
            <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="What did you find?" style={inputStyle}
              onFocus={e => { e.target.style.background = '#fff'; e.target.style.boxShadow = '4px 4px 0 0 #2d5e3e'; }}
              onBlur={e => { e.target.style.background = '#f0e7d5'; e.target.style.boxShadow = 'none'; }}
            />
          </div>
          <div style={{ gridColumn: '1/-1', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label style={{ fontSize: 15, fontWeight: 600 }}>What did you pay?</label>
            <input type="text" value={cost} onChange={e => setCost(e.target.value)} placeholder="$" style={{ ...inputStyle, maxWidth: 240 }}
              onFocus={e => { e.target.style.background = '#fff'; e.target.style.boxShadow = '4px 4px 0 0 #2d5e3e'; }}
              onBlur={e => { e.target.style.background = '#f0e7d5'; e.target.style.boxShadow = 'none'; }}
            />
          </div>
        </div>

        {/* Detail tags — pillbox dropdowns */}
        <div style={{ marginBottom: 28 }}>
          <label style={{ fontSize: 15, fontWeight: 600, display: 'block', marginBottom: 12 }}>Details</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {Object.entries(TAG_LABELS).map(([key, label]) => {
              const sel = tags[key];
              return (
                <div key={key} style={{ position: 'relative' }}>
                  <select
                    value={sel || ''}
                    onChange={e => setTags(t => ({ ...t, [key]: e.target.value || undefined }))}
                    style={{
                      appearance: 'none', display: 'inline-flex', alignItems: 'center', gap: 6,
                      padding: '8px 28px 8px 14px', borderRadius: 9999, fontSize: 13, fontWeight: 600,
                      border: '2px solid #1a1a1a', cursor: 'pointer',
                      background: sel ? '#2d5e3e' : '#fff', color: sel ? '#fbf6ee' : '#1a1a1a',
                      boxShadow: '2px 2px 0 0 #1a1a1a', fontFamily: 'var(--font-sans)',
                    }}
                  >
                    <option value="">+ {label}</option>
                    {(CATEGORY_CHOICES[key] || []).map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                  <span style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', fontSize: 10, color: sel ? '#fbf6ee' : '#8a8275' }}>▼</span>
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <Button variant="primary" type="submit">Get prediction ↗</Button>
          <button type="button" onClick={() => onNav('inventory')} style={{ fontSize: 14, color: '#8a8275', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'var(--font-sans)' }}>Cancel</button>
        </div>
      </form>
    </div>
  );
}

Object.assign(window, { ScreenAdd });
