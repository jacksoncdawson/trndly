# trndly API reference

The FastAPI service at `backend/services/scheduleServer.py` exposes a
small read-only API over the precomputed predictions parquet. All routes
are `GET`; no request triggers a model call.

Default base URL when running locally: `http://127.0.0.1:8000` (or the
port passed to uvicorn). The static UI mounts at `/ui/`; everything
below is same-origin so no CORS setup is required.

OpenAPI JSON: `GET /openapi.json` — Swagger UI: `GET /docs`.

---

## `GET /health`

Liveness + bundle status.

```bash
curl -s http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "predictions_loaded": true,
  "predictions_anchor_month": "2020-08",
  "predictions_univariate_rows": 182,
  "predictions_fingerprint_rows": 6461,
  "error": null
}
```

If `predictions_loaded` is `false`, `error` carries the load failure
reason (e.g., "no `predictions_*.parquet` found — run `python -m pipelines.monthly run`").

---

## `GET /options`

Vocabularies for UI dropdowns. Each category returns `[{name, id}]` so
the frontend can POST IDs (or assemble the fingerprint query string) by
mapping back from the user's name selection.

```bash
curl -s http://localhost:8000/options
```

```json
{
  "colors":      [{"name": "Beige", "id": 4}, {"name": "Black", "id": 1}, ...],
  "categories":  [{"name": "Alice band", "id": 49}, {"name": "Bag", "id": 50}, ...],
  "materials":   [{"name": "acrylic", "id": 32}, {"name": "canvas", "id": 14}, ...],
  "appearances": [{"name": "All over pattern", "id": 5}, ...],
  "genders":     [{"name": "Men", "id": 3}, {"name": "Unisex", "id": 2}, {"name": "Women", "id": 1}]
}
```

Source: `data/reference/lookup.csv`. Filters by `category` column to
the five dimensions the UI exposes (drops `color_spectrum` and
`product_group` — they're internal).

---

## `GET /trends`

Every univariate prediction row, optionally filtered. One row per
`(dimension, level_id)` from the latest predictions parquet.

**Query params:**
| Param | Type | Notes |
|---|---|---|
| `dimension` | str | optional. e.g. `color_master`, `material`, `product_type`, `graphical_appearance`, `gender`, `product_group`, `color_spectrum` |
| `state` | str | optional. one of `rising`, `peak`, `flat`, `falling` |

```bash
curl -s 'http://localhost:8000/trends?dimension=color_master&state=rising'
```

```json
[
  {
    "dimension": "color_master",
    "level_id": 4,
    "level_name": "Beige",
    "y_h1": 0.108, "y_h2": 0.111, "y_h3": 0.115, "y_h4": 0.118, "y_h5": 0.121, "y_h6": 0.124,
    "state": "rising",
    "stat": "+22% next 6mo"
  },
  ...
]
```

---

## `GET /forecast/fingerprint`

One fingerprint forecast. All five IDs are required as query params.
Returns `404` if no precomputed row matches.

**Query params:** all `int`, all required.
- `product_type_id`
- `gender_id`
- `color_master_id`
- `graphical_appearance_id`
- `material_id`

```bash
curl -s 'http://localhost:8000/forecast/fingerprint?product_type_id=1&gender_id=1&color_master_id=0&graphical_appearance_id=0&material_id=5'
```

```json
{
  "product_type_id": 1, "gender_id": 1, "color_master_id": 0,
  "graphical_appearance_id": 0, "material_id": 5,
  "product_type_name": "Trousers", "gender_name": "Women",
  "color_master_name": "Unknown", "graphical_appearance_name": "Unknown",
  "material_name": "viscose",
  "y_h1": 4.589e-05, "y_h2": 4.962e-05, "y_h3": 5.226e-05,
  "y_h4": 5.267e-05, "y_h5": 5.282e-05, "y_h6": 5.302e-05,
  "state": "rising",
  "stat": "+32% next 6mo"
}
```

```bash
# Missing fingerprint → 404
curl -i 'http://localhost:8000/forecast/fingerprint?product_type_id=999999&gender_id=1&color_master_id=1&graphical_appearance_id=1&material_id=1'
# HTTP/1.1 404 Not Found
# {"detail":"no precomputed forecast for fingerprint product_type_id=999999 ..."}
```

**Why some fingerprints are missing:** the predictions parquet only
contains rows where the cube has 4 contiguous months of history at the
anchor month (t-3..t). 5-D combinations that didn't appear in the cube
at the latest anchor — or that lack lag coverage — are silently
skipped during `pipelines.monthly.predict` and therefore 404 here.

---

## State + stat semantics

`state ∈ {rising, peak, flat, falling}` is computed once during
`pipelines.monthly.predict` (in `pipelines/monthly/state.py`) and stored
in the parquet. The API doesn't recompute on read.

`stat` is a short human-readable string keyed off `state`:
- `rising`: `"+{int}% next 6mo"`
- `falling`: `"−{int}% next 6mo"` (U+2212 minus sign)
- `peak`: `"at peak"`
- `flat`: `"stable"`

Initial thresholds are placeholders (RISING_RATIO=1.15, FALLING_RATIO=0.85,
PEAK_HORIZON_INDEX=1) flagged for tuning.

---

## Reloading predictions

The `BUNDLE` global is loaded once at startup via the FastAPI lifespan
hook. To pick up a new monthly tick's output, **restart the service**.
There's no hot-reload endpoint — predictions change at most monthly,
and a Cloud Run revision rollout is the natural refresh.
