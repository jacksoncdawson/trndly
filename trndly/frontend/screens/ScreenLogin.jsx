// ScreenLogin.jsx — demo login screen.
//
// DEMO AUTH SURFACE — calls useAuth().login(). The current login() always
// succeeds, so any (or empty) input takes the user into the app.
// Replace `login()` in auth.js with a real call to swap behavior.

function ScreenLogin() {
  const { login } = useAuth();
  const [email,    setEmail]    = React.useState('');
  const [password, setPassword] = React.useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    login(email, password); // demo: always succeeds
  };

  const inputStyle = {
    background: '#f0e7d5', border: '2px solid #1a1a1a', borderRadius: 12,
    padding: '12px 14px', fontSize: 15, fontFamily: 'var(--font-sans)',
    color: '#1a1a1a', outline: 'none', width: '100%',
    transition: 'background 120ms ease, box-shadow 160ms cubic-bezier(0.34,1.4,0.64,1)',
  };
  const focusOn  = e => { e.target.style.background = '#fff'; e.target.style.boxShadow = '4px 4px 0 0 #2d5e3e'; };
  const focusOff = e => { e.target.style.background = '#f0e7d5'; e.target.style.boxShadow = 'none'; };

  return (
    <div data-screen-label="Login" style={{
      minHeight: '100vh', background: '#fbf6ee',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 32,
    }}>
      <div style={{ width: '100%', maxWidth: 420, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 20 }}>

        {/* Brand lockup */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
          <img src="assets/brand-mark.svg" width="48" height="48"
               style={{ borderRadius: 12, border: '2px solid #1a1a1a', boxShadow: '2px 2px 0 0 #1a1a1a', display: 'block' }}/>
          <div style={{
            fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 36,
            letterSpacing: '-0.02em', fontVariationSettings: '"SOFT" 50',
          }}>trndly</div>
        </div>

        {/* Tagline */}
        <div style={{ fontSize: 14, color: '#5a544a', textAlign: 'center', marginBottom: 8 }}>
          Know what to stock. Know when to sell.
        </div>

        {/* Card */}
        <form onSubmit={handleSubmit} style={{
          background: '#fff', border: '2px solid #1a1a1a', borderRadius: 16,
          padding: 28, boxShadow: '4px 4px 0 0 #1a1a1a', width: '100%',
          display: 'flex', flexDirection: 'column', gap: 18,
        }}>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.07em', color: '#8a8275', marginBottom: 4 }}>Sign in</div>
            <h1 style={{
              fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 26,
              letterSpacing: '-0.02em', fontVariationSettings: '"SOFT" 50', lineHeight: 1.1,
            }}>Welcome back.</h1>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label htmlFor="login-email" style={{ fontSize: 14, fontWeight: 600 }}>Email</label>
            <input
              id="login-email" type="email" autoComplete="email"
              value={email} onChange={e => setEmail(e.target.value)}
              placeholder="you@example.com" style={inputStyle}
              onFocus={focusOn} onBlur={focusOff}
            />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label htmlFor="login-password" style={{ fontSize: 14, fontWeight: 600 }}>Password</label>
            <input
              id="login-password" type="password" autoComplete="current-password"
              value={password} onChange={e => setPassword(e.target.value)}
              placeholder="••••••••" style={inputStyle}
              onFocus={focusOn} onBlur={focusOff}
            />
          </div>

          <Button variant="primary" type="submit">Sign in ↗</Button>

          <div style={{ fontSize: 12, color: '#8a8275', textAlign: 'center', paddingTop: 4 }}>
            Demo build — any credentials sign you in.
          </div>
        </form>
      </div>
    </div>
  );
}

Object.assign(window, { ScreenLogin });
