// Sidebar.jsx — left-rail navigation for the trndly web app
// Exports to window: Sidebar, SidebarIcon, TopBar

const SIDEBAR_ICONS = {
  Sparkles:   (c,s) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4 M22 5h-4 M4 17v2 M5 18H3"/></svg>,
  TrendingUp: (c,s) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>,
  Package:    (c,s) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M16.5 9.4L7.55 4.24"/><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 002 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/><path d="M3.27 6.96L12 12.01l8.73-5.05"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>,
  Plus:       (c,s) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth="2.5" strokeLinecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>,
  Settings:   (c,s) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>,
};

function SidebarIcon({ name, size = 20, color = 'currentColor' }) {
  const fn = SIDEBAR_ICONS[name];
  if (!fn) return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2"><circle cx="12" cy="12" r="4"/></svg>;
  return fn(color, size);
}

const SIDEBAR_NAV = [
  { id: 'highlights', label: 'Highlights', icon: 'Sparkles' },
  { id: 'inventory',  label: 'Inventory',  icon: 'Package' },
  { id: 'trends',     label: 'Trends',     icon: 'TrendingUp' },
  { id: 'settings',   label: 'Settings',   icon: 'Settings' },
];

// Visual metadata for the API status pill — green/amber/red dot + label.
const API_STATUS_META = {
  ok:         { dot: '#2d5e3e' },
  degraded:   { dot: '#c98e1f' },
  down:       { dot: '#c64a3a' },
  connecting: { dot: '#b5ad9c' },
  unknown:    { dot: '#b5ad9c' },
};

function ApiStatusPill() {
  const { health, healthError, healthLoading } = useData();
  let state, label;
  if (healthError) {
    state = 'down';
    label = 'API · offline';
  } else if (healthLoading && !health) {
    state = 'connecting';
    label = 'API · connecting';
  } else if (health && health.status === 'healthy') {
    state = 'ok';
    label = `API · ${health.predictions_anchor_month || 'live'}`;
  } else if (health && health.status === 'degraded') {
    state = 'degraded';
    label = 'API · degraded';
  } else {
    state = 'unknown';
    label = 'API · unknown';
  }
  const meta = API_STATUS_META[state];
  const tooltip = (() => {
    try { return JSON.stringify(health || (healthError && { error: healthError.message }) || {}, null, 2); }
    catch { return label; }
  })();

  return (
    <div title={tooltip} style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '4px 10px', borderRadius: 9999,
      background: '#fff', border: '1.5px solid #1a1a1a',
      fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, monospace)',
      fontSize: 11, fontWeight: 600, color: '#5a544a',
      maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
    }}>
      <span style={{ width: 8, height: 8, borderRadius: 9999, background: meta.dot, flexShrink: 0 }}/>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
    </div>
  );
}

function Sidebar({ active, onNav }) {
  // DEMO AUTH — read user + logout from auth context. Replace the auth module,
  // not this component, when wiring real auth.
  const { user, logout } = useAuth();
  const initials = (user && user.name)
    ? user.name.split(' ').map(s => s[0]).join('').slice(0, 2).toUpperCase()
    : '·';

  return (
    <aside style={{
      width: 220, flexShrink: 0, background: '#fff',
      borderRight: '2px solid #1a1a1a',
      display: 'flex', flexDirection: 'column',
      height: '100vh', position: 'sticky', top: 0,
    }}>
      {/* Logo lockup */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '20px 20px 16px' }}>
        <img src="assets/brand-mark.svg" width="36" height="36"
             style={{ borderRadius: 10, border: '2px solid #1a1a1a', boxShadow: '2px 2px 0 0 #1a1a1a', display: 'block', flexShrink: 0 }}/>
        <div style={{
          fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 22,
          letterSpacing: '-0.02em', fontVariationSettings: '"SOFT" 50',
        }}>trndly</div>
      </div>

      {/* Primary nav */}
      <nav style={{ flex: 1, padding: '20px 14px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {SIDEBAR_NAV.map(item => {
          const isActive = active === item.id;
          return (
            <button key={item.id} onClick={() => onNav(item.id)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '10px 12px', borderRadius: 12,
                background: isActive ? '#2d5e3e' : '#fff',
                color: isActive ? '#fbf6ee' : '#5a544a',
                border: '2px solid #1a1a1a',
                boxShadow: '2px 2px 0 0 #1a1a1a',
                cursor: 'pointer', fontFamily: 'var(--font-sans)',
                fontSize: 14, fontWeight: isActive ? 700 : 500,
                transition: 'transform 160ms cubic-bezier(0.34,1.4,0.64,1), box-shadow 160ms cubic-bezier(0.2,0,0,1), background 120ms ease',
                width: '100%', textAlign: 'left',
              }}
              onMouseEnter={e => { e.currentTarget.style.transform = 'translate(-1px,-1px)'; e.currentTarget.style.boxShadow = '4px 4px 0 0 #1a1a1a'; }}
              onMouseLeave={e => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = '2px 2px 0 0 #1a1a1a'; }}
              onMouseDown={e => { e.currentTarget.style.transform = 'translate(1px,1px)'; e.currentTarget.style.boxShadow = '1px 1px 0 0 #1a1a1a'; }}
              onMouseUp={e => { e.currentTarget.style.transform = 'translate(-1px,-1px)'; e.currentTarget.style.boxShadow = '4px 4px 0 0 #1a1a1a'; }}
            >
              <SidebarIcon name={item.icon} size={18} color={isActive ? '#fbf6ee' : '#8a8275'}/>
              {item.label}
            </button>
          );
        })}
      </nav>

      {/* Add Item — primary CTA */}
      <div style={{ padding: '12px 10px 8px' }}>
        <button onClick={() => onNav('add')}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            width: '100%', padding: '10px 16px', borderRadius: 9999,
            background: '#2d5e3e', color: '#fbf6ee',
            border: '2px solid #1a1a1a', boxShadow: '2px 2px 0 0 #1a1a1a',
            cursor: 'pointer', fontFamily: 'var(--font-sans)',
            fontSize: 14, fontWeight: 600,
            transition: 'all 160ms cubic-bezier(0.34,1.4,0.64,1)',
          }}
          onMouseEnter={e => { e.currentTarget.style.transform = 'translate(-1px,-1px)'; e.currentTarget.style.boxShadow = '4px 4px 0 0 #1a1a1a'; }}
          onMouseLeave={e => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = '2px 2px 0 0 #1a1a1a'; }}
        >
          <SidebarIcon name="Plus" size={16} color="#fbf6ee"/>
          Add Item
        </button>
      </div>

      {/* API status pill — live read of /health, so you can see at a glance whether the forecast service is reachable. */}
      <div style={{ padding: '8px 10px 0' }}>
        <ApiStatusPill/>
      </div>

      {/* User pill — pinned to the bottom of the rail */}
      <div style={{ padding: '8px 10px 16px', borderTop: '1px solid #ece2cf', marginTop: 8 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '8px 10px', borderRadius: 12,
          background: '#fdfaf3', border: '2px solid #1a1a1a',
          boxShadow: '2px 2px 0 0 #1a1a1a',
        }}>
          <div style={{
            width: 28, height: 28, borderRadius: 9999,
            background: '#2d5e3e', color: '#fbf6ee',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 11, fontWeight: 700, letterSpacing: '.04em',
            border: '2px solid #1a1a1a', flexShrink: 0,
          }}>{initials}</div>
          <div style={{ flex: 1, minWidth: 0, lineHeight: 1.15 }}>
            <div style={{ fontSize: 13, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {user ? user.name : 'Guest'}
            </div>
            <button
              onClick={logout}
              style={{
                background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                fontFamily: 'var(--font-sans)', fontSize: 11, fontWeight: 600,
                color: '#8a8275', textDecoration: 'underline', textUnderlineOffset: 2,
              }}
            >Sign out</button>
          </div>
        </div>
      </div>
    </aside>
  );
}

// TopBar — the sticky title strip at the top of each main screen.
function TopBar({ title, action }) {
  return (
    <header style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '20px 32px 16px',
      borderBottom: '2px solid #1a1a1a',
      background: '#fbf6ee',
      position: 'sticky', top: 0, zIndex: 50,
    }}>
      <h1 style={{
        fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 28,
        letterSpacing: '-0.02em', fontVariationSettings: '"SOFT" 50', lineHeight: 1.1,
      }}>{title}</h1>
      {action && action}
    </header>
  );
}

Object.assign(window, { Sidebar, SidebarIcon, TopBar });
