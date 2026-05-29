"""Tiny live integration test against gitlab.com.

Skipped automatically when ``CRAWLER_TOKEN__GITLAB_COM`` is not set, so CI
without a GitLab PAT just won't run it. Run locally with::

    CRAWLER_TOKEN__GITLAB_COM=glpat-… uv run pytest tests/integration/test_gitlab_dryrun.py -v

This is a smoke test, not a behavioral assertion — the goal is to confirm
the adapter wiring talks to a real GitLab instance end-to-end. We crawl
the project page of ``gitlab-org/gitlab-foss`` for one round and assert
its node lands in the graph.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.environ.get("CRAWLER_TOKEN__GITLAB_COM"),
    reason="no CRAWLER_TOKEN__GITLAB_COM in env",
)
def test_one_round_against_gitlab_com() -> None:
    from open_pulse_crawler.config import resolve_tokens
    from open_pulse_crawler.crawler import GitHubCrawler
    from open_pulse_crawler.platforms import PlatformRegistry
    from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter
    from open_pulse_crawler.platforms.gitlab.client import GitLabClient

    tokens = resolve_tokens("gitlab.com")
    assert tokens, "expected CRAWLER_TOKEN__GITLAB_COM to be present"

    client = GitLabClient(host="gitlab.com", tokens=tokens)
    adapter = GitLabAdapter(client=client, instance_host="gitlab.com")

    registry = PlatformRegistry()
    registry.register(adapter)

    crawler = GitHubCrawler(registry=registry, max_rounds=1)
    crawler.add_seeds(["https://gitlab.com/gitlab-org/gitlab-foss"])
    crawler.crawl(show_progress=False)

    assert "https://gitlab.com/gitlab-org/gitlab-foss" in crawler.graph.repos
