"""Tiny live dryrun against huggingface.co.

Anonymous-friendly — runs whenever ``CRAWLER_SKIP_INTEGRATION`` is unset.
Skipped when explicitly opted out (e.g., offline CI).

Hits the live HuggingFace API with a 2-second delay between requests to
stay well under the public rate limit. Verifies the cross-platform
paper bridge end-to-end: arxiv URL + github URL + at least one linked HF repo.
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("CRAWLER_SKIP_INTEGRATION") == "1",
    reason="CRAWLER_SKIP_INTEGRATION=1 set",
)
def test_fetch_real_huggingface_paper_llama2() -> None:
    """Walk the Llama 2 paper to its arxiv / github / linked HF repos.

    The Llama 2 paper (arxiv:2307.09288) is a stable seed:
      - Has githubRepo populated (facebookresearch/llama)
      - Has multiple linkedModels (meta-llama/Llama-2-7b, …)
      - Public anonymous read.
    """
    from open_pulse_crawler.platforms.huggingface.adapter import HuggingFaceAdapter
    from open_pulse_crawler.platforms.huggingface.client import HuggingFaceHTTPClient
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import HuggingFacePaper

    client = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    adapter = HuggingFaceAdapter(client=client, instance_host="huggingface.co")

    seed = "https://huggingface.co/papers/2307.09288"

    # 1. Fetch the paper.
    paper = adapter.fetch(seed)
    if paper is None:
        pytest.skip("Llama 2 paper returned 404 — DNS/network issue or HF API change.")
    assert isinstance(paper, HuggingFacePaper)
    assert paper.arxiv_id == "2307.09288"
    assert paper.arxiv_url == "https://arxiv.org/abs/2307.09288"

    # 2. Walk one round of edges.
    time.sleep(2.0)
    edges = list(adapter.expand(paper, ExpandOpts()))

    by_kind = {}
    for e in edges:
        by_kind.setdefault(e.kind, []).append(e.dst)

    # arxiv edge MUST be present (synthesize_target_url is deterministic).
    assert "related_to.IsIdenticalTo" in by_kind
    assert by_kind["related_to.IsIdenticalTo"] == ["https://arxiv.org/abs/2307.09288"]

    # github_repo MAY be empty on a future API revision; fall through if so.
    if paper.github_repo:
        assert "related_to.IsSupplementedBy" in by_kind
        assert by_kind["related_to.IsSupplementedBy"][0].startswith("https://github.com/")

    # At least one linked HF repo edge should exist (the paper has 8+ linkedModels at writing time).
    repo_kinds = ("references_model", "references_dataset", "references_space")
    total_linked = sum(len(by_kind.get(k, [])) for k in repo_kinds)
    if total_linked == 0:
        pytest.skip(
            "Llama 2 paper unexpectedly returned 0 linkedModels/Datasets/Spaces — "
            "HF API may have changed shape; investigate."
        )
    assert total_linked >= 1
