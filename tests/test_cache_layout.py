"""Per-host cache directory layout (Task 15).

The v3 layout is ``<cache_dir>/<host>/<sha>.json``. Two clients pointed
at the same ``cache_dir`` but with different ``host``s must produce
isolated cache trees so operators can wipe one host's cache without
nuking the others, and so the same URI on two instances never collides.
"""

from open_pulse_crawler.platforms.github.client import APICache


def test_cache_writes_under_host_subdirectory(tmp_path):
    cache = APICache(cache_dir=tmp_path, host="github.com")
    cache.set("some-endpoint", "", {"data": 1})

    host_dir = tmp_path / "github.com"
    assert host_dir.is_dir()
    # The cache file lives under the host subdirectory.
    files = list(host_dir.glob("*.json"))
    assert len(files) == 1


def test_cache_reads_from_host_subdirectory(tmp_path):
    cache = APICache(cache_dir=tmp_path, host="gitlab.epfl.ch")
    cache.set("endpoint", "", {"value": "x"})

    cache2 = APICache(cache_dir=tmp_path, host="gitlab.epfl.ch")
    assert cache2.get("endpoint") == {"value": "x"}


def test_cache_isolates_hosts(tmp_path):
    """Same lookup key on two hosts must NOT collide."""
    gh = APICache(cache_dir=tmp_path, host="github.com")
    gl = APICache(cache_dir=tmp_path, host="gitlab.com")
    gh.set("same-endpoint", "", {"who": "github"})
    gl.set("same-endpoint", "", {"who": "gitlab"})
    assert gh.get("same-endpoint") == {"who": "github"}
    assert gl.get("same-endpoint") == {"who": "gitlab"}

    # And on disk, the two host subdirectories each have exactly one file.
    gh_files = list((tmp_path / "github.com").glob("*.json"))
    gl_files = list((tmp_path / "gitlab.com").glob("*.json"))
    assert len(gh_files) == 1
    assert len(gl_files) == 1


def test_cache_default_host_is_github_com(tmp_path):
    """The host parameter defaults to ``github.com`` for v2.x callers."""
    cache = APICache(cache_dir=tmp_path)
    cache.set("ep", "", {"v": 1})
    assert (tmp_path / "github.com").is_dir()
    assert cache.get("ep") == {"v": 1}


def test_cache_host_attribute_is_exposed(tmp_path):
    """``self.host`` is set so callers can introspect the cache layout."""
    cache = APICache(cache_dir=tmp_path, host="gitlab.epfl.ch")
    assert cache.host == "gitlab.epfl.ch"
    # cache_dir is the host-scoped directory (parent is the operator-supplied root).
    assert cache.cache_dir == tmp_path / "gitlab.epfl.ch"
