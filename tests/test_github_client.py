"""Tests for github_client helpers."""

import os
from pathlib import Path
from unittest.mock import patch

from open_pulse_crawler.github_client import (
    CACHE_DIR_ENV,
    DEFAULT_CACHE_DIR,
    APICache,
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


def test_resolve_cache_dir_uses_default_arg():
    """The caller-supplied `default` is used when no explicit value / env var."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(CACHE_DIR_ENV, None)
        assert resolve_cache_dir(default=Path("/srv/cache")) == Path("/srv/cache")


def test_resolve_cache_dir_env_overrides_default_arg():
    with patch.dict(os.environ, {CACHE_DIR_ENV: "/from/env"}):
        assert resolve_cache_dir(default=Path("/srv/cache")) == Path("/from/env")


def test_apicache_unwritable_dir_disables_gracefully(tmp_path):
    """An unusable cache dir disables the cache instead of raising — a crawl
    must never crash because the cache directory can't be created."""
    blocker = tmp_path / "afile"
    blocker.write_text("x")  # a file where APICache will try to mkdir a dir

    cache = APICache(blocker / "cache")  # must NOT raise

    assert cache.enabled is False
    assert cache.get("some-key") is None
    cache.set("some-key", "", {"v": 1})  # no-op, no exception


def test_apicache_writable_dir_round_trips(tmp_path):
    cache = APICache(tmp_path / "cache")
    assert cache.enabled is True
    cache.set("endpoint", "", {"hello": "world"})
    assert cache.get("endpoint") == {"hello": "world"}
