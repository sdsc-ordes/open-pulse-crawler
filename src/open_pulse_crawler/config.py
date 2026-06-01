"""Host-keyed environment variable resolution for the multi-platform crawler.

The crawler talks to several Git forge hosts (GitHub, GitLab instances,
RenkuLab). Tokens, enablement, and other per-host knobs are read from
environment variables that embed a normalised host identifier.

Public surface:

- :func:`host_env_ident` — normalise a host name to an env-var identifier
  (``gitlab.epfl.ch`` → ``GITLAB_EPFL_CH``).
- :func:`enabled_instances` — read ``CRAWLER_PLATFORMS`` (comma-separated).
  Defaults to ``["github.com"]`` when unset or blank.
- :func:`resolve_tokens` — per-host token resolution with legacy GitHub
  fallback. Emits a one-shot :class:`DeprecationWarning` (via
  :func:`warnings.warn`) the first time a legacy variable is read in the
  process.
- :func:`resolve_crossref_mailto` — read ``CRAWLER_CROSSREF_MAILTO`` polite-pool
  email. Returns ``None`` when unset or blank.

The legacy fallback intentionally preserves v2's multi-token semantics:
``CRAWLER_GITHUB_TOKEN_POOL``, ``CRAWLER_GITHUB_TOKEN``, and ``GITHUB_TOKEN``
are *all* treated as comma-separated lists so operators currently running
``GITHUB_TOKEN=a,b`` keep getting ``["a", "b"]`` after the upgrade.
"""

from __future__ import annotations

import os
import warnings
from typing import List, Optional

# Canonical, host-keyed env-var prefixes.
TOKEN_POOL_PREFIX = "CRAWLER_TOKEN_POOL__"
TOKEN_PREFIX = "CRAWLER_TOKEN__"

# Legacy GitHub-only env vars. All three are treated as comma-separated lists
# to preserve v2 multi-token rotation semantics.
LEGACY_GITHUB_POOL_ENV = "CRAWLER_GITHUB_TOKEN_POOL"
LEGACY_GITHUB_TOKEN_ENV = "CRAWLER_GITHUB_TOKEN"
LEGACY_GITHUB_BARE_ENV = "GITHUB_TOKEN"

PLATFORMS_ENV = "CRAWLER_PLATFORMS"
CROSSREF_MAILTO_ENV = "CRAWLER_CROSSREF_MAILTO"

_DEFAULT_PLATFORMS = ("github.com",)

# One-shot deprecation flag. Survives across calls within a single process;
# tests reload this module to re-arm it.
_warned_legacy_github = False


def _split(value: str) -> List[str]:
    """Split a comma-separated env-var value, stripping blanks."""
    return [t.strip() for t in value.split(",") if t.strip()]


def host_env_ident(host: str) -> str:
    """Normalise a host name to an env-var identifier.

    Lowercases, replaces ``.`` and ``-`` with ``_``, then uppercases. E.g.
    ``gitlab.epfl.ch`` → ``GITLAB_EPFL_CH``.
    """
    return host.lower().replace(".", "_").replace("-", "_").upper()


def enabled_instances() -> List[str]:
    """Return the list of enabled platform hosts.

    Reads :data:`PLATFORMS_ENV` (``CRAWLER_PLATFORMS``) as a comma-separated
    list. Returns ``["github.com"]`` when unset or blank.
    """
    raw = os.environ.get(PLATFORMS_ENV, "")
    if not raw.strip():
        return list(_DEFAULT_PLATFORMS)
    return _split(raw)


def resolve_crossref_mailto() -> Optional[str]:
    """Return the Crossref polite-pool contact email, or ``None`` if unset/blank.

    Reads :data:`CROSSREF_MAILTO_ENV` (``CRAWLER_CROSSREF_MAILTO``). The value
    is stripped of leading/trailing whitespace; a blank or whitespace-only
    string is treated as unset and returns ``None``.
    """
    raw = os.environ.get(CROSSREF_MAILTO_ENV, "")
    stripped = raw.strip()
    return stripped if stripped else None


def _warn_legacy_github_once() -> None:
    """Emit a single ``DeprecationWarning`` per process for legacy GitHub vars."""
    global _warned_legacy_github
    if _warned_legacy_github:
        return
    _warned_legacy_github = True
    warnings.warn(
        f"{LEGACY_GITHUB_POOL_ENV}, {LEGACY_GITHUB_TOKEN_ENV}, and "
        f"{LEGACY_GITHUB_BARE_ENV} are deprecated; use "
        f"{TOKEN_PREFIX}GITHUB_COM (single token) or "
        f"{TOKEN_POOL_PREFIX}GITHUB_COM (comma-separated list for rotation) "
        "instead.",
        DeprecationWarning,
        stacklevel=2,
    )


def resolve_tokens(host: str) -> List[str]:
    """Resolve tokens for ``host`` from the environment.

    Precedence (first non-blank wins):

    1. ``CRAWLER_TOKEN_POOL__<IDENT>`` — comma-separated pool.
    2. ``CRAWLER_TOKEN__<IDENT>`` — single token (returned as a one-element list).
    3. *GitHub only:* ``CRAWLER_GITHUB_TOKEN_POOL`` → ``CRAWLER_GITHUB_TOKEN``
       → ``GITHUB_TOKEN``. All three are parsed as comma-separated lists.
       Reading any of them emits a one-shot :class:`DeprecationWarning`.

    Returns an empty list when nothing is configured.
    """
    ident = host_env_ident(host)

    pool = os.environ.get(f"{TOKEN_POOL_PREFIX}{ident}", "")
    if pool.strip():
        return _split(pool)

    single = os.environ.get(f"{TOKEN_PREFIX}{ident}", "")
    if single.strip():
        return _split(single)

    if host == "github.com":
        for legacy in (
            LEGACY_GITHUB_POOL_ENV,
            LEGACY_GITHUB_TOKEN_ENV,
            LEGACY_GITHUB_BARE_ENV,
        ):
            value = os.environ.get(legacy, "")
            if value.strip():
                _warn_legacy_github_once()
                return _split(value)

    return []
