// ItemGraphic.jsx — placeholder garment illustrations for inventory items
// types: jacket, skirt, blazer, pants
// Exports to window: ItemGraphic, ItemGraphicSvg

// Bordered, fixed-size variant — for hero spots in the item detail screen.
function ItemGraphic({ type, size = 64 }) {
  const svgs = {
    jacket: (
      <svg viewBox="0 0 64 64" width={size} height={size} fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect width="64" height="64" rx="12" fill="#f5ede0"/>
        <path d="M16 14 L10 24 L10 50 L24 50 L24 34 L32 34 L40 34 L40 50 L54 50 L54 24 L48 14 L40 18 L32 22 L24 18 Z" fill="#8a8275" opacity="0.5"/>
        <path d="M24 18 L24 50 M40 18 L40 50" stroke="#fbf6ee" strokeWidth="1.5"/>
        <path d="M16 14 C18 20 22 22 24 18" stroke="#5a544a" strokeWidth="1.5" fill="none"/>
        <path d="M48 14 C46 20 42 22 40 18" stroke="#5a544a" strokeWidth="1.5" fill="none"/>
      </svg>
    ),
    skirt: (
      <svg viewBox="0 0 64 64" width={size} height={size} fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect width="64" height="64" rx="12" fill="#f5ede0"/>
        <path d="M22 16 L42 16 L50 52 L14 52 Z" fill="#8a8275" opacity="0.5"/>
        <line x1="22" y1="16" x2="42" y2="16" stroke="#5a544a" strokeWidth="2" strokeLinecap="round"/>
      </svg>
    ),
    blazer: (
      <svg viewBox="0 0 64 64" width={size} height={size} fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect width="64" height="64" rx="12" fill="#f5ede0"/>
        <path d="M18 12 L10 22 L10 52 L26 52 L26 32 L32 28 L38 32 L38 52 L54 52 L54 22 L46 12 L38 16 L32 20 L26 16 Z" fill="#8a8275" opacity="0.5"/>
        <path d="M26 16 L26 52 M38 16 L38 52" stroke="#fbf6ee" strokeWidth="1.5"/>
        <circle cx="32" cy="36" r="2" fill="#fbf6ee" opacity="0.7"/>
        <circle cx="32" cy="42" r="2" fill="#fbf6ee" opacity="0.7"/>
      </svg>
    ),
    pants: (
      <svg viewBox="0 0 64 64" width={size} height={size} fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect width="64" height="64" rx="12" fill="#f5ede0"/>
        <path d="M14 14 L50 14 L50 20 L42 20 L42 52 L36 52 L32 36 L28 52 L22 52 L22 20 L14 20 Z" fill="#8a8275" opacity="0.5"/>
        <line x1="14" y1="20" x2="50" y2="20" stroke="#5a544a" strokeWidth="1.5"/>
        <line x1="32" y1="20" x2="32" y2="36" stroke="#fbf6ee" strokeWidth="1.5"/>
      </svg>
    ),
  };
  return (
    <div style={{ width: size, height: size, border: '2px solid #1a1a1a', borderRadius: 12, overflow: 'hidden', flexShrink: 0 }}>
      {svgs[type] || <svg viewBox="0 0 64 64" width={size} height={size}><rect width="64" height="64" rx="12" fill="#f5ede0"/></svg>}
    </div>
  );
}

// Unbordered variant that fills its container — for inventory tile thumbnails.
function ItemGraphicSvg({ type }) {
  const svgs = {
    jacket: (
      <svg viewBox="0 0 64 64" preserveAspectRatio="xMidYMid meet" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M16 14 L10 24 L10 50 L24 50 L24 34 L32 34 L40 34 L40 50 L54 50 L54 24 L48 14 L40 18 L32 22 L24 18 Z" fill="#8a8275" opacity="0.6"/>
        <path d="M24 18 L24 50 M40 18 L40 50" stroke="#fbf6ee" strokeWidth="1.5"/>
        <path d="M16 14 C18 20 22 22 24 18" stroke="#5a544a" strokeWidth="1.5" fill="none"/>
        <path d="M48 14 C46 20 42 22 40 18" stroke="#5a544a" strokeWidth="1.5" fill="none"/>
      </svg>
    ),
    skirt: (
      <svg viewBox="0 0 64 64" preserveAspectRatio="xMidYMid meet" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M22 16 L42 16 L50 52 L14 52 Z" fill="#8a8275" opacity="0.6"/>
        <line x1="22" y1="16" x2="42" y2="16" stroke="#5a544a" strokeWidth="2" strokeLinecap="round"/>
      </svg>
    ),
    blazer: (
      <svg viewBox="0 0 64 64" preserveAspectRatio="xMidYMid meet" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M18 12 L10 22 L10 52 L26 52 L26 32 L32 28 L38 32 L38 52 L54 52 L54 22 L46 12 L38 16 L32 20 L26 16 Z" fill="#8a8275" opacity="0.6"/>
        <path d="M26 16 L26 52 M38 16 L38 52" stroke="#fbf6ee" strokeWidth="1.5"/>
        <circle cx="32" cy="36" r="2" fill="#fbf6ee" opacity="0.7"/>
        <circle cx="32" cy="42" r="2" fill="#fbf6ee" opacity="0.7"/>
      </svg>
    ),
    pants: (
      <svg viewBox="0 0 64 64" preserveAspectRatio="xMidYMid meet" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M14 14 L50 14 L50 20 L42 20 L42 52 L36 52 L32 36 L28 52 L22 52 L22 20 L14 20 Z" fill="#8a8275" opacity="0.6"/>
        <line x1="14" y1="20" x2="50" y2="20" stroke="#5a544a" strokeWidth="1.5"/>
        <line x1="32" y1="20" x2="32" y2="36" stroke="#fbf6ee" strokeWidth="1.5"/>
      </svg>
    ),
  };
  return svgs[type] || (
    <svg viewBox="0 0 64 64" preserveAspectRatio="xMidYMid meet" width="100%" height="100%">
      <rect x="18" y="14" width="28" height="36" rx="4" fill="#8a8275" opacity="0.5"/>
    </svg>
  );
}

Object.assign(window, { ItemGraphic, ItemGraphicSvg });
