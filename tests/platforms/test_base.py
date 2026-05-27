import pytest
from open_pulse_crawler.platforms.base import (
    PlatformAdapter, Edge, ExpandOpts, RateLimitInfo,
)
from open_pulse_crawler.platforms import PlatformRegistry


def test_expand_opts_defaults():
    o = ExpandOpts()
    assert o.crawl_issues is False
    assert o.crawl_prs is False
    assert o.crawl_dependencies is False
    assert o.crawl_dependents is False
    assert o.crawl_stars is False
    assert o.min_stars == 0
    assert o.max_contributors is None
    assert o.issue_max == 100
    assert o.pr_max == 100


def test_edge_shape():
    e = Edge(
        src="https://github.com/a",
        kind="contributor_of",
        dst="https://github.com/a/repo",
    )
    assert e.src == "https://github.com/a"
    assert e.kind == "contributor_of"
    assert e.dst == "https://github.com/a/repo"


def test_rate_limit_info_optional_reset():
    r1 = RateLimitInfo(remaining=10, limit=5000)
    assert r1.reset_at is None
    r2 = RateLimitInfo(remaining=10, limit=5000, reset_at=1714435200.0)
    assert r2.reset_at == 1714435200.0


def test_platform_adapter_is_abstract():
    with pytest.raises(TypeError):
        PlatformAdapter()  # cannot instantiate the ABC


def test_registry_unknown_host_raises_keyerror():
    r = PlatformRegistry()
    with pytest.raises(KeyError):
        r.adapter_for("https://unknown.example.com/foo")


def test_registry_register_and_resolve_by_host():
    class FakeAdapter(PlatformAdapter):
        platform = "fake"
        def __init__(self, host):
            self.instance_host = host
        def classify(self, uri):
            return None
        def fetch(self, uri):
            return None
        def expand(self, node, opts):
            return iter([])
        def normalize_uri(self, raw):
            return raw
        def rate_limit_state(self):
            return RateLimitInfo(remaining=1, limit=1)

    r = PlatformRegistry()
    a = FakeAdapter("example.com")
    r.register(a)
    assert r.adapter_for("https://example.com/x") is a
    assert r.adapter_for("https://example.com/y/z") is a


def test_registry_host_lookup_is_case_insensitive():
    class FakeAdapter(PlatformAdapter):
        platform = "fake"
        def __init__(self, host):
            self.instance_host = host
        def classify(self, uri): return None
        def fetch(self, uri): return None
        def expand(self, node, opts): return iter([])
        def normalize_uri(self, raw): return raw
        def rate_limit_state(self): return RateLimitInfo(remaining=1, limit=1)

    r = PlatformRegistry()
    r.register(FakeAdapter("Example.COM"))
    # The registry should normalize hosts to lowercase on lookup, matching
    # node_id.canonical_url which lowercases the netloc.
    assert r.adapter_for("https://example.com/foo").instance_host.lower() == "example.com"


def test_registry_hosts_returns_sorted_list():
    class FA(PlatformAdapter):
        platform = "fake"
        def __init__(self, host): self.instance_host = host
        def classify(self, uri): return None
        def fetch(self, uri): return None
        def expand(self, node, opts): return iter([])
        def normalize_uri(self, raw): return raw
        def rate_limit_state(self): return RateLimitInfo(remaining=1, limit=1)

    r = PlatformRegistry()
    r.register(FA("gitlab.epfl.ch"))
    r.register(FA("github.com"))
    r.register(FA("gitlab.com"))
    assert r.hosts() == ["github.com", "gitlab.com", "gitlab.epfl.ch"]
