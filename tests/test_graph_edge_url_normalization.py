"""Finding A: /api/v*/graph must return edge-list endpoints as canonical URLs.

Node *keys* were already URLs, but per-node edge-list fields
(contributors/members/followers/starred_repositories/…) used to come back as
bare shorthand, forcing every consumer to re-normalize (host-aware). The
graph response now normalizes them at the boundary using each node's own
host, so the response is fully URL-joined.
"""
from __future__ import annotations

from open_pulse_crawler.api.deps import normalize_graph_edge_urls


def test_github_user_edge_fields_normalized_to_urls():
    graph = {
        "schema_version": 3,
        "users": {
            "https://github.com/marftn": {
                "url": "https://github.com/marftn",
                "login": "marftn",
                "followers": ["caviri"],
                "starred_repositories": ["sdsc-ordes/gimie"],
                "following": [],
            }
        },
        "orgs": {},
        "repos": {},
        "teams": {},
    }
    out = normalize_graph_edge_urls(graph)
    u = out["users"]["https://github.com/marftn"]
    assert u["followers"] == ["https://github.com/caviri"]
    assert u["starred_repositories"] == ["https://github.com/sdsc-ordes/gimie"]


def test_normalization_is_host_aware_not_github_centric():
    """A GitLab node's contributors resolve to the GitLab host, NOT github.com."""
    graph = {
        "users": {}, "orgs": {}, "teams": {},
        "repos": {
            "https://gitlab.epfl.ch/grp/proj": {
                "url": "https://gitlab.epfl.ch/grp/proj",
                "full_name": "grp/proj",
                "contributors": ["bovel"],
                "dependencies": ["grp/sub/lib"],  # multi-segment GitLab path
                "forked_from": "grp/upstream",
            }
        },
    }
    out = normalize_graph_edge_urls(graph)
    r = out["repos"]["https://gitlab.epfl.ch/grp/proj"]
    assert r["contributors"] == ["https://gitlab.epfl.ch/bovel"]
    assert r["dependencies"] == ["https://gitlab.epfl.ch/grp/sub/lib"]
    assert r["forked_from"] == "https://gitlab.epfl.ch/grp/upstream"


def test_non_ref_fields_and_dict_lists_untouched():
    """Typed dict-lists (authors) and scalar lists (tags) must NOT be rewritten."""
    graph = {
        "users": {}, "orgs": {}, "teams": {},
        "repos": {
            "https://huggingface.co/papers/2307.09288": {
                "url": "https://huggingface.co/papers/2307.09288",
                "authors": [{"name": "Hugo Touvron"}],
                "ai_keywords": ["llm", "fine-tuning"],
                "tags": ["transformers"],
            }
        },
    }
    out = normalize_graph_edge_urls(graph)
    r = out["repos"]["https://huggingface.co/papers/2307.09288"]
    assert r["authors"] == [{"name": "Hugo Touvron"}]
    assert r["ai_keywords"] == ["llm", "fine-tuning"]
    assert r["tags"] == ["transformers"]


def test_already_url_entries_are_idempotent():
    graph = {
        "users": {
            "https://github.com/x": {
                "url": "https://github.com/x",
                "followers": ["https://github.com/already"],
            }
        },
        "orgs": {}, "repos": {}, "teams": {},
    }
    out = normalize_graph_edge_urls(graph)
    assert out["users"]["https://github.com/x"]["followers"] == [
        "https://github.com/already"
    ]
