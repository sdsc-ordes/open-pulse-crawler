# src/open_pulse_crawler/platforms/datacite.py
"""Shared DataCite URL synthesizer for adapters that consume Zenodo /
Infoscience / other DataCite-flavored related-identifier vocabularies.

A related identifier is a (scheme, identifier) pair drawn from DataCite's
RelationType + identifier-scheme vocabularies, e.g.
``("doi", "10.5281/zenodo.42")`` or ``("arxiv", "2401.12345")``.

`synthesize_target_url(scheme, ident)` turns such a pair into the canonical
HTTPS URL the BFS engine can route by host, or returns ``None`` when the
identifier can't be made into a URL.
"""
from __future__ import annotations

from typing import Optional

from ..node_id import rewrite_zenodo_doi_url


def synthesize_target_url(scheme: str, ident: str) -> Optional[str]:
    """Turn a (scheme, identifier) pair from a DataCite-flavored
    ``related_identifiers`` entry into a full canonical URL.

    Returns ``None`` when the entry can't be made into a URL — those
    edges are dropped rather than emitted with a useless target.

    Supported schemes:
        url    pass through if already https://; otherwise drop
        doi    Zenodo DOIs → canonical platform URL; other DOIs → doi.org
        arxiv  "2401.12345" (or "arXiv:2401.12345") → arxiv.org/abs/<id>
        orcid  "0000-0002-1825-0097" → orcid.org/<id>
        pmid   "12345678" → pubmed.ncbi.nlm.nih.gov/<id>/
        pmcid  "PMC1234567" or "1234567" → ncbi.nlm.nih.gov/pmc/articles/PMC<id>/
        swh    "swh:1:dir:..." → archive.softwareheritage.org/<urn>

    Identifiers that already start with ``http(s)://`` pass through under
    any declared scheme — sources sometimes stamp the URL directly into
    the identifier regardless of the declared scheme.
    """
    if not ident:
        return None

    if ident.startswith(("http://", "https://")):
        return ident

    scheme = (scheme or "").lower()
    if scheme == "url":
        return None  # url scheme but identifier wasn't a URL — drop

    if scheme == "doi":
        rewritten = rewrite_zenodo_doi_url(f"https://doi.org/{ident}")
        return rewritten or f"https://doi.org/{ident}"

    if scheme == "arxiv":
        arxiv_id = ident
        if arxiv_id.lower().startswith("arxiv:"):
            arxiv_id = arxiv_id[len("arxiv:"):]
        arxiv_id = arxiv_id.strip()
        return f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None

    if scheme == "orcid":
        oid = ident
        if oid.lower().startswith("orcid:"):
            oid = oid[len("orcid:"):]
        oid = oid.strip().strip("/")
        return f"https://orcid.org/{oid}" if oid else None

    if scheme == "pmid":
        pid = ident.strip()
        return f"https://pubmed.ncbi.nlm.nih.gov/{pid}/" if pid else None

    if scheme == "pmcid":
        pid = ident.strip()
        if not pid:
            return None
        if not pid.upper().startswith("PMC"):
            pid = "PMC" + pid
        else:
            pid = "PMC" + pid[3:]  # canonical-case prefix
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pid}/"

    if scheme == "swh":
        return f"https://archive.softwareheritage.org/{ident.strip()}"

    return None
