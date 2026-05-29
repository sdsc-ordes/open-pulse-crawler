"""Thin httpx wrapper for Infoscience's DSpace 7 REST API.

Mirrors the Zenodo client's shape but speaks HAL+JSON instead of plain
JSON. Pagination follows ``_links.next``. 429 responses honor the
``Retry-After`` header with one automatic retry, then raise.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Anonymous DSpace page-size cap is 100 (no 25 limit like Zenodo).
DEFAULT_PAGE_SIZE = 100

# Cap the Retry-After value the client honors, to avoid hanging on a
# rogue server response. 60 seconds is enough for any reasonable backoff.
MAX_RETRY_AFTER_SECONDS = 60


class InfoscienceClient:
    """HTTP client for Infoscience's DSpace 7 REST endpoints.

    Authentication: ``Authorization: Bearer <token>`` when ``tokens`` is
    non-empty. Anonymous (no header) when ``tokens=[]`` — Infoscience's
    public items, persons, and orgunits are readable without auth.

    Rate-limit handling: on HTTP 429, the client reads the ``Retry-After``
    header (or defaults to 5 seconds), sleeps, and retries the request
    once. A second 429 raises ``httpx.HTTPStatusError`` so callers can
    decide whether to keep trying.
    """

    def __init__(
        self,
        host: str,
        tokens: List[str],
        _cache_dir: Optional[Path] = None,
    ) -> None:
        self.host = host
        self.base_url = f"https://{host}"
        self.tokens = list(tokens)
        self._idx = 0
        self._session = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(15.0, connect=8.0),
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        if not self.tokens:
            logger.warning(
                "InfoscienceClient(%s) constructed with no tokens — "
                "anonymous reads only; rate limits will be tight.",
                host,
            )
        else:
            self._apply_current_token()

        self._cache: Optional[Any] = None
        if _cache_dir is not None:
            from ..github.client import APICache, resolve_cache_ttl
            self._cache = APICache(
                _cache_dir,
                ttl_seconds=resolve_cache_ttl(),
                host=self.host,
            )

    # ---- token rotation -----------------------------------------------------

    def _apply_current_token(self) -> None:
        self._session.headers["Authorization"] = f"Bearer {self.tokens[self._idx]}"

    def _rotate(self) -> None:
        """Cycle to the next token. No-op in anonymous mode."""
        if not self.tokens:
            return
        self._idx = (self._idx + 1) % len(self.tokens)
        self._apply_current_token()

    # ---- single-entity fetches with 429 retry ------------------------------

    def _do_get(self, path: str, params: Optional[Dict[str, Any]] = None,
                _retried: bool = False) -> httpx.Response:
        """GET ``path`` with one automatic retry on HTTP 429.

        Honors the ``Retry-After`` header (capped at ``MAX_RETRY_AFTER_SECONDS``).
        Second 429 raises via the caller's ``raise_for_status``.
        """
        resp = self._session.get(path, params=params) if params is not None \
            else self._session.get(path)
        if resp.status_code == 429 and not _retried:
            retry_after_raw = resp.headers.get("Retry-After", "5")
            try:
                retry_after = float(retry_after_raw)
            except (TypeError, ValueError):
                retry_after = 5.0
            retry_after = min(max(retry_after, 0.0), MAX_RETRY_AFTER_SECONDS)
            logger.warning(
                "%s on %s returned 429; sleeping %.1fs then retrying once.",
                path, self.host, retry_after,
            )
            time.sleep(retry_after)
            return self._do_get(path, params=params, _retried=True)
        return resp

    def _request_json(self, path: str, params: Optional[Dict[str, Any]] = None,
                      *, degrade_on_auth: bool = False) -> Optional[Dict[str, Any]]:
        """GET ``path`` and return parsed JSON.

        404 → ``None``. 401/403 → ``None`` when ``degrade_on_auth=True``,
        else raise. Any other non-2xx raises ``httpx.HTTPStatusError``.
        """
        resp = self._do_get(path, params)
        if resp.status_code == 404:
            return None
        if degrade_on_auth and resp.status_code in (401, 403):
            logger.warning(
                "%s on %s returned %s; degrading to None.",
                path, self.host, resp.status_code,
            )
            return None
        if not resp.is_success:
            resp.raise_for_status()
        return resp.json()

    def get_item_by_handle(self, handle: str) -> Optional[Dict[str, Any]]:
        """Return the item JSON for the given handle, or ``None`` on 404.

        Uses the DSpace 7 ``/server/api/pid/find`` endpoint with the
        ``hdl:`` prefix scheme, which is the standard resolver for Handle
        System persistent identifiers in DSpace 7.x.  The legacy
        ``/server/api/handle/{handle}`` path is not exposed by Infoscience.
        """
        return self._request_json(
            "/server/api/pid/find",
            params={"id": f"hdl:{handle}"},
        )

    def get_item_by_uuid(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Return the item JSON for the given UUID, or ``None`` on 404."""
        return self._request_json(f"/server/api/core/items/{uuid}")

    # ---- HAL+JSON paginated iteration --------------------------------------

    def _iter_paginated(
        self,
        path: str,
        params: Dict[str, Any],
    ) -> Iterable[Dict[str, Any]]:
        """Yield indexable objects across all pages of a DSpace search.

        DSpace 7 wraps search results in:
            _embedded.searchResult._embedded.objects[]._embedded.indexableObject
        and exposes the next-page URL at
            _embedded.searchResult._links.next.href
        """
        url = path
        current_params: Optional[Dict[str, Any]] = params
        while True:
            resp = self._do_get(url, current_params)
            if not resp.is_success:
                resp.raise_for_status()
            body = resp.json()
            search = body.get("_embedded", {}).get("searchResult", {})
            objects = search.get("_embedded", {}).get("objects", [])
            for obj in objects:
                ix = obj.get("_embedded", {}).get("indexableObject")
                if ix is not None:
                    yield ix
            next_link = search.get("_links", {}).get("next", {})
            next_href = next_link.get("href") if isinstance(next_link, dict) else None
            if not next_href:
                return
            url = next_href
            current_params = None  # next-link has params baked in

    def iter_person_items(self, person_uuid: str) -> Iterable[Dict[str, Any]]:
        """Items where the given Person UUID is an author authority."""
        return self._iter_paginated(
            "/server/api/discover/search/objects",
            {"dsoType": "item", "query": f"author.authority:{person_uuid}", "size": DEFAULT_PAGE_SIZE},
        )

    def iter_orgunit_items(self, orgunit_uuid: str) -> Iterable[Dict[str, Any]]:
        """Items whose authors are affiliated with the given OrgUnit UUID."""
        return self._iter_paginated(
            "/server/api/discover/search/objects",
            {"dsoType": "item",
             "query": f"author.parent-organization.authority:{orgunit_uuid}",
             "size": DEFAULT_PAGE_SIZE},
        )

    def iter_child_orgunits(self, orgunit_uuid: str) -> Iterable[Dict[str, Any]]:
        """OrgUnit entities whose parentOrganization authority is the given UUID."""
        return self._iter_paginated(
            "/server/api/discover/search/objects",
            {"dsoType": "item",
             "query": f"dspace.entity.type:OrgUnit AND organization.parentOrganization.authority:{orgunit_uuid}",
             "size": DEFAULT_PAGE_SIZE},
        )

