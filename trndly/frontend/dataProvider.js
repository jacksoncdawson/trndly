// dataProvider.js — session-scoped store for inventory + signals,
// with API-backed trends + options served via SWR.
//
// ─────────────────────────────────────────────────────────────────
// Public surface (the contract screens consume):
//   useData() → {
//     inventory, signals, recentlyAddedName,    // session state
//     addItem(item),                             // mutator
//     trends, options, lookupIds,                // API-backed (SWR)
//     trendsLoading, optionsLoading,
//   }
//   <DataProvider>
//
// Trends and options are fetched from the FastAPI service; while loading
// (or on error) we fall back to the seed mocks from data.js so the UI
// always has something to render.
//
// Adds persist for the browser session only — refreshing the page resets
// to the seed `INVENTORY_DATA`/`INVENTORY_SIGNALS` from data.js. To make
// inventory durable, swap the useState seeds for an API fetch and route
// addItem through POST /api/inventory.
// ─────────────────────────────────────────────────────────────────

const DataContext = React.createContext(null);

// SWR is loaded as UMD in index.html → exposes window.SWR.useSWR.
const useSWR = (window.SWR && (window.SWR.default || window.SWR.useSWR)) || null;

function _safeSWR(key, fetcher, options) {
  // If SWR didn't load (CDN failure, offline dev), return a no-data shape
  // so the rest of the provider still renders against mock fallbacks.
  if (!useSWR) return { data: undefined, error: undefined, isLoading: false };
  return useSWR(key, fetcher, options);
}

function DataProvider({ children }) {
  const [inventory, setInventory] = React.useState(() => INVENTORY_DATA.slice());
  const [signals,   setSignals]   = React.useState(() => Object.assign({}, INVENTORY_SIGNALS));
  // Name of the most recently added item — used to animate its tile on the
  // inventory grid. Auto-clears after a few seconds so it doesn't re-animate
  // on subsequent visits within the same session.
  const [recentlyAddedName, setRecentlyAddedName] = React.useState(null);

  // ─── API-backed trends + options ─────────────────────────────────────
  const trendsRes  = _safeSWR('/trends',  apiFetcher, { revalidateOnFocus: false });
  const optionsRes = _safeSWR('/options', apiFetcher, { revalidateOnFocus: false });

  const trends = React.useMemo(() => {
    if (trendsRes.data) return mapTrendsToTrendData(trendsRes.data);
    // Fallback: hand-authored mock from data.js.
    return TREND_DATA;
  }, [trendsRes.data]);

  const optionsState = React.useMemo(() => {
    if (optionsRes.data) {
      return {
        options:    mapOptionsToLookupOptions(optionsRes.data),
        lookupIds:  indexOptionsById(optionsRes.data),
      };
    }
    return { options: LOOKUP_OPTIONS, lookupIds: {} };
  }, [optionsRes.data]);

  // Keep the legacy window globals in sync with whatever's currently loaded
  // — some screens still read them directly; refactoring those is a
  // follow-up. SWR handles re-rendering when data changes so the screens
  // see the fresh values on the next render cycle.
  React.useEffect(() => {
    if (trendsRes.data) window.TREND_DATA = trends;
  }, [trends]);
  React.useEffect(() => {
    if (optionsRes.data) window.LOOKUP_OPTIONS = optionsState.options;
  }, [optionsState.options]);

  const addItem = React.useCallback((item) => {
    setInventory(prev => [item, ...prev]);
    if (item.signals) {
      setSignals(prev => ({ ...prev, [item.name]: item.signals }));
    }
    setRecentlyAddedName(item.name);
    setTimeout(() => setRecentlyAddedName(curr => curr === item.name ? null : curr), 4000);
  }, []);

  const value = {
    inventory,
    signals,
    recentlyAddedName,
    addItem,
    trends,
    options:        optionsState.options,
    lookupIds:      optionsState.lookupIds,
    trendsLoading:  trendsRes.isLoading,
    optionsLoading: optionsRes.isLoading,
    trendsError:    trendsRes.error,
    optionsError:   optionsRes.error,
  };

  return (
    <DataContext.Provider value={value}>
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
