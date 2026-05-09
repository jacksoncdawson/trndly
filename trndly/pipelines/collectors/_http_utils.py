"""Shared HTTP + streaming-output utilities for retail scrapers.

Two pieces, deduplicated from the four scrapers in this directory
(gap, hollister, american_eagle, uniqlo):

* ``request_with_retry`` — async GET with exponential-backoff retry on
  network errors and a configurable set of retry-worthy HTTP statuses.
  Default set is 408/425/429/5xx (transient). Hollister and AE pass
  ``retryable_statuses=DEFAULT_RETRYABLE_STATUSES | {403}`` because
  Akamai sometimes 403s under load and yields on retry.
* ``StreamingItemWriter`` — append-as-you-go CSV writer. Writes a
  ``items_<retailer>_partial.csv`` while the scrape runs, atomically
  renames to the final path on clean exit. With ``resume=True``, prior
  ``(style_id, cc_id, gender)`` keys are loaded so duplicate work is
  skipped without re-reading the file on every append.

Why both live in one module: they're only ever co-imported by retail
scrapers. Keeping them together avoids spreading scraper-only utilities
across multiple files.
"""
from __future__ import annotations

import asyncio
import csv
import os
import random
from pathlib import Path
from typing import Iterable

import httpx

# --------------------------------------------------------------------------- #
# Retry config                                                                  #
# --------------------------------------------------------------------------- #

DEFAULT_RETRYABLE_STATUSES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})
DEFAULT_MAX_ATTEMPTS: int = 5

# Canonical 18-column items CSV schema. Every scraper's ``_combo_to_row``
# must produce a dict with exactly these keys, in this order. Tests pin
# this in tests/conftest.py::ITEMS_CSV_FIELDNAMES; the two must agree.
CSV_FIELDNAMES: list[str] = [
    "scraped_at", "retailer",
    "style_id", "cc_id", "web_product_type",
    "title", "gender",
    "color_raw", "product_type_raw", "material_raw", "graphical_appearance_raw",
    "color_master_id", "color_spectrum_id", "gender_id",
    "product_type_id", "product_group_id", "material_id", "graphical_appearance_id",
]


# --------------------------------------------------------------------------- #
# Async GET with retry                                                          #
# --------------------------------------------------------------------------- #

async def request_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    label: str = "",
    verbose: bool = True,
    retryable_statuses: Iterable[int] = DEFAULT_RETRYABLE_STATUSES,
) -> httpx.Response | None:
    """GET with exponential-backoff retry on transient errors.

    Returns the ``Response`` on 200; ``None`` if retries exhausted or a
    non-retryable status was returned. Wait between attempts is
    ``2**(attempt-1) + uniform(0, 0.6)`` seconds. Same shape across all
    four scrapers; the only thing they vary is ``retryable_statuses``
    (Hollister/AE add 403).
    """
    statuses = set(retryable_statuses)
    for attempt in range(1, max_attempts + 1):
        try:
            r = await client.get(url, params=params, follow_redirects=True)
            if r.status_code == 200:
                return r
            if r.status_code in statuses:
                wait = (2 ** (attempt - 1)) + random.uniform(0, 0.6)
                if verbose:
                    print(
                        f"    [http] {label} got {r.status_code}, "
                        f"retry {attempt}/{max_attempts} in {wait:.1f}s"
                    )
                await asyncio.sleep(wait)
                continue
            if verbose:
                print(f"    [http] {label} non-retryable {r.status_code}: {r.text[:200]}")
            return None
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            wait = (2 ** (attempt - 1)) + random.uniform(0, 0.6)
            if verbose:
                print(
                    f"    [http] {label} {type(exc).__name__}: "
                    f"retry {attempt}/{max_attempts} in {wait:.1f}s"
                )
            await asyncio.sleep(wait)
    if verbose:
        print(f"    [http] {label} GAVE UP after {max_attempts} attempts")
    return None


# --------------------------------------------------------------------------- #
# Streaming CSV writer with partial→atomic-rename                               #
# --------------------------------------------------------------------------- #

class StreamingItemWriter:
    """Append rows to ``<final_stem>_partial.csv`` as they're produced;
    on clean ``__exit__`` atomically rename to ``final_path``.

    With ``resume=True`` and an existing partial, prior
    ``(style_id, cc_id, gender)`` keys are loaded so callers can use
    ``already_have(...)`` to skip duplicate work without re-reading.

    Same dedup-key shape across all four scrapers:
    ``(style_id, cc_id, gender)`` — gender is part of the key because
    the same SKU can legitimately appear in both the men's and women's
    catalogs for unisex items.
    """

    def __init__(
        self,
        final_path: Path,
        *,
        resume: bool = False,
        fieldnames: Iterable[str] = CSV_FIELDNAMES,
    ) -> None:
        self.final_path = final_path
        self.partial_path = final_path.with_name(final_path.stem + "_partial.csv")
        self._resume = resume
        self._fieldnames = list(fieldnames)
        self._existing: set[tuple[str, str, str]] = set()
        self._handle = None
        self._writer = None

    def __enter__(self) -> "StreamingItemWriter":
        if self._resume and self.partial_path.exists():
            with self.partial_path.open("r", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self._existing.add((
                        row.get("style_id", ""),
                        row.get("cc_id", ""),
                        row.get("gender", ""),
                    ))
            print(f"  [resume] loaded {len(self._existing)} prior keys from {self.partial_path}")
            self._handle = self.partial_path.open("a", newline="")
            self._writer = csv.DictWriter(self._handle, fieldnames=self._fieldnames)
        else:
            # fresh run — clobber any stale partial
            if self.partial_path.exists():
                self.partial_path.unlink()
            self._handle = self.partial_path.open("w", newline="")
            self._writer = csv.DictWriter(self._handle, fieldnames=self._fieldnames)
            self._writer.writeheader()
            self._handle.flush()
        return self

    def already_have(self, style_id: str, cc_id: str, gender: str) -> bool:
        return (style_id, cc_id, gender) in self._existing

    def write(self, row: dict) -> None:
        self._writer.writerow(row)
        # csv.DictWriter buffers; per-row flush is cheap for ~5K-row
        # runs and the durability is worth it for crash-resume.
        self._handle.flush()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            self._handle.close()
        if exc_type is None:
            os.replace(self.partial_path, self.final_path)
        # On exception, leave the partial file in place for inspection / resume.
