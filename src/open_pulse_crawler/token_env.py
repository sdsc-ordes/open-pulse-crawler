"""Resolve GitHub PAT(s) from environment variables.

This module is a thin compatibility wrapper around
:func:`open_pulse_crawler.config.resolve_tokens` for the ``github.com`` host.

Priority (highest to lowest), via :mod:`open_pulse_crawler.config`:

  1. ``CRAWLER_TOKEN_POOL__GITHUB_COM`` — canonical, comma-separated list.
  2. ``CRAWLER_TOKEN__GITHUB_COM`` — canonical, single token.
  3. Legacy GitHub-only vars (all comma-separated for v2 compatibility):
     ``CRAWLER_GITHUB_TOKEN_POOL`` → ``CRAWLER_GITHUB_TOKEN`` → ``GITHUB_TOKEN``.
     Reading any of these emits both a :class:`DeprecationWarning` (from
     :mod:`config`) and a one-shot ``logger.warning(...)`` (from this module),
     each fired at most once per process.

Callers receive a list of tokens (possibly empty) and decide how to fail.
"""

from __future__ import annotations

import logging
import os
from typing import List

from open_pulse_crawler import config

logger = logging.getLogger(__name__)

# Legacy env-var names re-exported for backwards compatibility. ``POOL_ENV``
# and ``TOKEN_ENV`` were the v2 "new" names; in v3 they are legacy aliases
# kept around for operators still using them.
POOL_ENV = config.LEGACY_GITHUB_POOL_ENV
TOKEN_ENV = config.LEGACY_GITHUB_TOKEN_ENV
LEGACY_ENV = config.LEGACY_GITHUB_BARE_ENV

# Canonical v3 env-var names for GitHub.
NEW_POOL_ENV = f"{config.TOKEN_POOL_PREFIX}GITHUB_COM"
NEW_TOKEN_ENV = f"{config.TOKEN_PREFIX}GITHUB_COM"

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


def _new_var_set() -> bool:
    """Return True if either canonical v3 GitHub env var has a non-blank value."""
    for var in (NEW_POOL_ENV, NEW_TOKEN_ENV):
        if os.environ.get(var, "").strip():
            return True
    return False


def _any_legacy_var_set() -> bool:
    """Return True if any legacy GitHub env var has a non-blank value."""
    for var in (POOL_ENV, TOKEN_ENV, LEGACY_ENV):
        if os.environ.get(var, "").strip():
            return True
    return False


def resolve_github_tokens() -> List[str]:
    """Return tokens for GitHub from the environment.

    Thin wrapper around :func:`open_pulse_crawler.config.resolve_tokens` that
    additionally emits a one-shot ``logger.warning`` when the result will come
    from a legacy fallback (preserves v2's ``caplog``-based test contract).
    Returns an empty list when no source variable is set.
    """
    if not _new_var_set() and _any_legacy_var_set():
        _warn_legacy_once()
    return config.resolve_tokens("github.com")


def tokens_not_set_message() -> str:
    """Human-readable error when no token env var is set."""
    return (
        f"No GitHub tokens found in environment. Set {TOKEN_ENV} to a single "
        f"token, or {POOL_ENV} to a comma-separated list for rotation."
    )
