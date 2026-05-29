"""Thin httpx wrapper for huggingface.co's REST API.

Mirrors the DataCite client's shape. Pagination follows the ``Link`` header
with ``cursor=<opaque>`` query parameter (same shape as GitLab keyset). 429
responses honor the ``Retry-After`` header with one automatic retry, then
raise.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

# HuggingFace's default page size is 100 (max for list endpoints).
DEFAULT_PAGE_SIZE = 100

# Cap Retry-After honored — avoid hanging on a rogue server response.
MAX_RETRY_AFTER_SECONDS = 60

# Regex for extracting the next-page URL from a Link header value:
#   <https://huggingface.co/api/models?cursor=...&limit=100>; rel="next"
_LINK_NEXT_RE = re.compile(r'<(?P<url>[^>]+)>;\s*rel="next"')


class HuggingFaceHTTPClient:
    """HTTP client for ``huggingface.co`` API endpoints.

    Authentication: ``Authorization: Bearer hf_...`` when ``tokens`` is
    non-empty. Anonymous (no header) when ``tokens=[]`` — HuggingFace's
    public reads return 200 for all entity endpoints we crawl.

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
            headers={"Accept": "application/json"},
        )
        if not self.tokens:
            logger.warning(
                "HuggingFaceHTTPClient(%s) constructed with no tokens — "
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

    def _request_json(
        self, path: str, params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """GET ``path`` and return parsed JSON body.

        404 → ``None``. Any other non-2xx raises ``httpx.HTTPStatusError``.
        """
        resp = self._do_get(path, params)
        if resp.status_code == 404:
            return None
        if not resp.is_success:
            resp.raise_for_status()
        return resp.json()

    def get_model(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """Return the model JSON for the given ``<owner>/<name>``, or None on 404."""
        return self._request_json(f"/api/models/{repo_id}")

    def get_dataset(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """Return the dataset JSON for the given ``<owner>/<name>``, or None on 404."""
        return self._request_json(f"/api/datasets/{repo_id}")

    def get_space(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """Return the space JSON for the given ``<owner>/<name>``, or None on 404."""
        return self._request_json(f"/api/spaces/{repo_id}")

    def get_paper(self, arxiv_id: str) -> Optional[Dict[str, Any]]:
        """Return the paper JSON for the given arxiv ID, or None on 404.

        HuggingFace's /api/papers/<arxiv-id> sometimes returns a list of
        submissions (one per HF re-post); take the first dict.
        """
        data = self._request_json(f"/api/papers/{arxiv_id}")
        if isinstance(data, list):
            return data[0] if data else None
        return data

    def get_collection(self, slug: str) -> Optional[Dict[str, Any]]:
        """Return the collection JSON for ``<owner>/<slug-with-id>``, or None on 404."""
        return self._request_json(f"/api/collections/{slug}")

    def get_user_overview(self, username: str) -> Optional[Dict[str, Any]]:
        """Return the user overview JSON for ``<username>``, or None on 404."""
        return self._request_json(f"/api/users/{username}/overview")

    def get_org_overview(self, org_name: str) -> Optional[Dict[str, Any]]:
        """Return the org overview JSON for ``<org_name>``, or None on 404."""
        return self._request_json(f"/api/organizations/{org_name}/overview")
