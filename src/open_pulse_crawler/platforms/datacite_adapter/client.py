"""Thin httpx wrapper for DataCite's REST API.

Mirrors the Infoscience client's shape but speaks JSON:API (vnd.api+json)
content type. Pagination follows ``links.next``. 429 responses honor the
``Retry-After`` header with one automatic retry, then raise.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

# DataCite's default page size is 25; max is 1000. We default to 100
# (matches Infoscience for consistency; raises by 4× without hitting the cap).
DEFAULT_PAGE_SIZE = 100

# Cap Retry-After honored — avoid hanging on a rogue server response.
MAX_RETRY_AFTER_SECONDS = 60


class DataCiteHTTPClient:
    """HTTP client for ``api.datacite.org`` endpoints.

    Authentication: ``Authorization: Bearer <token>`` when ``tokens`` is
    non-empty. Anonymous (no header) when ``tokens=[]`` — DataCite's
    public reads return 200 for ``/dois``, ``/clients``, search endpoints.

    Rate-limit handling: on HTTP 429, the client reads the ``Retry-After``
    header (or defaults to 5 seconds), sleeps, and retries once. A second
    429 raises ``httpx.HTTPStatusError``.
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
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            headers={"Accept": "application/vnd.api+json"},
        )
        if not self.tokens:
            logger.warning(
                "DataCiteHTTPClient(%s) constructed with no tokens — "
                "anonymous reads only; rate limits will be tighter.",
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

    # ---- token rotation ---------------------------------------------------

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
        resp = (self._session.get(path, params=params)
                if params is not None else self._session.get(path))
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

    def _request_data(
        self, path: str, params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """GET ``path`` and return the JSON:API ``data`` payload.

        404 → ``None``. Any other non-2xx raises ``httpx.HTTPStatusError``.
        """
        resp = self._do_get(path, params)
        if resp.status_code == 404:
            return None
        if not resp.is_success:
            resp.raise_for_status()
        body = resp.json()
        return body.get("data")

    def get_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """Return the JSON:API ``data`` for the given DOI, or ``None`` on 404."""
        return self._request_data(f"/dois/{doi}")

    def get_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Return the JSON:API ``data`` for the given DataCite client id,
        or ``None`` on 404.
        """
        return self._request_data(f"/clients/{client_id}")

    def get_client_prefixes(self, client_id: str) -> List[str]:
        """Return the DOI prefixes owned by the given client (empty on 404)."""
        data = self._request_data(f"/clients/{client_id}/relationships/prefixes")
        if not data:
            return []
        return [p.get("id", "") for p in data if isinstance(p, dict) and p.get("id")]

    # ---- cursor-paginated search ------------------------------------------

    def _iter_dois_query(
        self, query: str,
    ) -> Iterable[Dict[str, Any]]:
        """Yield DOI records across all pages of a /dois search.

        First page uses ``page[cursor]=1``; subsequent pages follow
        ``links.next`` (absolute URL, params baked in).
        """
        path: str = "/dois"
        current_params: Optional[Dict[str, Any]] = {
            "query": query,
            "page[size]": DEFAULT_PAGE_SIZE,
            "page[cursor]": 1,
        }
        while True:
            resp = self._do_get(path, current_params)
            if not resp.is_success:
                resp.raise_for_status()
            body = resp.json()
            for item in body.get("data") or []:
                yield item
            next_link = (body.get("links") or {}).get("next")
            if not next_link:
                return
            path = next_link
            current_params = None  # next-link is absolute, params already in URL

    def iter_dois_by_ror(self, ror_url: str) -> Iterable[Dict[str, Any]]:
        """Yield DOI records affiliated with the given ROR organization."""
        return self._iter_dois_query(
            f'creators.affiliation.affiliationIdentifier:"{ror_url}"',
        )

    def iter_dois_by_orcid(self, orcid_url: str) -> Iterable[Dict[str, Any]]:
        """Yield DOI records authored by the given ORCID identifier."""
        return self._iter_dois_query(
            f'creators.nameIdentifiers.nameIdentifier:"{orcid_url}"',
        )
