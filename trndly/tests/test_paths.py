"""Smoke tests for the central path registry.

Catches drift between ``pipelines.paths`` and the filesystem. Every
``Path``-typed module attribute must point to either (a) an existing
file/directory or (b) a directory whose parent exists. ``*_GLOB``
constants must be non-empty strings.

Constants in ``_PENDING_AUDIT`` are exempt — they are known-stale
pointers awaiting consumer audit (see plan Open Items).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipelines import paths  # noqa: E402


_PENDING_AUDIT: set[str] = set()


def _path_constants() -> list[tuple[str, Path]]:
    return [
        (name, value)
        for name, value in vars(paths).items()
        if isinstance(value, Path) and not name.startswith("_")
    ]


def _glob_constants() -> list[tuple[str, str]]:
    return [
        (name, value)
        for name, value in vars(paths).items()
        if name.endswith("_GLOB") and isinstance(value, str)
    ]


def test_ensure_data_dirs_runs():
    paths.ensure_data_dirs()


@pytest.mark.parametrize(
    "name,path",
    [(n, p) for n, p in _path_constants() if n not in _PENDING_AUDIT],
)
def test_path_constant_resolves(name: str, path: Path) -> None:
    if path.exists():
        return
    assert path.parent.exists(), (
        f"{name} = {path} does not exist and its parent {path.parent} is missing too."
    )


@pytest.mark.parametrize("name,pattern", _glob_constants())
def test_glob_constant_is_valid(name: str, pattern: str) -> None:
    assert pattern, f"{name} is empty"
    assert "*" in pattern or "?" in pattern, f"{name}={pattern!r} has no glob wildcard"


# --------------------------------------------------------------------------- #
# Immutable raw landing zone (Phase 1.1)                                        #
# --------------------------------------------------------------------------- #

def test_items_csv_path_for_is_month_stamped() -> None:
    p = paths.items_csv_path_for("gap", "2026-05")
    assert p.name == "items_gap_2026-05.csv"
    assert p.parent == paths.RAW_ITEMS_DIR


def test_items_csv_path_for_defaults_to_current_month() -> None:
    import re

    p = paths.items_csv_path_for("hollister")
    assert re.fullmatch(r"items_hollister_\d{4}-\d{2}\.csv", p.name), p.name


def test_parse_items_filename() -> None:
    assert paths._parse_items_filename("items_gap_2026-05.csv") == ("gap", "2026-05")
    # American_eagle has an underscore in the retailer name — month must still split off.
    assert paths._parse_items_filename("items_american_eagle_2026-05.csv") == (
        "american_eagle",
        "2026-05",
    )
    assert paths._parse_items_filename("items_gap.csv") == ("gap", None)
    assert paths._parse_items_filename("notitems.csv") == (None, None)


def test_discover_items_files_prefers_monthly_over_legacy(tmp_path) -> None:
    # Legacy + monthly for the same retailer: drop legacy, keep all monthly.
    for n in (
        "items_gap.csv",
        "items_gap_2026-05.csv",
        "items_gap_2026-06.csv",
        "items_hollister.csv",            # legacy-only retailer: kept
        "items_uniqlo_2026-05.csv",       # monthly-only retailer: kept
        "stray.csv",                      # not an items file: ignored
    ):
        (tmp_path / n).write_text("x")

    found = {p.name for p in paths.discover_items_files(tmp_path)}
    assert found == {
        "items_gap_2026-05.csv",
        "items_gap_2026-06.csv",
        "items_hollister.csv",
        "items_uniqlo_2026-05.csv",
    }


def test_discover_items_files_empty_dir(tmp_path) -> None:
    assert paths.discover_items_files(tmp_path) == []
