"""Thin httpx wrapper for Zenodo's InvenioRDM REST API.

Mirrors the GitLab client's shape:
  1. Construction with optional token rotation pool (anonymous when empty).
  2. Single-entity fetches map 404 -> None.  (Task 4)
  3. Iterators degrade to ``[]`` on 401/403 so a missing scope doesn't
     tank the whole crawl.  (Task 5)
  4. Per-host disk cache is pre-allocated when ``_cache_dir`` is set; the
     single-entity lookups still hit the API for now (same TODO as the
     GitLab client -- wiring is straightforward for Zenodo because the API
     returns plain JSON).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)


class ZenodoClient:
    """HTTP client for Zenodo's records / communities / users endpoints.

    Authentication: ``Authorization: Bearer <token>`` when ``tokens`` is
    non-empty. Anonymous (no header) when ``tokens=[]`` -- Zenodo's public
    records and communities are readable without auth.
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
                "ZenodoClient(%s) constructed with no tokens -- "
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

    # ---- single-entity fetches ---------------------------------------------

    def _request_json(self, path: str, *, degrade_on_auth: bool = False) -> Optional[Dict[str, Any]]:
        """GET ``path`` and return parsed JSON.

        404 -> ``None``. 401/403 -> ``None`` when ``degrade_on_auth=True``,
        else raise. Any other non-2xx raises ``httpx.HTTPStatusError``.
        """
        resp = self._session.get(path)
        if resp.status_code == 404:
            return None
        if degrade_on_auth and resp.status_code in (401, 403):
            logger.warning(
                "%s on %s returned %s; degrading to None (insufficient scope or "
                "anonymous access not permitted).",
                path, self.host, resp.status_code,
            )
            return None
        if not resp.is_success:
            resp.raise_for_status()
        return resp.json()

    def get_record(self, record_id) -> Optional[Dict[str, Any]]:
        """Return the record's JSON payload, or ``None`` on 404."""
        return self._request_json(f"/api/records/{record_id}")

    def get_community(self, slug: str) -> Optional[Dict[str, Any]]:
        """Return the community's JSON payload, or ``None`` on 404."""
        return self._request_json(f"/api/communities/{slug}")

    def get_user(self, user_id) -> Optional[Dict[str, Any]]:
        """Return the user's JSON payload.

        Returns ``None`` on 404, 401, or 403 -- ``/api/users/<id>`` is often
        auth-required on production Zenodo.
        """
        return self._request_json(f"/api/users/{user_id}", degrade_on_auth=True)

    # ---- list / iterate ----------------------------------------------------

    def _iter_paginated(
        self,
        path: str,
        params: Dict[str, Any],
        *,
        degrade_on_auth: bool = False,
    ) -> Iterable[Dict[str, Any]]:
        """Yield ``hits.hits`` entries across all pages, following ``links.next``.

        Zenodo's InvenioRDM API uses keyset pagination: each response carries
        ``links.next`` as an absolute URL when more pages exist. We follow the
        URL verbatim rather than incrementing a ``page=`` param.

        On 401/403 with ``degrade_on_auth=True`` we stop and emit nothing.
        """
        # First page: pass params dict
        resp = self._session.get(path, params=params)
        while True:
            if degrade_on_auth and resp.status_code in (401, 403):
                logger.warning(
                    "%s on %s returned %s; emitting no entries (insufficient "
                    "scope or anonymous access not permitted).",
                    path, self.host, resp.status_code,
                )
                return
            if not resp.is_success:
                resp.raise_for_status()
            body = resp.json()
            hits = body.get("hits", {}).get("hits", [])
            for hit in hits:
                yield hit
            next_url = body.get("links", {}).get("next")
            if not next_url:
                return
            # Follow the absolute next URL -- params are baked in.
            resp = self._session.get(next_url)

    def iter_community_records(self, slug: str) -> Iterable[Dict[str, Any]]:
        """All records belonging to community ``slug``, paginated."""
        return self._iter_paginated(
            "/api/records",
            {"communities": slug, "size": 100},
        )

    def iter_user_records(self, user_id) -> Iterable[Dict[str, Any]]:
        """All records uploaded by ``user_id``.

        Often auth-required; degrades to ``[]`` on 401/403.
        """
        return self._iter_paginated(
            "/api/records",
            {"q": f"owners.user:{user_id}", "size": 100},
            degrade_on_auth=True,
        )
