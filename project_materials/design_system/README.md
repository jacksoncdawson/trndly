# trndly Design System

**Version:** v1.0 · **Updated:** 2026-05-01 · **Owner:** jack
**Surface:** Desktop web app

---

## What is trndly?

trndly is a friendly tool for resellers (Depop, Poshmark, vintage shops) that does two things:

1. **Trend prediction.** Browse popularity for clothing features — colors, materials, product types, eras. See the past six months and the predicted next three. Quickly figure out: is burgundy still rising? Is Y2K denim cooling off?
2. **Listing timeline.** For each item in your inventory, get a per-item recommendation: list now, in a month, in three months — based on what we predict you'll earn at each point.

Backend concepts (model run IDs, confidence scores) are deliberately kept off the user surface. Users see decisions, not infrastructure.

---

## CONTENT FUNDAMENTALS

### Voice & Tone

**Friendly, considered, plain-spoken.** Think of a thoughtful friend who's been reselling for ten years — showing you their notebook. They don't lecture, they don't hedge, they don't talk like a hedge fund.

### Rules

- **Short sentences.** "Linen is at peak. Sell now." Never padded.
- **Verbs, not jargon.** "List in 1 month" — not "Optimal listing window: T+30."
- **Numbers come first.** "$84" before any explanation of what it means.
- **Sentence case for prose.** Lowercase for system labels ("rising", "peak"). Title Case only for proper nouns and section headlines.
- **Never reveal the model.** No "confidence: 0.78", no model versions. Users see "rising" and a chart.
- **No emoji.** The brand uses unicode symbols (↗ ● ↘ →) as trend-state glyphs, not emoji.
- **First-person is avoided** in system copy — the product speaks plainly ("List now" not "We recommend you list now").

### Examples of good copy

| Context | Good | Bad |
|---|---|---|
| Trend state label | "rising" | "Trend Status: RISING" |
| Recommendation | "Hold for 1 month, then list" | "Optimal listing window: T+30 days" |
| Hero headline | "Know what to stock. Know when to sell." | "AI-powered reseller insights platform" |
| Metric label | "est. return" | "Estimated Return on Investment" |
| Action | "Add reminder" | "Schedule Listing Notification" |

---

## VISUAL FOUNDATIONS

### Color System — Cream paper palette

Backgrounds are warm cream (`#fbf6ee`), not white. Cards sit slightly lighter than the bg (`#ffffff`) to feel like pinned notes on paper.

- **Surfaces:** `--color-bg` (#fbf6ee) → `--color-surface-1` (#fff) → `--color-surface-2` (#fdfaf3) → `--color-surface-3` (#f5ede0) → `--color-surface-sunk` (#f0e7d5 for inputs)
- **Text:** Off-black (`#1a1a1a`) primary, warm brown-grey secondary (`#5a544a`), muted tertiary (`#8a8275`)
- **Brand primary:** Deep forest green (`#2d5e3e`) — primary buttons, rising trend state, predicted data on charts
- **Pop palette:** 6 earthy, saturated colors — rust, mustard, coral, sky, plum, sage
- **Trend states (reserved meaning — never reuse for other purposes):**
  - Forest (`#2d5e3e`) = **rising** — stock up, popularity climbing
  - Mustard (`#e8b840`) = **peak** — sell now, at maximum
  - Rust (`#c64a3a`) = **falling** — declining, list immediately
  - Warm grey (`#8a8275`) = **flat** — stable

### Typography

Two families:

1. **Fraunces** — soft, slightly chunky variable serif. All headlines, big numbers (prices, returns, metrics). Always `font-weight: 800` with `font-variation-settings: "SOFT" 50`. Google Fonts.
2. **Inter** — geometric sans. All body text, labels, UI chrome. From rsms.me (variable). JetBrains Mono for monospaced metadata.

Type scale: 1.25 major-third anchored at 16px body — 11 / 13 / 15 / 16 / 18 / 22 / 28 / 36 / 48 / 64px.

**Eyebrow labels** are all-caps Inter, extra letter-spacing, on a filled black pill (`border-radius: full`).

### Spacing

4px base grid. Scale: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96px (`--space-1` through `--space-9`).

### Borders

**Bold black outlines are a brand signature.** `2px solid #1a1a1a` on cards, buttons, badges, inputs. `1px solid #e6dcc8` for soft hairline dividers only. Thickness is non-negotiable.

### Elevation / Shadows

**Hard offset "stamp" shadows** — no blur. Feels like risograph printing.

- `--shadow-xs`: 1px 1px 0 0 black
- `--shadow-sm`: 2px 2px 0 0 black — default card
- `--shadow-md`: 4px 4px 0 0 black — panels, hover state
- `--shadow-lg`: 6px 6px 0 0 black

On hover, buttons translate `(-1px, -1px)` and shadow grows from sm → md. On press, `(+1px, +1px)` and shadow shrinks.

### Corner Radii

Generous rounding. Cards: `--radius-lg` (16px). Buttons + tags: `--radius-full` (pills). Inputs: `--radius-md` (12px).

### Cards

White (`#fff`) on cream bg. `2px solid #1a1a1a`. `--shadow-sm`. `--radius-lg`. Padding `--space-5` (24px). They feel like index cards pinned to a corkboard.

### Backgrounds & Surfaces

Flat cream — no gradients, no textures, no patterns. Item thumbnails may use simple gradients as color placeholders. Page bg is always `--color-bg`.

### Animation & Motion

Slightly springy on enter (`cubic-bezier(0.34, 1.4, 0.64, 1)` — light overshoot), snappy on exit. Durations: 80ms / 160ms / 240ms / 400ms.

### Iconography

- **Trend-state glyphs:** `↗` (rising), `●` (peak), `↘` (falling), `→` (flat) — unicode characters as inline text.
- **Star glyph:** `★` for "list now" / recommendation contexts.
- **No icon library locked in.** Sidebar icons are inlined in `Sidebar.jsx`. Adopt Lucide when expanding.

---

## FILE INDEX

```
trndly Design System/
├── README.md                      ← this file
├── SKILL.md                       ← agent skill definition
├── tokens.css                     ← design tokens (source of truth CSS vars)
├── tokens.json                    ← design tokens (machine-readable)
├── colors_and_type.css            ← semantic CSS vars + pre-composed type styles
│
├── assets/
│   ├── brand-mark.svg             ← brand square, forest bg + cream "t"
│   └── brand-mark-light.svg       ← inverted, cream bg + black "t"
│
├── preview/                       ← design system reference cards (one concept per file)
│   ├── brand-logo.html
│   ├── brand-voice.html
│   ├── colors-surfaces.html
│   ├── colors-brand-pop.html
│   ├── colors-trend-states.html
│   ├── colors-chart.html
│   ├── type-display.html
│   ├── type-sans.html
│   ├── type-mono.html
│   ├── spacing-scale.html
│   ├── spacing-radii.html
│   ├── shadows-elevation.html
│   ├── comp-buttons.html
│   ├── comp-tags-badges.html
│   ├── comp-inputs.html
│   ├── comp-cards.html
│   └── comp-trend-chip.html
│
└── ui_kits/
    └── web/                       ← desktop web app prototype
        ├── README.md
        ├── index.html             ← entry point
        ├── App.jsx                ← root shell + routing
        ├── data.js                ← shared mock data
        ├── components/
        │   ├── Sidebar.jsx        ← left rail nav + sticky TopBar
        │   ├── Button.jsx
        │   ├── Tag.jsx
        │   ├── TrendCard.jsx      ← + TrendChip, ChartSparkline
        │   ├── Chart.jsx          ← HighlightSparkline, ItemPopularityChart, ChartLegend, SectionLabel
        │   └── ItemGraphic.jsx    ← placeholder garment illustrations
        └── screens/
            ├── ScreenHighlights.jsx
            ├── ScreenTrends.jsx
            ├── ScreenInventory.jsx
            ├── ScreenItem.jsx
            └── ScreenAdd.jsx
```

## Handoff notes (for engineering)

- **Tokens are the contract.** Use CSS vars from `tokens.css` / `colors_and_type.css`. Don't hardcode hexes in production code (the kit's JSX inlines them only for Babel-in-browser convenience).
- **No mobile.** This kit is desktop-only; previous mobile prototypes have been removed.
- **Real photography is missing.** Item thumbnails use `ItemGraphic` placeholder SVGs. Wire to real CDN URLs when ready.
- **No icon library committed.** Adopt Lucide (MIT, stroke-based) for any expansion beyond what's inlined in `Sidebar.jsx`.
- **Real chart library.** The current sparklines and popularity chart are hand-rolled SVG suitable for prototypes. For production, swap to Recharts/Visx with the same color/stroke conventions documented above.
