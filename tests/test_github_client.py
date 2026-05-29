"""Tests for github_client helpers."""

import os
import time
from pathlib import Path
from unittest.mock import patch

from open_pulse_crawler.platforms.github.client import (
    CACHE_DIR_ENV,
    CACHE_TTL_ENV,
    DEFAULT_CACHE_DIR,
    DEFAULT_CACHE_TTL_DAYS,
    APICache,
    resolve_cache_dir,
    resolve_cache_ttl,
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


# ── Cache TTL ────────────────────────────────────────────────────────────────


def test_resolve_cache_ttl_default_is_30_days():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(CACHE_TTL_ENV, None)
        assert resolve_cache_ttl() == DEFAULT_CACHE_TTL_DAYS * 86400.0
    assert DEFAULT_CACHE_TTL_DAYS == 30


def test_resolve_cache_ttl_from_env():
    with patch.dict(os.environ, {CACHE_TTL_ENV: "7"}):
        assert resolve_cache_ttl() == 7 * 86400.0


def test_resolve_cache_ttl_zero_disables_expiry():
    with patch.dict(os.environ, {CACHE_TTL_ENV: "0"}):
        assert resolve_cache_ttl() is None
    with patch.dict(os.environ, {CACHE_TTL_ENV: "-1"}):
        assert resolve_cache_ttl() is None


def test_resolve_cache_ttl_invalid_falls_back_to_default():
    with patch.dict(os.environ, {CACHE_TTL_ENV: "not-a-number"}):
        assert resolve_cache_ttl() == DEFAULT_CACHE_TTL_DAYS * 86400.0


def test_apicache_serves_fresh_entry_within_ttl(tmp_path):
    cache = APICache(tmp_path / "cache", ttl_seconds=3600)
    cache.set("ep", "", {"v": 1})
    assert cache.get("ep") == {"v": 1}  # well within the TTL


def test_apicache_treats_stale_entry_as_miss(tmp_path):
    cache = APICache(tmp_path / "cache", ttl_seconds=60)
    cache.set("ep", "", {"v": 1})
    # Backdate the cache file's mtime to 10 minutes ago — older than the TTL.
    cache_file = cache.cache_dir / f"{cache._get_cache_key('ep', '')}.json"
    old = time.time() - 600
    os.utime(cache_file, (old, old))
    assert cache.get("ep") is None  # expired -> miss


def test_apicache_no_ttl_keeps_entry_indefinitely(tmp_path):
    cache = APICache(tmp_path / "cache", ttl_seconds=None)
    cache.set("ep", "", {"v": 1})
    cache_file = cache.cache_dir / f"{cache._get_cache_key('ep', '')}.json"
    old = time.time() - 10 * 365 * 86400  # 10 years old
    os.utime(cache_file, (old, old))
    assert cache.get("ep") == {"v": 1}  # ttl_seconds=None -> never expires
