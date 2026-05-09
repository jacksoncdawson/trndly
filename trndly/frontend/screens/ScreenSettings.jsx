// ScreenSettings.jsx — placeholder settings screen.

function ScreenSettings() {
  const sections = [
    { title: 'Account',       hint: 'Profile, email, sign-in.' },
    { title: 'Notifications', hint: 'Trend alerts, listing reminders.' },
    { title: 'Data sources',  hint: 'Connected scrapers and signal feeds.' },
    { title: 'Appearance',    hint: 'Theme and density.' },
  ];

  return (
    <div data-screen-label="Settings">
      <TopBar title="Settings"/>

      <div style={{ padding: 32, maxWidth: 720, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{
          background: '#f8ebc9', border: '2px solid #1a1a1a', borderRadius: 16,
          padding: '14px 16px', boxShadow: '2px 2px 0 0 #1a1a1a',
          fontSize: 14, fontWeight: 600, color: '#1a1a1a',
        }}>
          Settings is a placeholder for now — none of the controls below are wired up.
        </div>

        {sections.map(s => (
          <div key={s.title} style={{
            background: '#fff', border: '2px solid #1a1a1a', borderRadius: 16,
            padding: 20, boxShadow: '2px 2px 0 0 #1a1a1a',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16,
          }}>
            <div>
              <div style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 18, fontVariationSettings: '"SOFT" 50' }}>{s.title}</div>
              <div style={{ fontSize: 13, color: '#8a8275', marginTop: 4 }}>{s.hint}</div>
            </div>
            <span style={{
              fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.08em',
              padding: '4px 10px', borderRadius: 9999,
              background: '#f0e7d5', border: '2px solid #1a1a1a', color: '#5a544a',
            }}>coming soon</span>
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { ScreenSettings });
