"""Tests for github_client helpers."""

import os
from pathlib import Path
from unittest.mock import patch

from open_pulse_crawler.github_client import (
    CACHE_DIR_ENV,
    DEFAULT_CACHE_DIR,
    resolve_cache_dir,
)


def test_resolve_cache_dir_explicit_wins_over_env():
    with patch.dict(os.environ, {CACHE_DIR_ENV: "/from/env"}):
        assert resolve_cache_dir(Path("/explicit")) == Path("/explicit")


def test_resolve_cache_dir_uses_env_when_no_explicit():
    with patch.dict(os.environ, {CACHE_DIR_ENV: "/from/env"}):
        assert resolve_cache_dir() == Path("/from/env")


def test_resolve_cache_dir_falls_back_to_default():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(CACHE_DIR_ENV, None)
        assert resolve_cache_dir() == Path(DEFAULT_CACHE_DIR)
    # The documented default location.
    assert DEFAULT_CACHE_DIR == "data/open-pulse-crawler/cache"


def test_resolve_cache_dir_empty_env_disables_caching():
    with patch.dict(os.environ, {CACHE_DIR_ENV: ""}):
        assert resolve_cache_dir() is None
    with patch.dict(os.environ, {CACHE_DIR_ENV: "   "}):
        assert resolve_cache_dir() is None
