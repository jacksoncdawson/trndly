"""Publish browser-ready JSON for the static SPA (the serving 'publisher').

Replaces the read-only FastAPI server in the serving path: instead of an app
that loads the predictions at boot and echoes them, the monthly tick emits the
exact same shapes as static files, deployed to Firebase Hosting (Phase 2).

Reads (via ``pipelines.serving.load_bundle`` + ``build_*``) from the tick:
    data/ticks/<YYYY-MM>/predictions_{univariate,fingerprint}.parquet
    data/ticks/<YYYY-MM>/merged_{univariate,fingerprint}.parquet   (the lag-join source)
    data/reference/lookup.csv                                       (options vocabularies)

Writes, into ``data/ticks/<YYYY-MM>/published/`` by default:
    trends.json / fingerprint.json / options.json / health.json   ← canonical (SPA fetches these)
    trends_<YYYY-MM>.json / ... / health_<YYYY-MM>.json           ← versioned archive
AND ALSO refreshes ``frontend/data/`` with the 4 canonical files (the SPA/CDN copy).

The canonical (month-less) files are the "latest pointer": byte-identical copies
of the current month's versioned files. The SPA fetches the canonical paths; the
Hosting ``Cache-Control`` on them is what busts the CDN each tick. The lag-join
(``share_lag*``/``share_t``) is the #1 parity risk and is gated by
``tests/serving/test_publish.py`` (golden-file diff with float tolerance).

Usage:
    python -m pipelines.monthly.publish
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pipelines.paths import (
    FRONTEND_DATA_DIR,
    LOOKUP_CSV,
    resolve_tick_month,
    tick_merged_path,
    tick_predictions_path,
    tick_published_dir,
)
from pipelines.serving import (
    build_fingerprint_index,
    build_health,
    build_options,
    build_trend_rows,
    load_bundle,
)

logger = logging.getLogger(__name__)

# Logical name → builder. Each value is produced fresh per publish.
_CANONICAL_NAMES = ("trends", "fingerprint", "options", "health")


def _dump(obj, path) -> None:
    """Write ``obj`` as pretty JSON. Pydantic models are dumped via model_dump."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=False)


def build_payloads(
    *,
    univariate_path=None,
    fingerprint_path=None,
    merged_univariate_path=None,
    merged_fingerprint_path=None,
    lookup_path=None,
) -> tuple[dict, str]:
    """Build the four JSON-ready payloads + the anchor month ('YYYY-MM').

    Returns ``({name: json_serializable}, anchor_month)``. Paths default to the
    canonical data tree; the golden test injects fixture paths.
    """
    bundle, error = load_bundle(
        univariate_path=univariate_path,
        fingerprint_path=fingerprint_path,
        merged_univariate_path=merged_univariate_path,
        merged_fingerprint_path=merged_fingerprint_path,
    )
    if bundle is None:
        raise RuntimeError(f"cannot publish: {error}")

    trends = [r.model_dump() for r in build_trend_rows(bundle)]
    fingerprint = {
        key: resp.model_dump()
        for key, resp in build_fingerprint_index(bundle).items()
    }
    options = build_options(lookup_path=lookup_path or LOOKUP_CSV).model_dump()
    health = build_health(bundle).model_dump()

    payloads = {
        "trends": trends,
        "fingerprint": fingerprint,
        "options": options,
        "health": health,
    }
    return payloads, bundle.anchor_month


def run_publish(
    month=None,
    *,
    out_dir=None,
    univariate_path=None,
    fingerprint_path=None,
    merged_univariate_path=None,
    merged_fingerprint_path=None,
    lookup_path=None,
) -> dict:
    """Emit versioned + canonical JSON for the tick ``month``.

    Defaults read the tick's predictions + merged checkpoints and write into
    ``data/ticks/<month>/published/`` (``out_dir`` default), then ALSO refresh
    ``frontend/data/`` with the 4 canonical files (the SPA/CDN copy). Injected
    paths (the golden test) override the tick defaults.

    Returns a summary dict: ``{anchor_month, out_dir, files, counts}``.
    """
    month = resolve_tick_month(month)

    # A real tick run defaults every read path to its checkpoint; a test/fixture
    # run injects them. Only a real run refreshes the committed frontend/data/.
    injected = any(
        p is not None for p in (
            univariate_path, fingerprint_path,
            merged_univariate_path, merged_fingerprint_path,
        )
    )

    # Default the read paths to this tick's checkpoint; injected paths win.
    if univariate_path is None:
        univariate_path = tick_predictions_path(month, "univariate")
    if fingerprint_path is None:
        fingerprint_path = tick_predictions_path(month, "fingerprint")
    if merged_univariate_path is None:
        merged_univariate_path = tick_merged_path(month, "univariate")
    if merged_fingerprint_path is None:
        merged_fingerprint_path = tick_merged_path(month, "fingerprint")

    out = Path(out_dir).expanduser().resolve() if out_dir else tick_published_dir(month)
    out.mkdir(parents=True, exist_ok=True)

    payloads, anchor_month = build_payloads(
        univariate_path=univariate_path,
        fingerprint_path=fingerprint_path,
        merged_univariate_path=merged_univariate_path,
        merged_fingerprint_path=merged_fingerprint_path,
        lookup_path=lookup_path,
    )

    written: list[str] = []
    for name in _CANONICAL_NAMES:
        obj = payloads[name]
        versioned = out / f"{name}_{anchor_month}.json"
        canonical = out / f"{name}.json"
        _dump(obj, versioned)   # archive
        _dump(obj, canonical)   # latest pointer the SPA fetches
        written.extend([versioned.name, canonical.name])
        # Refresh the SPA/CDN copy in frontend/data/ with the canonical file.
        # Skipped for injected (test/fixture) runs so they never touch the
        # committed frontend/data/ files.
        if not injected and out != FRONTEND_DATA_DIR:
            _dump(obj, FRONTEND_DATA_DIR / f"{name}.json")

    counts = {
        "trends_rows": len(payloads["trends"]),
        "fingerprint_keys": len(payloads["fingerprint"]),
        "options_categories": {k: len(v) for k, v in payloads["options"].items()},
        "lags_synthetic": payloads["health"]["lags_synthetic"],
    }
    summary = {
        "anchor_month": anchor_month,
        "out_dir": str(out),
        "files": written,
        "counts": counts,
    }
    logger.info(
        "published anchor=%s → %s (trends=%d, fingerprint=%d)",
        anchor_month, out, counts["trends_rows"], counts["fingerprint_keys"],
    )
    return summary


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    summary = run_publish()
    logger.info("publish summary: %s", summary["counts"])


if __name__ == "__main__":
    main()
