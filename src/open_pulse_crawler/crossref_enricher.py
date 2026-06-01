"""Crossref enrichment pass over an already-crawled graph.

When the DataCite adapter cannot resolve a DOI it returns ``None`` and no
node is created — the DOI survives only as a *dangling string*
``https://doi.org/...`` nested somewhere inside another node's edge/relation
data. This module scans the graph for such referenced-but-unmaterialized
doi.org URLs and asks a Crossref client to materialize the ones Crossref
owns (journal articles / preprints), optionally expanding their references
to a bounded depth.

DOIs owned by a sibling adapter (Zenodo prefixes, see
:func:`open_pulse_crawler.platforms.datacite.is_owned_doi_url`) are excluded.

A CLI to drive this is a later task; this module is the engine only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol

from open_pulse_crawler.models import CrossrefWork, GraphData
from open_pulse_crawler.platforms.datacite import is_owned_doi_url

logger = logging.getLogger(__name__)

_DOI_URL_PREFIX = "https://doi.org/"


class _ClientLike(Protocol):
    def fetch_work(self, doi: str) -> Optional[CrossrefWork]: ...


@dataclass
class EnrichmentSummary:
    """Counters describing what an :meth:`CrossrefEnricher.enrich` run did."""

    enriched: int = 0
    skipped_404: int = 0
    skipped_owned: int = 0
    skipped_already_present: int = 0
    references_expanded: int = 0
    references_truncated: int = 0
    max_depth_reached: int = 0  # last expansion depth that actually ran (0 if no expansion happened)


class CrossrefEnricher:
    """Walk a graph and materialize Crossref-registered dangling DOIs.

    ``client`` is anything exposing
    ``fetch_work(doi: str) -> Optional[CrossrefWork]`` (a bare DOI in, a
    work or ``None`` out). Tests inject a fake.
    """

    def __init__(
        self,
        client: _ClientLike,
        *,
        expand: bool = True,
        max_expand_depth: int = 1,
        max_references_per_work: Optional[int] = None,
    ) -> None:
        self._client = client
        self.expand = expand
        self.max_expand_depth = max_expand_depth
        self.max_references_per_work = max_references_per_work
        # doi.org URLs already attempted this run — guards cycles + re-runs.
        self.visited: set[str] = set()

    # ---- public API ---------------------------------------------------------

    def enrich(self, graph: GraphData) -> EnrichmentSummary:
        """Mutate ``graph`` in place; return an :class:`EnrichmentSummary`."""
        summary = EnrichmentSummary()

        # Phase 1: materialize the dangling doi.org URLs already in the graph.
        frontier: List[CrossrefWork] = []
        for url in self._dangling_doi_urls(graph):
            if not self._is_eligible(graph, url, summary):
                continue
            work = self._enrich_one(graph, url, summary)
            if work is not None:
                frontier.append(work)

        # Phase 2: bounded expansion of each materialized work's references.
        if self.expand:
            for depth in range(1, self.max_expand_depth + 1):
                if not frontier:
                    break
                next_frontier: List[CrossrefWork] = []
                for work in frontier:
                    refs = work.references
                    if (
                        self.max_references_per_work is not None
                        and len(refs) > self.max_references_per_work
                    ):
                        dropped = len(refs) - self.max_references_per_work
                        logger.info(
                            "Truncating references for %s: keeping %d of %d "
                            "(dropping %d).",
                            work.url,
                            self.max_references_per_work,
                            len(refs),
                            dropped,
                        )
                        summary.references_truncated += dropped
                        refs = refs[: self.max_references_per_work]
                    for ref_url in refs:
                        if not self._is_eligible(graph, ref_url, summary):
                            continue
                        w = self._enrich_one(graph, ref_url, summary)
                        if w is not None:
                            summary.references_expanded += 1
                            next_frontier.append(w)
                summary.max_depth_reached = depth
                if not next_frontier:
                    break
                frontier = next_frontier

        return summary

    # ---- helpers ------------------------------------------------------------

    def _dangling_doi_urls(self, graph: GraphData) -> List[str]:
        """Field-agnostic scan for every ``https://doi.org/...`` string.

        Recursively walks each node's ``model_dump()`` (dicts, lists,
        scalars) so it stays robust to whichever field holds the reference.
        Returns a de-duplicated, order-stable list.
        """
        found: List[str] = []
        seen: set[str] = set()

        def _walk(obj: Any) -> None:
            if isinstance(obj, dict):
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, (list, tuple, set)):
                for v in obj:
                    _walk(v)
            elif isinstance(obj, str):
                if obj.startswith(_DOI_URL_PREFIX) and obj not in seen:
                    seen.add(obj)
                    found.append(obj)

        for node_dict in (graph.users, graph.orgs, graph.repos, graph.teams):
            for node in node_dict.values():
                _walk(node.model_dump())

        return found

    def _is_eligible(
        self, graph: GraphData, url: str, summary: EnrichmentSummary
    ) -> bool:
        """Decide whether ``url`` should be fetched, recording skip reasons.

        ``visited`` is checked first and silently (no counter) so re-walks of
        the same URL within a run — including the host node's own dangling
        scan hits — don't double-count skips.
        """
        if url in self.visited:
            return False
        if is_owned_doi_url(url):
            summary.skipped_owned += 1
            return False
        if graph.has_repo(url):
            summary.skipped_already_present += 1
            return False
        return True

    def _enrich_one(
        self, graph: GraphData, url: str, summary: EnrichmentSummary
    ) -> Optional[CrossrefWork]:
        """Fetch + add the work for ``url``; return it, or ``None`` on miss."""
        self.visited.add(url)
        bare_doi = url[len(_DOI_URL_PREFIX):]
        work = self._client.fetch_work(bare_doi)
        if work is None:
            summary.skipped_404 += 1
            return None
        graph.add_repo(work)
        summary.enriched += 1
        return work
