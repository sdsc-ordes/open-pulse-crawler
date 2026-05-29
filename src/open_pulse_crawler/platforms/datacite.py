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

import re as _re
from typing import Dict, Optional, Tuple


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
        return rewrite_doi_url(ident) or f"https://doi.org/{ident}"

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


# ---- DOI prefix routing -----------------------------------------------------
# Maps DOI prefix → (canonical host, URL template, optional suffix regex).
# The URL template uses {suffix} for the post-prefix tail (or the first capture
# group of the suffix regex when one is supplied).
# When a DOI matches, BFS dispatch routes to the sibling adapter (Zenodo today;
# future Spec entries can add Infoscience or Crossref handlers here).
#
# Tuple shape: (canonical_host, url_template, suffix_re_or_None)
#   suffix_re: compiled regex applied to the raw suffix (everything after the
#              prefix slash).  When provided, group(1) is used as {suffix}.
#              When None, the raw suffix is used verbatim.
_DOI_PREFIX_REWRITERS: Dict[str, Tuple[str, str, Optional["_re.Pattern[str]"]]] = {
    "10.5281": (
        "zenodo.org",
        "https://zenodo.org/records/{suffix}",
        _re.compile(r"^zenodo\.(\d+)/?$", _re.IGNORECASE),
    ),
    "10.5072": (
        "sandbox.zenodo.org",
        "https://sandbox.zenodo.org/records/{suffix}",
        _re.compile(r"^zenodo\.(\d+)/?$", _re.IGNORECASE),
    ),
    # 10.5075 (EPFL) intentionally absent — opaque suffixes, no formulaic mapping
    # to Infoscience handles. Those DOIs flow through DataCite.
}


def rewrite_doi_url(doi_or_url: str) -> Optional[str]:
    """Map a doi.org URL or bare DOI to a sibling-adapter platform URL when
    the DOI's prefix is owned by another adapter (Zenodo today).

    Returns ``None`` to leave the DOI with DataCite. Accepts both forms:

        rewrite_doi_url("https://doi.org/10.5281/zenodo.42")  → "https://zenodo.org/records/42"
        rewrite_doi_url("10.5281/zenodo.42")                  → "https://zenodo.org/records/42"
        rewrite_doi_url("10.6084/m9.figshare.99")             → None
    """
    if not isinstance(doi_or_url, str) or not doi_or_url:
        return None
    # Strip doi.org URL form to bare DOI.
    bare = doi_or_url
    for prefix_url in ("https://doi.org/", "http://doi.org/"):
        if bare.startswith(prefix_url):
            bare = bare[len(prefix_url):]
            break
    # Bare DOI must look like "10.<prefix>/<suffix>".
    if "/" not in bare:
        return None
    prefix, _, raw_suffix = bare.partition("/")
    if not prefix.startswith("10.") or not raw_suffix:
        return None
    entry = _DOI_PREFIX_REWRITERS.get(prefix)
    if not entry:
        return None
    _host, template, suffix_re = entry
    if suffix_re is not None:
        m = suffix_re.match(raw_suffix)
        if not m:
            return None
        suffix = m.group(1)
    else:
        suffix = raw_suffix
    return template.format(suffix=suffix)


def is_owned_doi_url(raw: str) -> bool:
    """True iff ``rewrite_doi_url(raw)`` would return a non-None URL.

    Only matches the ``doi.org`` URL form (so already-rewritten Zenodo URLs
    return False — they're "owned" by Zenodo's host, not by the DOI prefix).
    """
    if not isinstance(raw, str):
        return False
    if not (raw.startswith("https://doi.org/") or raw.startswith("http://doi.org/")):
        return False
    return rewrite_doi_url(raw) is not None
