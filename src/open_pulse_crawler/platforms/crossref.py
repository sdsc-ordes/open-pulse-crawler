"""Thin httpx wrapper for Crossref's REST API and JSON→CrossrefWork mapper.

Crossref is a DOI registration agency for journal articles, conference papers,
books, and preprints. Its public API at ``api.crossref.org/works/{doi}`` returns
a JSON envelope ``{"status":"ok","message":{...}}`` where ``message`` holds the
bibliographic metadata.

Polite pool: callers are encouraged to set ``CRAWLER_CROSSREF_MAILTO`` so the
server can contact them in case of abuse. When the mailto is known, it is sent
as both a ``mailto=`` query param and embedded in the ``User-Agent`` header,
routing requests to a faster, dedicated pool.

Rate-limit handling: on HTTP 429 the client reads the ``Retry-After`` header
(or defaults to 5 seconds), sleeps, and retries once. A second 429 raises
``httpx.HTTPStatusError``.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from open_pulse_crawler import config
from open_pulse_crawler.models import CrossrefWork

logger = logging.getLogger(__name__)

# Cap honored for Retry-After — avoid hanging on a rogue server value.
MAX_RETRY_AFTER_SECONDS = 60

# One-shot warning flag: emit a single warning per process when no mailto
# is configured. Tests reset this to False to re-arm the warning.
_no_mailto_warned: bool = False

_BASE_UA_NO_MAILTO = "OpenPulseCrawler (+https://openpulse.science)"


class CrossrefClient:
    """HTTP client for ``api.crossref.org``.

    Polite-pool routing: when ``mailto`` is provided (or resolved from
    ``CRAWLER_CROSSREF_MAILTO``), requests include ``mailto=<addr>`` as a
    query parameter and an enriched ``User-Agent`` header. Without a mailto
    the public pool is used and a one-time warning is logged.

    Rate-limit handling: HTTP 429 is retried once after honoring
    ``Retry-After`` (capped at :data:`MAX_RETRY_AFTER_SECONDS`). A second
    429 raises ``httpx.HTTPStatusError``.
    """

    def __init__(
        self,
        mailto: Optional[str] = None,
        *,
        base_url: str = "https://api.crossref.org",
    ) -> None:
        global _no_mailto_warned

        # Resolve mailto: explicit arg → env var → None (public pool).
        if mailto is None:
            mailto = config.resolve_crossref_mailto()

        self._mailto: Optional[str] = mailto

        if self._mailto:
            user_agent = f"OpenPulseCrawler (+https://openpulse.science; mailto:{self._mailto})"
        else:
            user_agent = _BASE_UA_NO_MAILTO
            if not _no_mailto_warned:
                _no_mailto_warned = True
                logger.warning(
                    "CrossrefClient: no mailto address configured. "
                    "Set CRAWLER_CROSSREF_MAILTO for the Crossref polite pool "
                    "(faster rate limits). Proceeding with the public pool."
                )

        self._session = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": user_agent,
            },
        )

    # ---- low-level GET with 429 retry ---------------------------------------

    def _do_get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        _retried: bool = False,
    ) -> httpx.Response:
        """GET ``path`` with one automatic retry on HTTP 429.

        Honors the ``Retry-After`` header (capped at
        :data:`MAX_RETRY_AFTER_SECONDS`). A second 429 is returned as-is so
        the caller can raise via ``raise_for_status``.
        """
        kwargs: Dict[str, Any] = {}
        if params is not None:
            kwargs["params"] = params
        resp = self._session.get(path, **kwargs)
        if resp.status_code == 429 and not _retried:
            retry_after_raw = resp.headers.get("Retry-After", "5")
            try:
                retry_after = float(retry_after_raw)
            except (TypeError, ValueError):
                retry_after = 5.0
            retry_after = min(max(retry_after, 0.0), MAX_RETRY_AFTER_SECONDS)
            logger.warning(
                "%s returned 429; sleeping %.1fs then retrying once.",
                path,
                retry_after,
            )
            time.sleep(retry_after)
            return self._do_get(path, params=params, _retried=True)
        return resp

    # ---- public API ---------------------------------------------------------

    def fetch_work(self, doi: str) -> Optional[CrossrefWork]:
        """Return a :class:`CrossrefWork` for ``doi``, or ``None``.

        ``doi`` must be a bare DOI like ``10.1038/s41586-021-03819-2``.

        Returns ``None`` when:
        - The server responds with HTTP 404 (DOI not registered with Crossref).
        - The response envelope's ``status`` field is not ``"ok"``.
        - The ``message`` key is absent from the envelope.
        """
        # URL-quote the DOI path segment so slashes and special chars are safe.
        encoded_doi = quote(doi, safe="")
        path = f"/works/{encoded_doi}"

        params: Optional[Dict[str, Any]] = None
        if self._mailto:
            params = {"mailto": self._mailto}

        resp = self._do_get(path, params=params)

        if resp.status_code == 404:
            return None

        if not resp.is_success:
            resp.raise_for_status()

        body = resp.json()
        if body.get("status") != "ok":
            return None
        message = body.get("message")
        if not message:
            return None

        return crossref_message_to_work(message)


# ---------------------------------------------------------------------------
# Mapper: Crossref message dict → CrossrefWork
# ---------------------------------------------------------------------------

def crossref_message_to_work(message: dict) -> CrossrefWork:
    """Map a Crossref ``message`` object to a :class:`CrossrefWork`.

    Be defensive: any field may be absent; defaults to empty string / list /
    None as appropriate.
    """
    # DOI and canonical URL
    doi = message.get("DOI", "").lower()
    url = f"https://doi.org/{doi}"

    # Title: first element of a list
    title_list: List[str] = message.get("title", [])
    title = title_list[0] if title_list else ""

    # Publication year: first available from a prioritized chain of date fields
    publication_year: Optional[int] = None
    for date_field in ("published", "published-print", "published-online", "issued"):
        date_obj = message.get(date_field)
        if date_obj:
            date_parts = date_obj.get("date-parts", [])
            if date_parts and date_parts[0]:
                try:
                    publication_year = int(date_parts[0][0])
                except (TypeError, ValueError, IndexError):
                    pass
                else:
                    break

    publisher: str = message.get("publisher", "")

    # Container title: first element
    container_list: List[str] = message.get("container-title", [])
    container_title = container_list[0] if container_list else ""

    work_type: str = message.get("type", "")
    abstract: str = message.get("abstract", "")
    creators: List[Dict[str, Any]] = message.get("author", [])
    subjects: List[str] = message.get("subject", [])
    funders: List[Dict[str, Any]] = message.get("funder", [])
    is_referenced_by_count: Optional[int] = message.get("is-referenced-by-count")

    # References: collect DOIs from reference entries, lowercase, deduplicate
    # while preserving order.
    reference_dois: List[str] = []
    seen: set = set()
    for ref in message.get("reference", []):
        ref_doi = ref.get("DOI")
        if ref_doi:
            ref_doi_lower = ref_doi.lower()
            if ref_doi_lower not in seen:
                seen.add(ref_doi_lower)
                reference_dois.append(ref_doi_lower)
    references = [f"https://doi.org/{d}" for d in reference_dois]

    return CrossrefWork(
        url=url,
        full_name=url,  # RepoModel requires full_name; use the URL as canonical name
        doi=doi,
        title=title,
        publication_year=publication_year,
        publisher=publisher,
        container_title=container_title,
        work_type=work_type,
        abstract=abstract,
        creators=creators,
        subjects=subjects,
        funders=funders,
        is_referenced_by_count=is_referenced_by_count,
        references=references,
        reference_dois=reference_dois,
        relations=[],
    )
