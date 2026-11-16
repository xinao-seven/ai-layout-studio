"""Locate shared data files in both the dev checkout and the Docker image."""

from __future__ import annotations

from pathlib import Path


def project_roots() -> list[Path]:
    """Return candidate roots, nearest first, safe for shallow container paths.

    - dev: ``apps/server/app/...`` -> server root (``apps/server``) and repo root
      (``apps/server`` -> ``apps`` -> repo)
    - Docker: ``/app/app/...`` -> ``/app`` (no repo root above it)
    """
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    if len(here.parents) > 2:
        candidates.append(here.parents[2])
    if len(here.parents) > 4:
        candidates.append(here.parents[4])
    candidates.append(Path.cwd())
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique
