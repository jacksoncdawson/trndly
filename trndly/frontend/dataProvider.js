// dataProvider.js — session-scoped store for inventory + signals.
//
// ─────────────────────────────────────────────────────────────────
// DEMO DATA STORE — replace with API-backed state when wiring real services.
// Public surface (the contract screens consume):
//   useData() → { inventory, signals, addItem(item) }
//   <DataProvider>
// Each item: { name, color, type, cost, added, state, image?, signals? }
//
// Adds persist for the browser session only — refreshing the page resets
// to the seed `INVENTORY_DATA`/`INVENTORY_SIGNALS` from data.js. That's fine
// for the demo recording. To make this durable, swap the useState seeds
// for an API fetch and route addItem through POST /api/inventory.
// ─────────────────────────────────────────────────────────────────

const DataContext = React.createContext(null);

function DataProvider({ children }) {
  const [inventory, setInventory] = React.useState(() => INVENTORY_DATA.slice());
  const [signals,   setSignals]   = React.useState(() => Object.assign({}, INVENTORY_SIGNALS));
  // Name of the most recently added item — used to animate its tile on the
  // inventory grid. Auto-clears after a few seconds so it doesn't re-animate
  // on subsequent visits within the same session.
  const [recentlyAddedName, setRecentlyAddedName] = React.useState(null);

  const addItem = React.useCallback((item) => {
    setInventory(prev => [item, ...prev]);
    if (item.signals) {
      setSignals(prev => ({ ...prev, [item.name]: item.signals }));
    }
    setRecentlyAddedName(item.name);
    setTimeout(() => setRecentlyAddedName(curr => curr === item.name ? null : curr), 4000);
  }, []);

  return (
    <DataContext.Provider value={{ inventory, signals, recentlyAddedName, addItem }}>
      {children}
    </DataContext.Provider>
  );
}

function useData() {
  const ctx = React.useContext(DataContext);
  if (!ctx) throw new Error('useData must be used inside <DataProvider>');
  return ctx;
}

Object.assign(window, { DataProvider, useData });
