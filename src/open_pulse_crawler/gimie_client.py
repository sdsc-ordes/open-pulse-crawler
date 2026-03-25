"""HTTP + disk-caching client for the gimie JSON-LD endpoint."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


def _split_repo_full_name(repo_full_name: str) -> Tuple[Optional[str], Optional[str]]:
    if "/" not in repo_full_name:
        return None, repo_full_name
    owner, repo = repo_full_name.split("/", 1)
    return owner, repo


def _safe_fs_component(s: str) -> str:
    # Allowed: [a-zA-Z0-9._-] ; everything else becomes '_'.
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s).strip("_") or "unknown"


def _jsonld_payload_filename(repo_full_name: str) -> str:
    owner, repo = _split_repo_full_name(repo_full_name)
    owner_s = _safe_fs_component(owner or "unknown_owner")
    repo_s = _safe_fs_component(repo or "unknown_repo")
    return f"{owner_s}__{repo_s}.json"


def _jsonld_error_filename(repo_full_name: str, suffix: str) -> str:
    owner, repo = _split_repo_full_name(repo_full_name)
    owner_s = _safe_fs_component(owner or "unknown_owner")
    repo_s = _safe_fs_component(repo or "unknown_repo")
    return f"{owner_s}__{repo_s}.{suffix}.json"


def _repo_full_name_to_github_url(repo_full_name: str) -> Optional[str]:
    owner, repo = _split_repo_full_name(repo_full_name)
    if not owner or not repo:
        return None
    return f"https://github.com/{owner}/{repo}"


def clear_stale_jsonld_error_files(errors_dir: Path, repo_full_name: str) -> None:
    """Remove jsonld_errors artifacts for this repo (e.g. after a successful fetch)."""
    stem = Path(_jsonld_payload_filename(repo_full_name)).stem
    for p in errors_dir.glob(f"{stem}.*.json"):
        try:
            p.unlink()
        except OSError as exc:
            logger.debug("Could not remove stale gimie error file %s: %s", p, exc)


class GimieJsonLdClient:
    def __init__(
        self,
        *,
        api_base: str,
        jsonld_repo_segment: str,
        jsonld_dir: Optional[Path] = None,
        skip_existing_jsonld: bool = False,
        timeout_s: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.jsonld_repo_segment = jsonld_repo_segment
        self.jsonld_dir = jsonld_dir
        self.skip_existing_jsonld = skip_existing_jsonld
        self.timeout_s = timeout_s
        self.max_retries = max_retries

        if self.jsonld_dir is not None:
            self.jsonld_dir.mkdir(parents=True, exist_ok=True)
            # Mirror the standalone script layout:
            #   jsonld/<owner__repo>.json
            #   jsonld_errors/<owner__repo>.http_400.json
            self.errors_dir: Optional[Path] = self.jsonld_dir.parent / "jsonld_errors"
            self.errors_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.errors_dir = None

    def _jsonld_url(self, *, repo_full_name: str) -> str:
        github_url = _repo_full_name_to_github_url(repo_full_name)
        if not github_url:
            raise ValueError(f"repo_full_name must be 'owner/repo' (got {repo_full_name!r})")
        encoded_github_url = quote(github_url, safe="")
        base = (
            f"{self.api_base}/v1/repository/{self.jsonld_repo_segment}/json-ld/{encoded_github_url}"
        )
        # Always ask gimie to bypass its cache when we perform an HTTP fetch (not user-configurable).
        return f"{base}?force_refresh=true"

    def _read_cached_jsonld(self, payload_path: Path) -> Any:
        with payload_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write_cached_jsonld_atomic(self, payload_path: Path, data: Any) -> None:
        # Write atomically to avoid partial files being observed by other threads.
        tmp_path = payload_path.with_suffix(payload_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, payload_path)

    def fetch_repo_jsonld(self, repo_full_name: str) -> Optional[Any]:
        payload_path: Optional[Path] = None
        if self.jsonld_dir is not None:
            payload_path = self.jsonld_dir / _jsonld_payload_filename(repo_full_name)

        if (
            payload_path is not None
            and payload_path.exists()
            and self.skip_existing_jsonld
        ):
            data = self._read_cached_jsonld(payload_path)
            if self.errors_dir is not None:
                clear_stale_jsonld_error_files(self.errors_dir, repo_full_name)
            return data

        url = self._jsonld_url(repo_full_name=repo_full_name)

        last_exc: Optional[BaseException] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_s, headers={"accept": "application/json"}) as client:
                    resp = client.get(url)

                if 200 <= resp.status_code < 300:
                    data = resp.json()
                    if payload_path is not None:
                        self._write_cached_jsonld_atomic(payload_path, data)
                    if self.errors_dir is not None:
                        clear_stale_jsonld_error_files(self.errors_dir, repo_full_name)
                    return data

                # Non-2xx: keep last error and optionally fall back to cache.
                last_exc = RuntimeError(f"gimie JSON-LD request failed: HTTP {resp.status_code}")
                raw_body = resp.text or ""
                log_preview = raw_body[:500].replace("\n", " ").strip()
                logger.warning(
                    "gimie JSON-LD HTTP %s for %s: %s",
                    resp.status_code,
                    repo_full_name,
                    log_preview or "(empty body)",
                )
                if self.errors_dir is not None:
                    body_snippet = raw_body[:4000]
                    err_path = self.errors_dir / _jsonld_error_filename(
                        repo_full_name, f"http_{resp.status_code}"
                    )
                    err_payload = {
                        "repo": repo_full_name,
                        "repo_owner": _split_repo_full_name(repo_full_name)[0],
                        "repo_name": _split_repo_full_name(repo_full_name)[1],
                        "status": "http_error",
                        "http_status": resp.status_code,
                        "request_url": url,
                        "body_snippet": body_snippet,
                        "attempts": attempt,
                    }
                    try:
                        with err_path.open("w", encoding="utf-8") as f:
                            json.dump(err_payload, f, indent=2, ensure_ascii=False)
                    except Exception:
                        logger.exception("Failed to write gimie error file: %s", err_path)

                # For HTTP errors (like 400), retrying usually doesn't help;
                # write error and return None so the crawler can fall back.
                return None
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc

            # Simple exponential backoff.
            time.sleep(min(2 ** (attempt - 1), 10))

        if payload_path is not None and payload_path.exists():
            logger.warning(
                "Falling back to cached gimie JSON-LD for %s after failures: %s",
                repo_full_name,
                last_exc,
            )
            try:
                return self._read_cached_jsonld(payload_path)
            except Exception:
                pass

        if self.errors_dir is not None:
            err_path = self.errors_dir / _jsonld_error_filename(repo_full_name, "failed")
            err_payload = {
                "repo": repo_full_name,
                "repo_owner": _split_repo_full_name(repo_full_name)[0],
                "repo_name": _split_repo_full_name(repo_full_name)[1],
                "status": "failed",
                "error": repr(last_exc) if last_exc else "unknown error",
                "request_url": url,
                "attempts": self.max_retries,
            }
            try:
                with err_path.open("w", encoding="utf-8") as f:
                    json.dump(err_payload, f, indent=2, ensure_ascii=False)
            except Exception:
                logger.exception("Failed to write gimie failure file: %s", err_path)

        logger.error("Failed to fetch gimie JSON-LD for %s: %s", repo_full_name, last_exc)
        return None

