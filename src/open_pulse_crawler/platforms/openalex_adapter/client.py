"""Thin httpx wrapper for the OpenAlex REST API.

OpenAlex (``api.openalex.org``) is an open catalog of scholarly works,
authors, institutions, sources (venues), and funders. Single-entity GETs
return the entity object directly (no envelope); list endpoints return
``{"results": [...], "meta": {"next_cursor": ...}}`` and are paged with
cursor pagination (``cursor=*`` then ``meta.next_cursor`` until null/empty).

Polite pool: callers are encouraged to set ``CRAWLER_OPENALEX_MAILTO`` so the
server can contact them. When the mailto is known it is sent as both a
``mailto=`` query param and embedded in the ``User-Agent`` header, routing
requests to the faster polite pool. Without a mailto a one-time warning is
logged and the public pool is used.

Rate-limit handling: on HTTP 429 the client reads the ``Retry-After`` header
(or defaults to 5 seconds, capped at :data:`MAX_RETRY_AFTER_SECONDS`), sleeps,
and retries once. A second 429 raises ``httpx.HTTPStatusError``.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

import httpx

from open_pulse_crawler import config

logger = logging.getLogger(__name__)

# Cap honored for Retry-After — avoid hanging on a rogue server value.
MAX_RETRY_AFTER_SECONDS = 60

# OpenAlex list page size cap.
LIST_PER_PAGE = 200

# Max ids per ids.openalex batched filter (OpenAlex allows up to 50 per OR group).
BATCH_SIZE = 50

# One-shot warning flag: emit a single warning per process when no mailto is
# configured. Tests reset this to False to re-arm the warning.
_no_mailto_warned: bool = False

_BASE_UA_NO_MAILTO = "OpenPulseCrawler (+https://openpulse.science)"

_OPENALEX_URL_PREFIX = "https://openalex.org/"
_DOI_URL_PREFIX = "https://doi.org/"


def _bare_work_id(work_id: str) -> str:
    """Strip any ``https://openalex.org/`` prefix, returning the bare ``W…`` id."""
    if work_id.startswith(_OPENALEX_URL_PREFIX):
        return work_id[len(_OPENALEX_URL_PREFIX):]
    return work_id


class OpenAlexHTTPClient:
    """HTTP client for ``api.openalex.org``.

    Polite-pool routing: when ``mailto`` is provided (or resolved from
    ``CRAWLER_OPENALEX_MAILTO``), requests include ``mailto=<addr>`` as a query
    parameter and an enriched ``User-Agent`` header. Without a mailto the
    public pool is used and a one-time warning is logged.
    """

    def __init__(
        self,
        mailto: Optional[str] = None,
        *,
        base_url: str = "https://api.openalex.org",
    ) -> None:
        global _no_mailto_warned

        # Resolve mailto: explicit arg → env var → None (public pool).
        if mailto is None:
            mailto = config.resolve_openalex_mailto()

        self._mailto: Optional[str] = mailto

        if self._mailto:
            user_agent = (
                f"OpenPulseCrawler (+https://openpulse.science; "
                f"mailto:{self._mailto})"
            )
        else:
            user_agent = _BASE_UA_NO_MAILTO
            if not _no_mailto_warned:
                _no_mailto_warned = True
                logger.warning(
                    "OpenAlexHTTPClient: no mailto address configured. "
                    "Set CRAWLER_OPENALEX_MAILTO for the OpenAlex polite pool "
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

    # ---- resource management ------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP session (releases connections / fds)."""
        self._session.close()

    def __enter__(self) -> "OpenAlexHTTPClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- low-level GET with 429 retry ---------------------------------------

    def _do_get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        _retried: bool = False,
    ) -> httpx.Response:
        """GET ``path`` with one automatic retry on HTTP 429.

        Always injects the ``mailto`` query param when configured. Honors the
        ``Retry-After`` header (capped at :data:`MAX_RETRY_AFTER_SECONDS`). A
        second 429 is returned as-is so the caller can ``raise_for_status``.
        """
        merged: Dict[str, Any] = dict(params) if params else {}
        if self._mailto:
            merged.setdefault("mailto", self._mailto)

        resp = self._session.get(path, params=merged)

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

    def _get_entity(self, path: str) -> Optional[Dict[str, Any]]:
        """GET a single-entity ``path``; return the entity dict or ``None`` on 404."""
        resp = self._do_get(path)
        if resp.status_code == 404:
            return None
        if not resp.is_success:
            resp.raise_for_status()
        return resp.json()

    # ---- single-entity getters ----------------------------------------------

    def get_work(self, id: str) -> Optional[Dict[str, Any]]:
        """``GET /works/{id}`` → entity dict, or ``None`` on 404.

        ``id`` accepts a bare ``W…`` id, a ``doi:10…`` form, or a full DOI URL.
        """
        return self._get_entity(f"/works/{quote(id, safe=':/')}")

    def get_author(self, id: str) -> Optional[Dict[str, Any]]:
        """``GET /authors/{id}`` (``A…``, ``orcid:…``, or ORCID URL)."""
        return self._get_entity(f"/authors/{quote(id, safe=':/')}")

    def get_institution(self, id: str) -> Optional[Dict[str, Any]]:
        """``GET /institutions/{id}`` (``I…``, ``ror:…``, or ROR URL)."""
        return self._get_entity(f"/institutions/{quote(id, safe=':/')}")

    def get_source(self, id: str) -> Optional[Dict[str, Any]]:
        """``GET /sources/{id}`` (``S…``)."""
        return self._get_entity(f"/sources/{quote(id, safe=':/')}")

    def get_funder(self, id: str) -> Optional[Dict[str, Any]]:
        """``GET /funders/{id}`` (``F…``)."""
        return self._get_entity(f"/funders/{quote(id, safe=':/')}")

    # ---- cursor-paginated list helpers --------------------------------------

    def _iter_works(
        self,
        filter_value: str,
        cap: Optional[int],
        *,
        select: str = "id,doi",
        sort: Optional[str] = None,
    ) -> Iterable[Dict[str, Any]]:
        """Yield work result dicts across cursor pages of ``GET /works``.

        Starts at ``cursor=*`` and follows ``meta.next_cursor`` until it is
        null/empty. Stops once ``cap`` items have been yielded (``None`` = no
        cap).
        """
        if cap is not None and cap <= 0:
            return
        yielded = 0
        cursor: Optional[str] = "*"
        while cursor:
            params: Dict[str, Any] = {
                "filter": filter_value,
                "per-page": LIST_PER_PAGE,
                "select": select,
                "cursor": cursor,
            }
            if sort is not None:
                params["sort"] = sort
            resp = self._do_get("/works", params)
            if not resp.is_success:
                resp.raise_for_status()
            body = resp.json()
            for item in body.get("results") or []:
                yield item
                yielded += 1
                if cap is not None and yielded >= cap:
                    return
            cursor = (body.get("meta") or {}).get("next_cursor")

    def iter_citing_works(
        self, work_openalex_id: str, cap: Optional[int],
    ) -> Iterable[Dict[str, Any]]:
        """Yield works that cite ``work_openalex_id`` (newest first).

        ``work_openalex_id`` may be a bare ``W…`` id or a full
        ``https://openalex.org/W…`` URL; the bare id is used in the filter.
        """
        wid = _bare_work_id(work_openalex_id)
        return self._iter_works(
            f"cites:{wid}", cap, sort="publication_date:desc",
        )

    def iter_works_by_entity(
        self, filter_key: str, entity_id: str, cap: Optional[int],
    ) -> Iterable[Dict[str, Any]]:
        """Yield works matching ``{filter_key}:{entity_id}``.

        ``filter_key`` is typically ``author.id`` or ``institutions.id``.
        """
        eid = _bare_work_id(entity_id)
        return self._iter_works(f"{filter_key}:{eid}", cap)

    # ---- batch id → canonical url resolver ----------------------------------

    def resolve_ids_to_canonical(
        self, openalex_work_urls: List[str],
    ) -> Dict[str, str]:
        """Map each OpenAlex work URL to a canonical edge URL.

        For each work returned by the API: if it has a ``doi``, the canonical
        url is ``https://doi.org/{bare-doi-lowercased}``; otherwise it is the
        work's own ``https://openalex.org/W…`` url. Any input URL not returned
        by the API maps to its own ``https://openalex.org/W…`` url (unresolved
        fallback). The returned dict is keyed by the ORIGINAL input urls.

        Ids are batched in groups of :data:`BATCH_SIZE` (≤50) via
        ``GET /works?filter=ids.openalex:W1|W2|…``.
        """
        # Start every input mapped to its own url (unresolved fallback); the
        # API loop overrides entries it can resolve. Keyed by original input.
        result: Dict[str, str] = {url: url for url in openalex_work_urls}
        if not openalex_work_urls:
            return result

        # Map bare W-id → original input url so we can re-key API results.
        bare_to_input: Dict[str, str] = {}
        for url in openalex_work_urls:
            bare_to_input[_bare_work_id(url)] = url

        bare_ids = list(bare_to_input.keys())
        for start in range(0, len(bare_ids), BATCH_SIZE):
            batch = bare_ids[start:start + BATCH_SIZE]
            params = {
                "filter": "ids.openalex:" + "|".join(batch),
                "per-page": BATCH_SIZE,
                "select": "id,doi",
            }
            resp = self._do_get("/works", params)
            if not resp.is_success:
                resp.raise_for_status()
            body = resp.json()
            for work in body.get("results") or []:
                work_url = work.get("id")
                if not work_url:
                    continue
                input_url = bare_to_input.get(_bare_work_id(work_url))
                if input_url is None:
                    continue
                doi = work.get("doi")
                if doi:
                    bare_doi = doi
                    if bare_doi.startswith(_DOI_URL_PREFIX):
                        bare_doi = bare_doi[len(_DOI_URL_PREFIX):]
                    result[input_url] = f"{_DOI_URL_PREFIX}{bare_doi.lower()}"
                else:
                    result[input_url] = work_url
        return result
