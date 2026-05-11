// auth.js — demo auth module (window-scoped, no build step).
//
// ─────────────────────────────────────────────────────────────────
// DEMO AUTH — replace this whole file with a real auth client.
// Public surface (the contract App.jsx + Sidebar.jsx consume):
//   useAuth()   → { user, login(email, password), logout() }
//   AuthProvider({ children })
//   user object: { name: string, email: string }
//
// Notes for refactoring to real auth:
//   - Swap `login` for a fetch to /auth/login (POST).
//   - Persist the user via cookie/JWT instead of in-memory state.
//   - Read identity from server on mount; gate routes on real session.
// ─────────────────────────────────────────────────────────────────

// Single React context. Babel-in-browser doesn't support modules, so we
// rely on the script load order in index.html (auth.js loads after React).
const AuthContext = React.createContext(null);

// The demo "user" returned after any successful login attempt.
const DEMO_USER = { name: 'Demo User', email: 'demo@trndly.com' };

function AuthProvider({ children }) {
  // null = signed out, object = signed in.
  const [user, setUser] = React.useState(null);

  // Demo login: any input "works". Returns a promise so a future real impl
  // can be drop-in async. The form doesn't even need to call login() with
  // valid creds — pressing the submit button always lands you in the app.
  const login = (_email, _password) => {
    setUser(DEMO_USER);
    return Promise.resolve(DEMO_USER);
  };

  const logout = () => setUser(null);

  return (
    <AuthContext.Provider value={{ user, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

function useAuth() {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}

Object.assign(window, { AuthProvider, useAuth });
