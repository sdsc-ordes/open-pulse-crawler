"""Tests for config: host-keyed env-var resolution and platform enablement."""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize("host, ident", [
    ("github.com", "GITHUB_COM"),
    ("gitlab.epfl.ch", "GITLAB_EPFL_CH"),
    ("gitlab.ethz.ch", "GITLAB_ETHZ_CH"),
    ("renkulab.io", "RENKULAB_IO"),
    ("my-host.example.com", "MY_HOST_EXAMPLE_COM"),
])
def test_host_env_ident(host, ident):
    from open_pulse_crawler.config import host_env_ident
    assert host_env_ident(host) == ident


def test_enabled_instances_explicit(monkeypatch):
    from open_pulse_crawler.config import enabled_instances
    monkeypatch.setenv("CRAWLER_PLATFORMS", "github.com, gitlab.epfl.ch ,renkulab.io")
    assert enabled_instances() == ["github.com", "gitlab.epfl.ch", "renkulab.io"]


def test_enabled_instances_default_to_github(monkeypatch):
    from open_pulse_crawler.config import enabled_instances
    monkeypatch.delenv("CRAWLER_PLATFORMS", raising=False)
    assert enabled_instances() == ["github.com"]


def test_enabled_instances_blank_treated_as_unset(monkeypatch):
    from open_pulse_crawler.config import enabled_instances
    monkeypatch.setenv("CRAWLER_PLATFORMS", "   ")
    assert enabled_instances() == ["github.com"]


def test_resolve_tokens_pool_wins(monkeypatch):
    from open_pulse_crawler.config import resolve_tokens
    monkeypatch.setenv("CRAWLER_TOKEN_POOL__GITHUB_COM", "a,b,c")
    monkeypatch.setenv("CRAWLER_TOKEN__GITHUB_COM", "ignored")
    assert resolve_tokens("github.com") == ["a", "b", "c"]


def test_resolve_tokens_single(monkeypatch):
    from open_pulse_crawler.config import resolve_tokens
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__GITLAB_EPFL_CH", raising=False)
    monkeypatch.setenv("CRAWLER_TOKEN__GITLAB_EPFL_CH", "glpat-x")
    assert resolve_tokens("gitlab.epfl.ch") == ["glpat-x"]


def test_resolve_tokens_legacy_github_pool_emits_warning(monkeypatch):
    # Reload to reset the module-level "warned once" flag between tests.
    import open_pulse_crawler.config as cfg
    importlib.reload(cfg)
    for v in ("CRAWLER_TOKEN_POOL__GITHUB_COM", "CRAWLER_TOKEN__GITHUB_COM"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("CRAWLER_GITHUB_TOKEN_POOL", "gh1,gh2")
    with pytest.warns(DeprecationWarning, match="deprecated"):
        tokens = cfg.resolve_tokens("github.com")
    assert tokens == ["gh1", "gh2"]


def test_resolve_tokens_legacy_github_token_splits_comma(monkeypatch):
    import open_pulse_crawler.config as cfg
    importlib.reload(cfg)
    for v in ("CRAWLER_TOKEN_POOL__GITHUB_COM", "CRAWLER_TOKEN__GITHUB_COM",
              "CRAWLER_GITHUB_TOKEN_POOL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("CRAWLER_GITHUB_TOKEN", "a,b")
    with pytest.warns(DeprecationWarning):
        assert cfg.resolve_tokens("github.com") == ["a", "b"]


def test_resolve_tokens_legacy_github_token_env_splits_comma(monkeypatch):
    import open_pulse_crawler.config as cfg
    importlib.reload(cfg)
    for v in ("CRAWLER_TOKEN_POOL__GITHUB_COM", "CRAWLER_TOKEN__GITHUB_COM",
              "CRAWLER_GITHUB_TOKEN_POOL", "CRAWLER_GITHUB_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "old1,old2")
    with pytest.warns(DeprecationWarning):
        assert cfg.resolve_tokens("github.com") == ["old1", "old2"]


def test_resolve_tokens_empty(monkeypatch):
    from open_pulse_crawler.config import resolve_tokens
    for v in ("CRAWLER_TOKEN_POOL__GITLAB_ETHZ_CH", "CRAWLER_TOKEN__GITLAB_ETHZ_CH"):
        monkeypatch.delenv(v, raising=False)
    assert resolve_tokens("gitlab.ethz.ch") == []


def test_resolve_tokens_whitespace_value_treated_as_unset(monkeypatch):
    import open_pulse_crawler.config as cfg
    importlib.reload(cfg)
    monkeypatch.setenv("CRAWLER_TOKEN_POOL__GITHUB_COM", "   ")
    monkeypatch.setenv("CRAWLER_TOKEN__GITHUB_COM", "")
    monkeypatch.setenv("CRAWLER_GITHUB_TOKEN_POOL", "real,tokens")
    with pytest.warns(DeprecationWarning):
        assert cfg.resolve_tokens("github.com") == ["real", "tokens"]


# --- Crossref mailto config (Spec 6 foundations) ----------------------------

def test_crossref_mailto_env_constant():
    from open_pulse_crawler.config import CROSSREF_MAILTO_ENV
    assert CROSSREF_MAILTO_ENV == "CRAWLER_CROSSREF_MAILTO"


def test_resolve_crossref_mailto_returns_value_when_set(monkeypatch):
    from open_pulse_crawler.config import resolve_crossref_mailto
    monkeypatch.setenv("CRAWLER_CROSSREF_MAILTO", "researcher@example.org")
    assert resolve_crossref_mailto() == "researcher@example.org"


def test_resolve_crossref_mailto_returns_none_when_unset(monkeypatch):
    from open_pulse_crawler.config import resolve_crossref_mailto
    monkeypatch.delenv("CRAWLER_CROSSREF_MAILTO", raising=False)
    assert resolve_crossref_mailto() is None


def test_resolve_crossref_mailto_strips_whitespace(monkeypatch):
    from open_pulse_crawler.config import resolve_crossref_mailto
    monkeypatch.setenv("CRAWLER_CROSSREF_MAILTO", "  user@uni.edu  ")
    assert resolve_crossref_mailto() == "user@uni.edu"


def test_resolve_crossref_mailto_returns_none_for_blank(monkeypatch):
    from open_pulse_crawler.config import resolve_crossref_mailto
    monkeypatch.setenv("CRAWLER_CROSSREF_MAILTO", "   ")
    assert resolve_crossref_mailto() is None
