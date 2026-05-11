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
