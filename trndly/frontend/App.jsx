// App.jsx — root web app shell. Wraps everything in <AuthProvider>; gates the
// authenticated app behind the login screen.
//
// DEMO AUTH GATE — `useAuth()` is the seam. Real auth swaps `auth.js` and this
// component still works as-is (login screen until `user` exists, then app shell).

function AuthenticatedApp() {
  const [screen, setScreen]       = React.useState('highlights');
  const [itemIndex, setItemIndex] = React.useState(0);

  const screens = {
    highlights: <ScreenHighlights/>,
    trends:     <ScreenTrends/>,
    inventory:  <ScreenInventory onNav={setScreen} onSelectItem={setItemIndex}/>,
    item:       <ScreenItem onNav={setScreen} index={itemIndex}/>,
    add:        <ScreenAdd onNav={setScreen}/>,
    settings:   <ScreenSettings/>,
  };

  // Sub-screens (item, add) keep "inventory" highlighted in the sidebar.
  const navMap = {
    highlights: 'highlights',
    trends:     'trends',
    inventory:  'inventory',
    item:       'inventory',
    add:        'inventory',
    settings:   'settings',
  };

  return (
    <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'stretch' }}>
      <Sidebar active={navMap[screen] || 'highlights'} onNav={setScreen}/>
      <main style={{ flex: 1, minWidth: 0 }}>
        {screens[screen] || screens.highlights}
      </main>
    </div>
  );
}

function App() {
  const { user } = useAuth();
  return user ? <AuthenticatedApp/> : <ScreenLogin/>;
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <AuthProvider>
    <DataProvider>
      <App/>
    </DataProvider>
  </AuthProvider>
);
