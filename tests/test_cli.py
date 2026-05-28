"""Tests for the Typer CLI (Task 14 multi-platform options + ``doctor``).

These tests exercise option recognition, ``doctor`` output formats, and the
seed-normalization helper. They deliberately avoid touching the network or
the GitHub API: ``crawl`` itself is only smoke-checked via ``--help``.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from open_pulse_crawler.cli import (
    app,
    _normalize_seeds,
    _parse_platforms_csv,
)


runner = CliRunner()


# ──────────────────────────────────────────────────────────────────────────
# doctor subcommand
# ──────────────────────────────────────────────────────────────────────────


def _clear_token_env(monkeypatch) -> None:
    """Strip the env of every variable ``resolve_tokens`` consults.

    Keeps the host-keyed prefixes plus the legacy github trio out of the
    picture so each test starts from a known-empty baseline.
    """
    for var in (
        "CRAWLER_TOKEN__GITHUB_COM",
        "CRAWLER_TOKEN_POOL__GITHUB_COM",
        "CRAWLER_TOKEN__GITLAB_EPFL_CH",
        "CRAWLER_TOKEN_POOL__GITLAB_EPFL_CH",
        "CRAWLER_TOKEN__GITLAB_COM",
        "CRAWLER_TOKEN_POOL__GITLAB_COM",
        "CRAWLER_GITHUB_TOKEN_POOL",
        "CRAWLER_GITHUB_TOKEN",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def test_doctor_lists_hosts(monkeypatch):
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "github.com,gitlab.epfl.ch")
    monkeypatch.setenv("CRAWLER_TOKEN__GITHUB_COM", "tok")
    # gitlab.epfl.ch deliberately has no tokens — should show MISSING.

    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "github.com" in r.output
    assert "gitlab.epfl.ch" in r.output
    assert "OK" in r.output
    assert "MISSING" in r.output


def test_doctor_json_output(monkeypatch):
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "github.com")
    monkeypatch.setenv("CRAWLER_TOKEN_POOL__GITHUB_COM", "tok1,tok2")

    r = runner.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert isinstance(payload, list)
    assert payload[0]["host"] == "github.com"
    assert payload[0]["tokens"] == 2
    assert payload[0]["ok"] is True


def test_doctor_default_to_github_only(monkeypatch):
    _clear_token_env(monkeypatch)
    monkeypatch.delenv("CRAWLER_PLATFORMS", raising=False)
    # Tokens may or may not be configured — doctor still lists github.com.
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "github.com" in r.output


def test_doctor_json_marks_missing_tokens(monkeypatch):
    """A host with no tokens must serialize with ``ok=False, tokens=0``."""
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "gitlab.epfl.ch")

    r = runner.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload == [{"host": "gitlab.epfl.ch", "tokens": 0, "ok": False}]


# ──────────────────────────────────────────────────────────────────────────
# crawl subcommand: new options are recognised
# ──────────────────────────────────────────────────────────────────────────


def test_crawl_accepts_crawl_stars_flag():
    r = runner.invoke(app, ["crawl", "--help"])
    assert r.exit_code == 0, r.output
    assert "--crawl-stars" in r.output


def test_crawl_help_lists_new_options():
    r = runner.invoke(app, ["crawl", "--help"])
    assert r.exit_code == 0, r.output
    assert "--platforms" in r.output
    assert "--default-host" in r.output
    assert "--crawl-stars" in r.output


def test_crawl_accepts_platforms_option(monkeypatch):
    """Smoke-test: Typer parses ``--platforms`` / ``--default-host`` without
    yelling about unknown options. We don't drive a real crawl — we just
    invoke with no seeds and confirm we reach the "no seeds" error path
    rather than an "unknown option" parse error."""
    _clear_token_env(monkeypatch)
    r = runner.invoke(
        app,
        [
            "crawl",
            "--platforms",
            "github.com,gitlab.com",
            "--default-host",
            "github.com",
            "--rounds",
            "1",
        ],
    )
    # ``r.output`` is the merged stdout/stderr by default. We just need to
    # confirm Typer did not error on the new options.
    assert "no such option" not in (r.output or "").lower(), r.output
    # Typer should have accepted the options; we expect a non-zero exit
    # because no seeds were provided (or because no tokens are configured).
    assert r.exit_code != 0


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


def test_parse_platforms_csv_blank_returns_empty():
    assert _parse_platforms_csv(None) == []
    assert _parse_platforms_csv("") == []
    assert _parse_platforms_csv("   ") == []


def test_parse_platforms_csv_strips_and_splits():
    assert _parse_platforms_csv("github.com, gitlab.epfl.ch ,renkulab.io") == [
        "github.com",
        "gitlab.epfl.ch",
        "renkulab.io",
    ]


def test_normalize_seeds_applies_default_host_to_bare_seeds():
    out = _normalize_seeds(
        ["torvalds", "gitlab-org/gitlab", "https://github.com/foo/bar"],
        default_host="gitlab.com",
    )
    assert out == [
        "https://gitlab.com/torvalds",
        "https://gitlab.com/gitlab-org/gitlab",
        "https://github.com/foo/bar",
    ]


def test_normalize_seeds_default_github_unchanged():
    """Legacy github invocation: bare seeds resolve to github.com URLs."""
    out = _normalize_seeds(["torvalds", "torvalds/linux"], default_host="github.com")
    assert out == [
        "https://github.com/torvalds",
        "https://github.com/torvalds/linux",
    ]
