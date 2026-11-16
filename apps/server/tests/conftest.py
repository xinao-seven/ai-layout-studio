"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Settings isolated from the developer's real .env and data directory."""
    return Settings(
        environment="test",
        data_dir=tmp_path / "data",
        llm_model="test-model",
        _env_file=None,
    )
