# trndly — Frontend (Demo Build)

Desktop web app prototype for trndly: a tool for resellers (Depop, Poshmark,
vintage shops) that pairs trend prediction with per-item listing
recommendations. This is the **demo build** — wired with hand-authored data
and a no-op auth flow so the UI can be recorded end-to-end without any
backend running.

> **What "demo" means here.** Every section that simulates real behavior
> (auth, mock inventory, demo "Add Item" payload) is marked with a
> `DEMO …` comment block in source. When wiring to real services, replace
> those modules — the screens and components do not need to change.

---

## Quick start

No build step. The app uses Babel-in-browser to transpile JSX on load. Serve
the directory with any static server and open `index.html`:

```sh
# from trndly/frontend/
python3 -m http.server 8000
# → http://localhost:8000
```

VS Code's Live Server, `npx --yes serve .`, or any other static host works
equally well. Opening the file directly via `file://` will fail because
some asset loads (the brand SVG, fonts) need an HTTP origin.

### The recorded demo flow

1. **Sign in.** Any email + password (even empty) signs you in as `Demo User`.
2. **Highlights.** Landing screen — four curated callouts (rising / peak /
   sleeping / falling) drawn from `TREND_DATA`.
3. **Trends.** Filter all 19 features by category and state.
4. **Inventory.** Four pre-seeded items grouped by recommended listing window.
   Click any tile to drill into its detail screen.
5. **Add Item.** Drop a photo, type the name, pick the tags, submit → flash
   confirmation → auto-redirect to Inventory.
6. **Sign out.** Bottom of the sidebar; returns you to the login screen.

The recommended demo "new item" to add on camera is **Beige striped trousers**
(beige, stripe, trousers, women) — see `DEMO_ADD_ITEM` in `data.js`. All
three feature signals are rising → "list in 2+ months."

---

## File map

```
frontend/
├── index.html              ← entry point. Loads CSS, React, Babel, then JS in order.
├── App.jsx                 ← root: <AuthProvider> wraps the app; gates on login.
├── data.js                 ← DEMO DATA — trends, inventory, signals, lookups.
├── auth.js                 ← DEMO AUTH — context + always-succeeds login.
├── tokens.css              ← design tokens (CSS custom properties).
├── colors_and_type.css     ← semantic color + typography aliases.
├── README.md               ← this file.
│
├── assets/                 ← brand SVGs.
│
├── components/             ← reusable UI primitives.
│   ├── Sidebar.jsx         ← left nav rail + user pill + TopBar.
│   ├── Button.jsx          ← multi-variant button.
│   ├── Tag.jsx             ← pill badges.
│   ├── TrendCard.jsx       ← TrendCard + TrendChip + ChartSparkline.
│   ├── Chart.jsx           ← HighlightSparkline, ItemPopularityChart, ChartLegend, SectionLabel.
│   └── ItemGraphic.jsx     ← garment placeholder SVGs.
│
└── screens/                ← full-page views.
    ├── ScreenLogin.jsx     ← demo sign-in screen.
    ├── ScreenHighlights.jsx← landing: 4 curated callouts.
    ├── ScreenTrends.jsx    ← all features, filterable.
    ├── ScreenInventory.jsx ← user inventory grouped by listing window.
    ├── ScreenItem.jsx      ← per-item detail (chart + signal cards).
    ├── ScreenAdd.jsx       ← add new item form (with image dropzone).
    └── ScreenSettings.jsx  ← placeholder.
```

---

## How rendering works

There is no bundler. `index.html` loads everything in a deliberate order:

1. **Tokens / typography CSS** — design vars must exist before any component renders.
2. **React + ReactDOM (UMD)** — exposes `React`, `ReactDOM` globals.
3. **Babel Standalone** — transpiles every `<script type="text/babel">` block.
4. **`data.js`** — plain JS, attaches mock data to `window.*`.
5. **`auth.js`** — defines `AuthProvider` + `useAuth` on `window`.
6. **Components** (leaves before composites: `Tag`, `Button`, `Chart`,
   `ItemGraphic`, `TrendCard`, `Sidebar`).
7. **Screens** (`ScreenLogin` first, then the authenticated screens).
8. **`App.jsx`** — final render call.

Every module attaches its public surface to `window` (`Object.assign(window,
{...})`) so siblings can pick them up by global lookup. This is the cost of
not having a bundler. When migrating to Vite/CRA, swap each global write for
a real ESM `export`.

---

## Demo seams (what to replace when wiring real services)

These are the only files that contain demo-mode behavior. Refactoring them
should not require touching screens or components.

### 1. `auth.js` — demo authentication

- Exposes `<AuthProvider>` and `useAuth()`.
- `useAuth()` returns `{ user, login, logout }`. `user` is `null` when signed
  out, `{ name, email }` when signed in.
- `login()` always succeeds and sets the user to `{ name: 'Demo User',
email: 'demo@trndly.com' }` regardless of inputs.
- **To swap in real auth:** keep the same `useAuth()` shape. Replace the
  in-memory `useState(null)` with a real session check (cookie, JWT, OAuth
  callback). Replace `login()` with a `fetch('/auth/login', …)` round-trip.

The login screen (`ScreenLogin.jsx`) and Sidebar (`Sidebar.jsx`) are the only
consumers, and both go through `useAuth()` — they will not need changes.

### 2. `data.js` — demo data

The contract (field names + value vocabularies) is what the UI depends on.
Real data must match these shapes:

- **`TREND_DATA[]`** — `{ name, category, state, stat }`.
  - `category`: `'color' | 'material' | 'appearance' | 'product type' | 'gender'`
  - `state`: `'rising' | 'peak' | 'flat' | 'falling'`
  - `stat`: free-text forecast string (e.g. `'+38% next 6mo'`).
- **`INVENTORY_DATA[]`** — `{ name, color, type, cost, added, state }`.
  - `state`: `'list now' | 'falling' | 'hold 1mo' | 'hold 2mo'` — drives the
    grouping in `ScreenInventory.jsx` (see `TIMELINE_GROUPS`).
- **`INVENTORY_SIGNALS`** — keyed by inventory item name. Each value is an
  array of `{ label, value, state, category }` describing per-feature trends
  for the item. Used by `ScreenItem.jsx` for the "Signal breakdown" cards.
- **`STATE_META`** — visual treatment for each state (glyph + bg). Reserved
  meaning — do not reuse these colors elsewhere.
- **`LOOKUP_OPTIONS`** — option lists for the Add Item form, mirrored from
  `trndly/data/processed/lookup.csv`. Keep in sync with that file.
- **`DEMO_ADD_ITEM`** — the suggested "new item" for the recorded demo.
  Documents what to type when filling the form on camera, and what trend
  signals justify the recommendation.

When swapping to a real backend, replace the static arrays with API
responses that mirror these shapes (e.g. `GET /api/trends`,
`GET /api/inventory`, `GET /api/inventory/:id/signals`). The screens read
from `window.*` today; update them to read from a fetched store / context
provider.

### 3. `ScreenAdd.jsx` — image dropzone

The dropzone is intentionally non-network. It reads the file into a local
data URL via `FileReader` for visual preview only — nothing leaves the
browser. To wire to real storage:

1. Replace `setImage(dataUrl)` with an upload to your backend / S3.
2. Persist the resulting URL through to inventory creation.
3. Replace the 1.2-second `setTimeout` "Added! Getting prediction…" flash
   with the real submit/predict round-trip.

---

## Design-system consistency

Every chart curve, recommendation, and signal in this build is hand-tuned so
the demo tells an internally consistent story:

| Trend (TREND_DATA)                                 | State   | How it shows up                                                                         |
| -------------------------------------------------- | ------- | --------------------------------------------------------------------------------------- |
| Trousers (`+62%`)                                  | rising  | "Biggest mover" on Highlights · drives Olive cargo trousers → "hold 2mo" recommendation |
| Linen (`peak`)                                     | peak    | "At peak" on Highlights                                                                 |
| Hoodie (`stable`)                                  | flat    | "Sleeping low" on Highlights                                                            |
| Sequin (`-24%`)                                    | falling | "Sharpest drop" on Highlights                                                           |
| Denim (`-18%`)                                     | falling | Vintage denim jacket → "list now" (clear before further drop)                           |
| Skirt (`-12%`)                                     | falling | Tan suede mini skirt → "list now"                                                       |
| Beige (`+22%`) + Corduroy (`+41%`) + Blazer (peak) | mixed   | Beige corduroy blazer → "hold 1mo" (rising signals approaching the blazer peak)         |

The four sparkline shapes (`rising`, `peak`, `flat`, `falling`) are hard-coded
SVG paths in `components/Chart.jsx` — every place a state is rendered, the
curve and the label agree by construction.

---

## Conventions

- **Tokens are the contract.** Production code should reference CSS vars
  from `tokens.css` / `colors_and_type.css` rather than hard-coded hex
  values. The kit's JSX inlines hexes only because Babel-in-browser doesn't
  read CSS modules.
- **No mobile.** Desktop only.
- **No icon library committed.** Sidebar icons are inlined as SVG strings
  in `Sidebar.jsx`. Adopt Lucide (MIT, stroke-based) when expanding.
- **No real photography.** Item thumbnails use placeholder garment SVGs from
  `ItemGraphic.jsx`. Wire to a real CDN when ready.
- **Charts are hand-rolled SVG.** Suitable for the prototype; swap to
  Recharts / Visx for production while keeping the same color + stroke
  conventions.
- **Each screen carries `data-screen-label`** on its root element for
  tooling / screenshot diffing.

---

## Migration checklist (demo → production)

1. Replace `auth.js` with a real session client. `useAuth()` shape is the contract.
2. Replace `data.js` arrays with API responses — keep the field names.
3. Wire `ScreenAdd.jsx` image upload + form submit to real endpoints.
4. Move the no-build setup to Vite (or your bundler of choice). Convert each
   `Object.assign(window, {…})` to a real ESM `export`. Drop the
   `babel-standalone` script tag from `index.html`.
5. Adopt a chart library (Recharts/Visx). Replace the SVG primitives in
   `components/Chart.jsx` while preserving the past/now/predicted color
   conventions documented in the design system.
