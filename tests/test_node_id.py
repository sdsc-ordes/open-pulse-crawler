"""Tests for :mod:`open_pulse_crawler.node_id` — canonical URL node IDs."""

from __future__ import annotations

import pytest

from open_pulse_crawler.node_id import (
    NodeKind,
    canonical_url,
    extract_full_name,
    extract_login,
    extract_team_parts,
    host_of,
    is_team_url,
    kind_of,
    parse_seed,
    repo_url,
    team_url,
    user_url,
)


class TestCanonicalUrl:
    def test_basic_user_path(self):
        assert canonical_url("github.com", "/torvalds") == "https://github.com/torvalds"

    def test_basic_repo_path(self):
        assert (
            canonical_url("github.com", "/torvalds/linux")
            == "https://github.com/torvalds/linux"
        )

    def test_host_is_lowercased(self):
        assert canonical_url("GitHub.com", "/torvalds") == "https://github.com/torvalds"

    def test_path_case_is_preserved(self):
        assert (
            canonical_url("github.com", "/Torvalds/Linux")
            == "https://github.com/Torvalds/Linux"
        )

    def test_missing_leading_slash_is_added(self):
        assert canonical_url("github.com", "torvalds") == "https://github.com/torvalds"

    def test_trailing_slash_is_stripped(self):
        assert canonical_url("github.com", "/torvalds/") == "https://github.com/torvalds"

    def test_empty_host_rejected(self):
        with pytest.raises(ValueError):
            canonical_url("", "/torvalds")

    def test_idempotent(self):
        once = canonical_url("github.com", "/torvalds/linux")
        twice = canonical_url(host_of(once), "/" + once.split("/", 3)[3])
        assert once == twice


class TestHostOf:
    def test_returns_lowercase_host(self):
        assert host_of("https://GitHub.com/torvalds") == "github.com"

    def test_raises_when_no_host(self):
        with pytest.raises(ValueError):
            host_of("/torvalds")


class TestExtractLogin:
    def test_user_url(self):
        assert extract_login("https://github.com/torvalds") == "torvalds"

    def test_repo_url_returns_owner(self):
        assert extract_login("https://github.com/torvalds/linux") == "torvalds"

    def test_preserves_case(self):
        assert extract_login("https://github.com/Torvalds") == "Torvalds"

    def test_raises_when_no_path(self):
        with pytest.raises(ValueError):
            extract_login("https://github.com/")


class TestExtractFullName:
    def test_repo(self):
        assert (
            extract_full_name("https://github.com/torvalds/linux") == "torvalds/linux"
        )

    def test_preserves_case(self):
        assert (
            extract_full_name("https://github.com/Torvalds/Linux") == "Torvalds/Linux"
        )

    def test_rejects_user_url(self):
        with pytest.raises(ValueError):
            extract_full_name("https://github.com/torvalds")


class TestKindOf:
    def test_user_or_org(self):
        assert kind_of("https://github.com/torvalds") == NodeKind.USER_OR_ORG

    def test_repo(self):
        assert kind_of("https://github.com/torvalds/linux") == NodeKind.REPO

    def test_empty_path_rejected(self):
        with pytest.raises(ValueError):
            kind_of("https://github.com/")

    def test_unknown_shape_rejected(self):
        # Three segments that aren't a team URL — caller must disambiguate.
        with pytest.raises(ValueError):
            kind_of("https://github.com/a/b/c")


class TestBuilders:
    def test_user_url_default_host(self):
        assert user_url("torvalds") == "https://github.com/torvalds"

    def test_user_url_rejects_slash(self):
        with pytest.raises(ValueError):
            user_url("tor/valds")

    def test_repo_url(self):
        assert repo_url("torvalds/linux") == "https://github.com/torvalds/linux"

    def test_repo_url_rejects_single_segment(self):
        with pytest.raises(ValueError):
            repo_url("torvalds")

    def test_team_url(self):
        assert (
            team_url("sdsc-ordes", "core")
            == "https://github.com/orgs/sdsc-ordes/teams/core"
        )

    def test_team_url_rejects_invalid(self):
        with pytest.raises(ValueError):
            team_url("sdsc-ordes", "")
        with pytest.raises(ValueError):
            team_url("", "core")


class TestTeamHelpers:
    def test_is_team_url(self):
        assert is_team_url("https://github.com/orgs/acme/teams/core") is True
        assert is_team_url("https://github.com/acme") is False
        assert is_team_url("https://github.com/acme/repo") is False

    def test_extract_team_parts(self):
        assert (
            extract_team_parts("https://github.com/orgs/acme/teams/core")
            == ("acme", "core")
        )

    def test_extract_team_parts_rejects_non_team(self):
        with pytest.raises(ValueError):
            extract_team_parts("https://github.com/acme/repo")


class TestParseSeed:
    def test_bare_login(self):
        assert parse_seed("torvalds") == (
            NodeKind.USER_OR_ORG,
            "https://github.com/torvalds",
        )

    def test_owner_repo(self):
        assert parse_seed("torvalds/linux") == (
            NodeKind.REPO,
            "https://github.com/torvalds/linux",
        )

    def test_full_url(self):
        assert parse_seed("https://github.com/torvalds/linux") == (
            NodeKind.REPO,
            "https://github.com/torvalds/linux",
        )

    def test_normalizes_uppercase_host(self):
        assert parse_seed("https://GitHub.com/torvalds") == (
            NodeKind.USER_OR_ORG,
            "https://github.com/torvalds",
        )

    def test_trailing_slash_stripped(self):
        assert parse_seed("https://github.com/torvalds/") == (
            NodeKind.USER_OR_ORG,
            "https://github.com/torvalds",
        )

    def test_http_normalized_to_https(self):
        assert parse_seed("http://github.com/torvalds") == (
            NodeKind.USER_OR_ORG,
            "https://github.com/torvalds",
        )

    def test_path_case_preserved(self):
        assert parse_seed("https://github.com/Torvalds/Linux") == (
            NodeKind.REPO,
            "https://github.com/Torvalds/Linux",
        )

    def test_team_url_recognised(self):
        assert parse_seed("https://github.com/orgs/acme/teams/core") == (
            NodeKind.TEAM,
            "https://github.com/orgs/acme/teams/core",
        )

    def test_empty_rejected(self):
        for bad in ("", "   ", "\t\n"):
            with pytest.raises(ValueError):
                parse_seed(bad)

    def test_none_rejected(self):
        with pytest.raises(ValueError):
            parse_seed(None)  # type: ignore[arg-type]

    def test_url_without_host_rejected(self):
        with pytest.raises(ValueError):
            parse_seed("https:///torvalds")

    def test_idempotent_on_canonical_input(self):
        for raw in (
            "torvalds",
            "torvalds/linux",
            "https://github.com/torvalds",
            "https://github.com/torvalds/linux",
            "https://github.com/orgs/acme/teams/core",
        ):
            _, url = parse_seed(raw)
            assert parse_seed(url) == parse_seed(raw)

    def test_default_host_can_be_overridden(self):
        """Per-platform dispatch needs a way to mint URLs for other hosts."""
        assert parse_seed("torvalds", default_host="gitlab.com") == (
            NodeKind.USER_OR_ORG,
            "https://gitlab.com/torvalds",
        )


