"""Tests for token_env: resolve GitHub PATs from environment with priority."""

from __future__ import annotations

import logging

import pytest

from open_pulse_crawler import token_env
from open_pulse_crawler.token_env import (
    LEGACY_ENV,
    POOL_ENV,
    TOKEN_ENV,
    resolve_github_tokens,
    tokens_not_set_message,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Each test starts with all three env vars unset and a re-armed warning."""
    for var in (POOL_ENV, TOKEN_ENV, LEGACY_ENV):
        monkeypatch.delenv(var, raising=False)
    token_env.reset_deprecation_warning()
    yield
    token_env.reset_deprecation_warning()


def test_returns_empty_list_when_nothing_set():
    assert resolve_github_tokens() == []


def test_pool_takes_precedence_over_single_and_legacy(monkeypatch):
    monkeypatch.setenv(POOL_ENV, "p1,p2,p3")
    monkeypatch.setenv(TOKEN_ENV, "single")
    monkeypatch.setenv(LEGACY_ENV, "legacy")
    assert resolve_github_tokens() == ["p1", "p2", "p3"]


def test_pool_parses_comma_separated_and_strips(monkeypatch):
    monkeypatch.setenv(POOL_ENV, " p1 , , p2 ,p3 ")
    assert resolve_github_tokens() == ["p1", "p2", "p3"]


def test_single_used_when_pool_empty(monkeypatch):
    monkeypatch.setenv(POOL_ENV, "")
    monkeypatch.setenv(TOKEN_ENV, "ghp_one")
    assert resolve_github_tokens() == ["ghp_one"]


def test_single_tolerates_comma_list(monkeypatch):
    """If users put a comma list in TOKEN, accept it rather than silently dropping."""
    monkeypatch.setenv(TOKEN_ENV, "a,b")
    assert resolve_github_tokens() == ["a", "b"]


def test_legacy_fallback_used_when_new_vars_unset(monkeypatch):
    monkeypatch.setenv(LEGACY_ENV, "old1,old2")
    assert resolve_github_tokens() == ["old1", "old2"]


def test_legacy_emits_deprecation_warning_once(monkeypatch, caplog):
    monkeypatch.setenv(LEGACY_ENV, "old")
    with caplog.at_level(logging.WARNING, logger="open_pulse_crawler.token_env"):
        resolve_github_tokens()
        resolve_github_tokens()
    deprecation_records = [
        r for r in caplog.records if "deprecated" in r.getMessage().lower()
    ]
    assert len(deprecation_records) == 1
    msg = deprecation_records[0].getMessage()
    assert LEGACY_ENV in msg
    assert TOKEN_ENV in msg
    assert POOL_ENV in msg


def test_no_deprecation_when_new_var_present(monkeypatch, caplog):
    # Reset the once-flags. Use whatever helper this test file already has;
    # if there's no helper, reload both modules.
    import importlib
    import open_pulse_crawler.config as cfg
    import open_pulse_crawler.token_env as te
    importlib.reload(cfg); importlib.reload(te)

    monkeypatch.setenv("CRAWLER_TOKEN__GITHUB_COM", "new")
    monkeypatch.setenv("CRAWLER_GITHUB_TOKEN", "old")
    with caplog.at_level(logging.WARNING, logger="open_pulse_crawler.token_env"):
        assert te.resolve_github_tokens() == ["new"]
    assert not any("deprecated" in r.getMessage().lower() for r in caplog.records)


def test_whitespace_only_value_treated_as_unset(monkeypatch):
    monkeypatch.setenv(POOL_ENV, "   ")
    monkeypatch.setenv(TOKEN_ENV, "good")
    assert resolve_github_tokens() == ["good"]


def test_not_set_message_mentions_both_new_vars():
    msg = tokens_not_set_message()
    assert TOKEN_ENV in msg
    assert POOL_ENV in msg
