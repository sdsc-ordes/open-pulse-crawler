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
    # gitlab.epfl.ch deliberately has no tokens — should show ANONYMOUS
    # (GitLab's anonymous mode reads public projects without auth, so it's
    # functional without a token — unlike github.com whose 60/hour
    # anonymous limit makes it effectively unusable).

    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "github.com" in r.output
    assert "gitlab.epfl.ch" in r.output
    assert "OK" in r.output
    assert "ANONYMOUS" in r.output


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


def test_doctor_json_marks_github_missing_when_no_token(monkeypatch):
    """github.com is the only host that strictly requires a token —
    its anonymous rate limit (60/hour) is too low to be useful for crawling.
    With no token, doctor must report `ok=False, auth_required=True`."""
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "github.com")

    r = runner.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload == [
        {"host": "github.com", "tokens": 0, "auth_required": True, "ok": False},
    ]


def test_doctor_json_marks_gitlab_anonymous_when_no_token(monkeypatch):
    """GitLab without tokens registers in anonymous mode (public projects /
    users / groups remain readable). doctor must report `ok=True,
    auth_required=False` so users don't see a misleading "MISSING" status."""
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "gitlab.epfl.ch")

    r = runner.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload == [
        {"host": "gitlab.epfl.ch", "tokens": 0, "auth_required": False, "ok": True},
    ]


# ──────────────────────────────────────────────────────────────────────────
# crawl subcommand: new options are recognised
# ──────────────────────────────────────────────────────────────────────────


import re as _re

_ANSI_RE = _re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _help_text(app_, args):
    """Invoke ``--help`` and return plain text — strip ANSI codes and wrap
    on a wide terminal so Rich doesn't truncate long option names with
    ellipsis (which is what was breaking in CI's narrow runner terminal).
    """
    r = runner.invoke(app_, args, env={"COLUMNS": "240", "TERM": "dumb"})
    return r, _ANSI_RE.sub("", r.output)


def test_crawl_accepts_crawl_stars_flag():
    r, text = _help_text(app, ["crawl", "--help"])
    assert r.exit_code == 0, r.output
    assert "--crawl-stars" in text


def test_crawl_help_lists_new_options():
    r, text = _help_text(app, ["crawl", "--help"])
    assert r.exit_code == 0, r.output
    assert "--platforms" in text
    assert "--default-host" in text
    assert "--crawl-stars" in text


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


# ──────────────────────────────────────────────────────────────────────────
# Zenodo adapter registration (Task 8)
# ──────────────────────────────────────────────────────────────────────────


def test_doctor_lists_zenodo_with_anonymous_status(monkeypatch):
    monkeypatch.setenv("CRAWLER_PLATFORMS", "zenodo.org,sandbox.zenodo.org")
    monkeypatch.delenv("CRAWLER_TOKEN__ZENODO_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__ZENODO_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN__SANDBOX_ZENODO_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__SANDBOX_ZENODO_ORG", raising=False)

    r = runner.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0
    import json
    payload = json.loads(r.stdout)
    hosts = {p["host"] for p in payload}
    assert "zenodo.org" in hosts
    assert "sandbox.zenodo.org" in hosts
    # Anonymous-mode = no tokens but adapter still functional.
    for p in payload:
        if p["host"].endswith("zenodo.org"):
            assert p["tokens"] == 0


def test_build_registry_registers_zenodo_adapter_for_zenodo_org(monkeypatch):
    """When CRAWLER_PLATFORMS includes zenodo.org, the CLI builds a Zenodo
    adapter even without a token."""
    from open_pulse_crawler.cli import _build_registry
    monkeypatch.delenv("CRAWLER_TOKEN__ZENODO_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__ZENODO_ORG", raising=False)
    registry, github_client, missing = _build_registry(["zenodo.org"])
    assert "zenodo.org" in missing  # no token → reported as missing
    # …but the adapter IS registered for anonymous use:
    adapter = registry.adapter_for("https://zenodo.org/records/1")
    from open_pulse_crawler.platforms.zenodo.adapter import ZenodoAdapter
    assert isinstance(adapter, ZenodoAdapter)


def test_build_registry_registers_zenodo_adapter_with_token(monkeypatch):
    """Authenticated Zenodo path also produces a ZenodoAdapter."""
    from open_pulse_crawler.cli import _build_registry
    monkeypatch.setenv("CRAWLER_TOKEN__ZENODO_ORG", "zen-pat-test")
    registry, github_client, missing = _build_registry(["zenodo.org"])
    assert "zenodo.org" not in missing
    adapter = registry.adapter_for("https://zenodo.org/records/1")
    from open_pulse_crawler.platforms.zenodo.adapter import ZenodoAdapter
    assert isinstance(adapter, ZenodoAdapter)


def test_build_registry_registers_zenodo_adapter_for_sandbox(monkeypatch):
    """sandbox.zenodo.org gets its own Zenodo adapter."""
    from open_pulse_crawler.cli import _build_registry
    monkeypatch.delenv("CRAWLER_TOKEN__SANDBOX_ZENODO_ORG", raising=False)
    registry, github_client, missing = _build_registry(["sandbox.zenodo.org"])
    adapter = registry.adapter_for("https://sandbox.zenodo.org/records/1")
    from open_pulse_crawler.platforms.zenodo.adapter import ZenodoAdapter
    assert isinstance(adapter, ZenodoAdapter)
    assert adapter.instance_host == "sandbox.zenodo.org"


# ──────────────────────────────────────────────────────────────────────────
# Infoscience adapter registration (Task 8)
# ──────────────────────────────────────────────────────────────────────────


def test_build_registry_registers_infoscience_adapter(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    monkeypatch.delenv("CRAWLER_TOKEN__INFOSCIENCE_EPFL_CH", raising=False)
    registry, github_client, missing = _build_registry(["infoscience.epfl.ch"])
    assert "infoscience.epfl.ch" in missing  # no token → reported
    adapter = registry.adapter_for("https://infoscience.epfl.ch/handle/20.500.14299/1")
    from open_pulse_crawler.platforms.infoscience.adapter import InfoscienceAdapter
    assert isinstance(adapter, InfoscienceAdapter)


def test_build_registry_registers_infoscience_with_token(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    monkeypatch.setenv("CRAWLER_TOKEN__INFOSCIENCE_EPFL_CH", "dspace-tok-test")
    registry, github_client, missing = _build_registry(["infoscience.epfl.ch"])
    assert "infoscience.epfl.ch" not in missing
    adapter = registry.adapter_for("https://infoscience.epfl.ch/handle/20.500.14299/1")
    from open_pulse_crawler.platforms.infoscience.adapter import InfoscienceAdapter
    assert isinstance(adapter, InfoscienceAdapter)


# ──────────────────────────────────────────────────────────────────────────
# DataCite adapter registration (Task 9)
# ──────────────────────────────────────────────────────────────────────────


def test_build_registry_datacite_anonymous_registers_against_five_hosts(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter
    monkeypatch.delenv("CRAWLER_TOKEN__API_DATACITE_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__API_DATACITE_ORG", raising=False)
    registry, _gh, missing = _build_registry(["datacite.org"])
    assert "datacite.org" in missing
    # All five hosts resolve to the same DataCiteAdapter instance
    a = registry.adapter_for("https://doi.org/10.6084/m9.figshare.99")
    b = registry.adapter_for("https://ror.org/02s376052")
    c = registry.adapter_for("https://orcid.org/0000-0002-1825-0097")
    d = registry.adapter_for("https://api.datacite.org/dois/10.x/y")
    e = registry.adapter_for("https://commons.datacite.org/repositories/cern.zenodo")
    assert a is b is c is d is e
    assert isinstance(a, DataCiteAdapter)


def test_build_registry_datacite_with_token_marks_present(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    monkeypatch.setenv("CRAWLER_TOKEN__API_DATACITE_ORG", "dc-pat-test")
    registry, _gh, missing = _build_registry(["datacite.org"])
    assert "datacite.org" not in missing
    a = registry.adapter_for("https://doi.org/10.6084/m9.figshare.99")
    from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter
    assert isinstance(a, DataCiteAdapter)


def test_doctor_resolves_datacite_token_via_api_host(monkeypatch):
    """`crawler doctor` must look up DataCite's token under the API host
    (``CRAWLER_TOKEN__API_DATACITE_ORG``), not the user-facing platform key.
    Regression: previously reported `datacite.org: MISSING` even when the
    token was correctly configured."""
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "datacite.org")
    monkeypatch.setenv("CRAWLER_TOKEN__API_DATACITE_ORG", "dc-pat-test")
    r = runner.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    datacite_row = next((p for p in payload if p["host"] == "datacite.org"), None)
    assert datacite_row is not None
    assert datacite_row["tokens"] == 1
    assert datacite_row["ok"] is True


def test_doctor_datacite_without_token_reports_anonymous(monkeypatch):
    """Without `CRAWLER_TOKEN__API_DATACITE_ORG`, doctor reports ANONYMOUS
    (not MISSING): DataCite's public reads work without auth. `ok=True`
    because the adapter can crawl; `auth_required=False` because no
    token is strictly needed."""
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "datacite.org")
    monkeypatch.delenv("CRAWLER_TOKEN__API_DATACITE_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__API_DATACITE_ORG", raising=False)
    r = runner.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    datacite_row = next((p for p in payload if p["host"] == "datacite.org"), None)
    assert datacite_row is not None
    assert datacite_row["tokens"] == 0
    assert datacite_row["auth_required"] is False
    assert datacite_row["ok"] is True


def test_doctor_openalex_without_token_reports_anonymous(monkeypatch):
    """With `CRAWLER_PLATFORMS=openalex.org` and no tokens, doctor reports
    ANONYMOUS (not MISSING, not erroring): OpenAlex's public reads work
    without auth. `ok=True` because the adapter can crawl;
    `auth_required=False` because no token is needed (the
    `CRAWLER_OPENALEX_MAILTO` polite-pool email is not a token)."""
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "openalex.org")
    monkeypatch.delenv("CRAWLER_TOKEN__OPENALEX_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__OPENALEX_ORG", raising=False)
    r = runner.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    openalex_row = next((p for p in payload if p["host"] == "openalex.org"), None)
    assert openalex_row is not None
    assert openalex_row["tokens"] == 0
    assert openalex_row["auth_required"] is False
    assert openalex_row["ok"] is True


def test_doctor_text_output_renders_three_states(monkeypatch):
    """Smoke-test the rich-text output picks OK/ANONYMOUS/MISSING correctly."""
    _clear_token_env(monkeypatch)
    monkeypatch.setenv(
        "CRAWLER_PLATFORMS",
        "github.com,gitlab.epfl.ch,datacite.org",
    )
    # github.com gets a token → OK
    monkeypatch.setenv("CRAWLER_TOKEN__GITHUB_COM", "ghp_test")
    # gitlab.epfl.ch + datacite.org get no token → ANONYMOUS
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "github.com" in r.output and "OK" in r.output
    assert "ANONYMOUS" in r.output
    # MISSING should NOT appear in this configuration (only github has a token,
    # but it's set, so no host triggers MISSING).
    assert "MISSING" not in r.output


def test_doctor_text_output_renders_missing_for_github_without_token(monkeypatch):
    """Sanity: github.com without tokens shows MISSING (it's the only
    auth-required platform). Symmetrical with the previous test."""
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "github.com")
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "MISSING" in r.output


# ──────────────────────────────────────────────────────────────────────────
# HuggingFace adapter registration (Task 8)
# ──────────────────────────────────────────────────────────────────────────


def test_build_registry_registers_huggingface_anonymous(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    from open_pulse_crawler.platforms.huggingface.adapter import HuggingFaceAdapter
    monkeypatch.delenv("CRAWLER_TOKEN__HUGGINGFACE_CO", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__HUGGINGFACE_CO", raising=False)
    registry, _gh, missing = _build_registry(["huggingface.co"])
    assert "huggingface.co" in missing
    a = registry.adapter_for("https://huggingface.co/karpathy")
    assert isinstance(a, HuggingFaceAdapter)


def test_build_registry_registers_huggingface_with_token(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    from open_pulse_crawler.platforms.huggingface.adapter import HuggingFaceAdapter
    monkeypatch.setenv("CRAWLER_TOKEN__HUGGINGFACE_CO", "hf_test")
    registry, _gh, missing = _build_registry(["huggingface.co"])
    assert "huggingface.co" not in missing
    a = registry.adapter_for("https://huggingface.co/karpathy")
    assert isinstance(a, HuggingFaceAdapter)


def test_doctor_huggingface_without_token_reports_anonymous(monkeypatch):
    """HuggingFace is anonymous-friendly; doctor must show ANONYMOUS, not MISSING."""
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "huggingface.co")
    monkeypatch.delenv("CRAWLER_TOKEN__HUGGINGFACE_CO", raising=False)
    r = runner.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    row = next((p for p in payload if p["host"] == "huggingface.co"), None)
    assert row is not None
    assert row["tokens"] == 0
    assert row["auth_required"] is False
    assert row["ok"] is True


# ──────────────────────────────────────────────────────────────────────────
# enrich-crossref subcommand (Spec 6 — Crossref enrichment)
# ──────────────────────────────────────────────────────────────────────────


def _write_snapshot(path, graph) -> None:
    """Persist a ``GraphData`` to ``path`` exactly as ``export_to_json`` does."""
    from open_pulse_crawler.io_utils import export_to_json

    export_to_json(graph, path)


def _load_snapshot(path):
    """Re-load a snapshot JSON back into a ``GraphData``."""
    from open_pulse_crawler.models import GraphData

    data = json.loads(path.read_text())
    return GraphData(**data)


def test_enrich_crossref_help_lists_options():
    r, text = _help_text(app, ["enrich-crossref", "--help"])
    assert r.exit_code == 0, r.output
    assert "--input" in text
    assert "--output" in text
    assert "--expand" in text
    assert "--max-expand-depth" in text
    assert "--max-references-per-work" in text
    assert "--mailto" in text


def test_enrich_crossref_noop_on_graph_without_dangling_dois(tmp_path):
    """A graph with no dangling https://doi.org/... references enriches nothing,
    but still round-trips cleanly and writes a re-loadable snapshot."""
    from open_pulse_crawler.models import GraphData

    graph = GraphData()  # empty graph: no nodes, no dangling DOIs
    snap_in = tmp_path / "graph.json"
    snap_out = tmp_path / "graph.enriched.json"
    _write_snapshot(snap_in, graph)

    r = runner.invoke(
        app,
        ["enrich-crossref", "--input", str(snap_in), "--output", str(snap_out)],
    )
    assert r.exit_code == 0, r.output
    assert snap_out.exists()
    reloaded = _load_snapshot(snap_out)
    assert isinstance(reloaded, GraphData)
    # Summary must report nothing enriched.
    assert "enriched" in r.output
    assert "0" in r.output


def test_enrich_crossref_materializes_dangling_doi(tmp_path, monkeypatch):
    """A dangling https://doi.org/... reference inside a node gets materialized
    via a monkeypatched (no-network) Crossref client."""
    from open_pulse_crawler.models import DataCiteWork, CrossrefWork, GraphData
    import open_pulse_crawler.platforms.crossref as crossref_mod

    # A DataCiteWork carrying a dangling Crossref-owned DOI in its relations.
    host = DataCiteWork(
        url="https://doi.org/10.6084/m9.figshare.1",
        doi="10.6084/m9.figshare.1",
        relations=[{"id": "https://doi.org/10.1038/x"}],
    )
    graph = GraphData()
    graph.add_repo(host)
    snap_in = tmp_path / "graph.json"
    snap_out = tmp_path / "graph.enriched.json"
    _write_snapshot(snap_in, graph)

    prebuilt = CrossrefWork(
        url="https://doi.org/10.1038/x",
        doi="10.1038/x",
        title="T",
    )

    def _fake_fetch_work(self, doi):
        if doi == "10.1038/x":
            return prebuilt
        return None

    monkeypatch.setattr(
        crossref_mod.CrossrefClient, "fetch_work", _fake_fetch_work
    )

    r = runner.invoke(
        app,
        ["enrich-crossref", "--input", str(snap_in), "--output", str(snap_out)],
    )
    assert r.exit_code == 0, r.output
    reloaded = _load_snapshot(snap_out)
    assert "https://doi.org/10.1038/x" in reloaded.repos
    # Summary must report a non-zero enriched counter.
    # The table row looks like "enriched  | <N>" — find the line that contains
    # "enriched" and assert it holds a positive integer (would fail if N=0).
    enriched_line = next(
        (line for line in r.output.splitlines() if "enriched" in line),
        None,
    )
    assert enriched_line is not None, "No 'enriched' summary line in output"
    import re as _re_local
    numbers = [int(m) for m in _re_local.findall(r"\d+", enriched_line)]
    assert numbers and max(numbers) >= 1, (
        f"Expected enriched >= 1 in summary line, got: {enriched_line!r}"
    )


def test_enrich_crossref_invalid_input_exits_nonzero(tmp_path):
    """A nonexistent --input path produces a non-zero exit and a clear message."""
    missing = tmp_path / "does-not-exist.json"
    r = runner.invoke(app, ["enrich-crossref", "--input", str(missing)])
    assert r.exit_code != 0
    assert "does-not-exist.json" in r.output or "not" in r.output.lower()


def test_enrich_crossref_malformed_json_exits_nonzero(tmp_path):
    """A file containing malformed JSON produces a non-zero exit code.

    The command already catches ``json.JSONDecodeError`` and raises
    ``typer.Exit(1)`` — this test just covers that path.
    """
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{bad")
    r = runner.invoke(app, ["enrich-crossref", "--input", str(bad_json)])
    assert r.exit_code != 0
