"""Tests for the Crossref enrichment pass (Task C)."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pytest

from open_pulse_crawler.crossref_enricher import (
    CrossrefEnricher,
    EnrichmentSummary,
)
from open_pulse_crawler.models import CrossrefWork, DataCiteWork, GraphData


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------


class FakeCrossrefClient:
    """Returns prebuilt CrossrefWorks keyed by bare DOI, else None."""

    def __init__(self, works: Dict[str, CrossrefWork]) -> None:
        # keyed by bare doi
        self._works = works
        self.calls: List[str] = []

    def fetch_work(self, doi: str) -> Optional[CrossrefWork]:
        self.calls.append(doi)
        return self._works.get(doi)


def make_work(doi: str, references: Optional[List[str]] = None) -> CrossrefWork:
    url = f"https://doi.org/{doi}"
    return CrossrefWork(
        url=url,
        full_name=url,
        doi=doi,
        references=references or [],
    )


def dangling_repo(url: str, ref_url: str) -> DataCiteWork:
    """A repo node whose dumped data nests a dangling doi.org URL in relations."""
    return DataCiteWork(
        url=url,
        full_name=url,
        doi=url.replace("https://doi.org/", ""),
        relations=[{"type": "References", "id": ref_url}],
    )


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------


def test_dangling_scan_finds_nested_doi_url():
    graph = GraphData()
    graph.add_repo(dangling_repo("https://doi.org/10.1000/host", "https://doi.org/10.1038/x"))

    enricher = CrossrefEnricher(FakeCrossrefClient({}))
    found = enricher._dangling_doi_urls(graph)

    assert "https://doi.org/10.1038/x" in found
    # the host node's own url is also a doi.org url and should be picked up
    assert "https://doi.org/10.1000/host" in found


def test_skipped_owned_zenodo_doi():
    graph = GraphData()
    graph.add_repo(
        dangling_repo("https://doi.org/10.1000/host", "https://doi.org/10.5281/zenodo.42")
    )
    client = FakeCrossrefClient({})
    enricher = CrossrefEnricher(client, expand=False)
    summary = enricher.enrich(graph)

    assert summary.skipped_owned >= 1
    # zenodo doi never fetched
    assert "10.5281/zenodo.42" not in client.calls


def test_skipped_already_present():
    graph = GraphData()
    present_url = "https://doi.org/10.1038/already"
    graph.add_repo(make_work("10.1038/already"))  # already materialized
    graph.add_repo(dangling_repo("https://doi.org/10.1000/host", present_url))

    client = FakeCrossrefClient({})
    enricher = CrossrefEnricher(client, expand=False)
    summary = enricher.enrich(graph)

    assert summary.skipped_already_present >= 1
    assert "10.1038/already" not in client.calls


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------


def test_phase1_hit_materializes_node():
    target_url = "https://doi.org/10.1038/hit"
    graph = GraphData()
    graph.add_repo(dangling_repo("https://doi.org/10.1000/host", target_url))

    client = FakeCrossrefClient({"10.1038/hit": make_work("10.1038/hit")})
    enricher = CrossrefEnricher(client, expand=False)
    summary = enricher.enrich(graph)

    assert summary.enriched == 1
    assert graph.has_repo(target_url)
    assert graph.repos[target_url].subkind == "CrossrefWork"


def test_phase1_miss_is_404():
    target_url = "https://doi.org/10.1038/miss"
    graph = GraphData()
    graph.add_repo(dangling_repo("https://doi.org/10.1000/host", target_url))

    client = FakeCrossrefClient({})  # nothing → None
    enricher = CrossrefEnricher(client, expand=False)
    summary = enricher.enrich(graph)

    assert summary.skipped_404 == 1
    assert summary.enriched == 0
    assert not graph.has_repo(target_url)


# ---------------------------------------------------------------------------
# Phase 2 — expand
# ---------------------------------------------------------------------------


def test_phase2_expand_materializes_reference():
    a_url = "https://doi.org/10.1038/a"
    b_url = "https://doi.org/10.1038/b"
    graph = GraphData()
    graph.add_repo(dangling_repo("https://doi.org/10.1000/host", a_url))

    client = FakeCrossrefClient(
        {
            "10.1038/a": make_work("10.1038/a", references=[b_url]),
            "10.1038/b": make_work("10.1038/b"),
        }
    )
    enricher = CrossrefEnricher(client, expand=True, max_expand_depth=1)
    summary = enricher.enrich(graph)

    assert summary.references_expanded >= 1
    assert graph.has_repo(b_url)


def test_phase2_no_expand_when_disabled():
    a_url = "https://doi.org/10.1038/a"
    b_url = "https://doi.org/10.1038/b"
    graph = GraphData()
    graph.add_repo(dangling_repo("https://doi.org/10.1000/host", a_url))

    client = FakeCrossrefClient(
        {
            "10.1038/a": make_work("10.1038/a", references=[b_url]),
            "10.1038/b": make_work("10.1038/b"),
        }
    )
    enricher = CrossrefEnricher(client, expand=False)
    summary = enricher.enrich(graph)

    assert graph.has_repo(a_url)
    assert not graph.has_repo(b_url)
    assert summary.references_expanded == 0


# ---------------------------------------------------------------------------
# max_references_per_work
# ---------------------------------------------------------------------------


def test_max_references_per_work_truncates(caplog):
    a_url = "https://doi.org/10.1038/a"
    refs = [
        "https://doi.org/10.1038/r1",
        "https://doi.org/10.1038/r2",
        "https://doi.org/10.1038/r3",
    ]
    graph = GraphData()
    graph.add_repo(dangling_repo("https://doi.org/10.1000/host", a_url))

    client = FakeCrossrefClient(
        {
            "10.1038/a": make_work("10.1038/a", references=refs),
            "10.1038/r1": make_work("10.1038/r1"),
            "10.1038/r2": make_work("10.1038/r2"),
            "10.1038/r3": make_work("10.1038/r3"),
        }
    )
    enricher = CrossrefEnricher(
        client, expand=True, max_expand_depth=1, max_references_per_work=1
    )
    with caplog.at_level(logging.INFO, logger="open_pulse_crawler.crossref_enricher"):
        summary = enricher.enrich(graph)

    assert summary.references_expanded == 1
    assert summary.references_truncated == 2
    # exactly one of the three refs materialized
    materialized = [u for u in refs if graph.has_repo(u)]
    assert len(materialized) == 1
    # truncation was logged
    assert any("trunc" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------------------------------------
# max_expand_depth bound
# ---------------------------------------------------------------------------


def test_max_expand_depth_bound():
    a_url = "https://doi.org/10.1038/a"
    b_url = "https://doi.org/10.1038/b"
    c_url = "https://doi.org/10.1038/c"
    graph = GraphData()
    graph.add_repo(dangling_repo("https://doi.org/10.1000/host", a_url))

    client = FakeCrossrefClient(
        {
            "10.1038/a": make_work("10.1038/a", references=[b_url]),
            "10.1038/b": make_work("10.1038/b", references=[c_url]),
            "10.1038/c": make_work("10.1038/c"),
        }
    )
    enricher = CrossrefEnricher(client, expand=True, max_expand_depth=1)
    summary = enricher.enrich(graph)

    assert graph.has_repo(b_url)
    assert not graph.has_repo(c_url)
    assert summary.max_depth_reached == 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_fresh_enricher_second_run():
    a_url = "https://doi.org/10.1038/a"
    graph = GraphData()
    graph.add_repo(dangling_repo("https://doi.org/10.1000/host", a_url))
    client = FakeCrossrefClient({"10.1038/a": make_work("10.1038/a")})

    s1 = CrossrefEnricher(client, expand=False).enrich(graph)
    assert s1.enriched == 1

    repo_count = len(graph.repos)
    # fresh enricher, second run: everything already present → no new nodes
    s2 = CrossrefEnricher(client, expand=False).enrich(graph)
    assert s2.enriched == 0
    assert len(graph.repos) == repo_count


def test_summary_is_dataclass():
    s = EnrichmentSummary()
    assert s.enriched == 0
    assert s.skipped_404 == 0
    assert s.skipped_owned == 0
    assert s.skipped_already_present == 0
    assert s.references_expanded == 0
    assert s.references_truncated == 0
    assert s.max_depth_reached == 0


# ---------------------------------------------------------------------------
# max_depth_reached stays 0 when Phase-1 frontier is empty
# ---------------------------------------------------------------------------


def test_max_depth_reached_zero_when_frontier_empty():
    """Phase-1 produces an empty frontier (fetch_work → None); max_depth_reached must stay 0."""
    target_url = "https://doi.org/10.1038/ghost"
    graph = GraphData()
    graph.add_repo(dangling_repo("https://doi.org/10.1000/host", target_url))

    # client returns None for every doi → frontier after Phase 1 is empty
    client = FakeCrossrefClient({})
    summary = CrossrefEnricher(client, expand=True, max_expand_depth=3).enrich(graph)

    assert summary.max_depth_reached == 0
