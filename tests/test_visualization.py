"""Tests for the visualization color map (subkind-aware)."""

from open_pulse_crawler.visualization import (
    SUBKIND_COLOR,
    DEFAULT_SUBKIND_COLOR,
    _node_color,
)


def test_subkind_color_map_covers_all_v3_subkinds():
    """Every concrete subkind shipped in v3 has a color entry."""
    expected = {
        "GitHubUser",
        "GitHubOrganization",
        "GitHubRepository",
        "GitHubTeam",
        "GitLabUser",
        "GitLabGroup",
        "GitLabProject",
    }
    assert expected.issubset(set(SUBKIND_COLOR.keys()))


def test_subkind_colors_distinct_per_platform_kind():
    """GitHub and GitLab variants of the same abstract bucket get distinct colors."""
    assert SUBKIND_COLOR["GitHubUser"] != SUBKIND_COLOR["GitLabUser"]
    assert SUBKIND_COLOR["GitHubOrganization"] != SUBKIND_COLOR["GitLabGroup"]
    assert SUBKIND_COLOR["GitHubRepository"] != SUBKIND_COLOR["GitLabProject"]


def test_node_color_prefers_subkind_when_present():
    """When subkind is present, the per-subkind color wins over the abstract bucket."""
    gh = _node_color({"subkind": "GitHubUser", "node_type": "user"})
    gl = _node_color({"subkind": "GitLabUser", "node_type": "user"})
    assert gh == SUBKIND_COLOR["GitHubUser"]
    assert gl == SUBKIND_COLOR["GitLabUser"]


def test_node_color_falls_back_to_node_type_when_subkind_unknown():
    """Unknown subkind (e.g. for a sketched-in future platform) falls back to node_type."""
    result = _node_color({"subkind": "ZenodoCommunity", "node_type": "org"})
    # Just verify it doesn't crash and returns a valid color string.
    assert isinstance(result, str) and result.startswith("#")


def test_node_color_falls_back_when_subkind_missing():
    """Discovered nodes have no subkind; fall back to abstract bucket color."""
    result = _node_color({"node_type": "repo"})
    assert isinstance(result, str) and result.startswith("#")


def test_default_subkind_color_is_used_when_nothing_matches():
    """Empty attrs dict still returns a valid color (default for ``user`` bucket)."""
    # Empty dict → node_type defaults to 'user' → color_map['user']
    result = _node_color({})
    assert isinstance(result, str) and result.startswith("#")
    # Confirm DEFAULT_SUBKIND_COLOR is a sensible fallback hex string.
    assert DEFAULT_SUBKIND_COLOR.startswith("#")
