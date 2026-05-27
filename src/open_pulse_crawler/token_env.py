"""Resolve GitHub PAT(s) from environment variables.

Priority (highest to lowest):
  1. ``CRAWLER_GITHUB_TOKEN_POOL`` — comma-separated list of tokens for rotation.
  2. ``CRAWLER_GITHUB_TOKEN`` — single token (also tolerates a comma list).
  3. ``GITHUB_TOKEN`` — legacy, comma-separated list. Emits a deprecation
     warning the first time it is read in the current process.

Callers receive a list of tokens (possibly empty) and decide how to fail.
"""

from __future__ import annotations

import logging
import os
from typing import List

logger = logging.getLogger(__name__)

POOL_ENV = "CRAWLER_GITHUB_TOKEN_POOL"
TOKEN_ENV = "CRAWLER_GITHUB_TOKEN"
LEGACY_ENV = "GITHUB_TOKEN"

_warned_legacy = False


def _split(value: str) -> List[str]:
    return [t.strip() for t in value.split(",") if t.strip()]


def _warn_legacy_once() -> None:
    global _warned_legacy
    if _warned_legacy:
        return
    _warned_legacy = True
    logger.warning(
        "%s is deprecated; use %s (single token) or %s (comma-separated list "
        "for rotation) instead.",
        LEGACY_ENV,
        TOKEN_ENV,
        POOL_ENV,
    )


def reset_deprecation_warning() -> None:
    """Re-arm the once-per-process deprecation warning. For tests."""
    global _warned_legacy
    _warned_legacy = False


def resolve_github_tokens() -> List[str]:
    """Return tokens from the environment following the priority chain.

    Returns an empty list when no source variable is set.
    """
    pool = os.environ.get(POOL_ENV, "")
    if pool.strip():
        return _split(pool)

    single = os.environ.get(TOKEN_ENV, "")
    if single.strip():
        return _split(single)

    legacy = os.environ.get(LEGACY_ENV, "")
    if legacy.strip():
        _warn_legacy_once()
        return _split(legacy)

    return []


def tokens_not_set_message() -> str:
    """Human-readable error when no token env var is set."""
    return (
        f"No GitHub tokens found in environment. Set {TOKEN_ENV} to a single "
        f"token, or {POOL_ENV} to a comma-separated list for rotation."
    )
