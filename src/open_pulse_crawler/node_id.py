"""Canonical URL-based node identifiers.

Every node in the graph (user, organization, repository, team) is keyed by
the canonical URL of its public page on its source platform — e.g.
``https://github.com/torvalds`` for a user or ``https://github.com/torvalds/linux``
for a repository. Using URLs as IDs gives us:

  * Cross-platform uniqueness — a future GitLab adapter can store
    ``https://gitlab.com/...`` nodes next to GitHub nodes in the same graph
    without colliding on the bare login.
  * A natural fit for JSON-LD / linked-data exporters (gimie); the URL is
    already what those tools use as ``@id``.

This module owns the **canonical form** and the seed-string parser. Other
modules call into here at the boundary (CLI input, API input, crawler
seed handling) so that internally the rest of the code only ever sees
canonicalized URLs.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Tuple
from urllib.parse import urlparse


DEFAULT_HOST = "github.com"


class NodeKind(str, Enum):
    """What kind of node a seed string refers to.

    ``USER_OR_ORG`` is deliberately ambiguous: GitHub URLs of the form
    ``https://github.com/<login>`` can be a user or an organization, and
    only an API call can tell. The crawler resolves the ambiguity at
    fetch time.
    """

    USER_OR_ORG = "user_or_org"
    REPO = "repo"
    TEAM = "team"


def canonical_url(host: str, path: str) -> str:
    """Build a canonical URL from a host + path.

    Normalization rules:
      * Scheme is always ``https``.
      * Host is lowercased (hostnames are case-insensitive per RFC 3986).
      * Path is preserved as-is (GitHub displays casing in logins / repo
        names; we do not fold it). A leading ``/`` is enforced and a
        trailing ``/`` is stripped.
      * No query, no fragment, no userinfo, no port.
    """
    host = host.strip().lower()
    if not host:
        raise ValueError("canonical_url: host is required")
    path = path.strip()
    if not path.startswith("/"):
        path = "/" + path
    # Collapse a trailing slash but preserve "/" itself as a no-op.
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return f"https://{host}{path}"


def host_of(url: str) -> str:
    """Return the lowercase host of a canonical URL.

    Used by the future per-platform dispatch ( ``github.com`` vs
    ``gitlab.com`` ). Raises ``ValueError`` if the URL has no host.
    """
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError(f"host_of: no host in URL {url!r}")
    return parsed.hostname.lower()


def _strip_path(url: str) -> str:
    """Return the path component of a URL without the leading slash."""
    parsed = urlparse(url)
    path = (parsed.path or "").strip("/")
    return path


def extract_login(url: str) -> str:
    """Return the GitHub login (user or org name) embedded in a node URL.

    Works for both user/org nodes (``https://github.com/torvalds`` → ``torvalds``)
    and the owner half of a repo node (``https://github.com/torvalds/linux``
    → ``torvalds``).
    """
    path = _strip_path(url)
    if not path:
        raise ValueError(f"extract_login: URL has no path: {url!r}")
    return path.split("/", 1)[0]


def extract_full_name(url: str) -> str:
    """Return the ``owner/repo`` form for a repository node URL."""
    path = _strip_path(url)
    parts = path.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"extract_full_name: not a repo URL: {url!r}")
    return f"{parts[0]}/{parts[1]}"


def kind_of(url: str) -> NodeKind:
    """Infer the node kind from the shape of a canonical URL's path.

    One path segment → ``USER_OR_ORG``.
    Two path segments → ``REPO``.
    More segments (e.g. teams under an org) are recognised as ``TEAM``
    only via the explicit ``team_url`` constructor; URLs of unknown shape
    raise ``ValueError`` so the caller is forced to decide.
    """
    path = _strip_path(url)
    if not path:
        raise ValueError(f"kind_of: empty path in URL {url!r}")
    parts = path.split("/")
    if len(parts) == 1:
        return NodeKind.USER_OR_ORG
    if len(parts) == 2:
        return NodeKind.REPO
    raise ValueError(f"kind_of: cannot infer kind for URL {url!r}")


def user_url(login: str, host: str = DEFAULT_HOST) -> str:
    """Build the canonical URL for a user or org login."""
    login = login.strip().strip("/")
    if not login or "/" in login:
        raise ValueError(f"user_url: invalid login {login!r}")
    return canonical_url(host, login)


def repo_url(full_name: str, host: str = DEFAULT_HOST) -> str:
    """Build the canonical URL for a repo full name (``owner/repo``)."""
    full_name = full_name.strip().strip("/")
    parts = full_name.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"repo_url: expected owner/repo, got {full_name!r}")
    return canonical_url(host, f"{parts[0]}/{parts[1]}")


def team_url(org_login: str, slug: str, host: str = DEFAULT_HOST) -> str:
    """Build the canonical URL for an organization team.

    GitHub renders teams at ``https://github.com/orgs/<org>/teams/<slug>``;
    we use that same path so the URL resolves in a browser.
    """
    org_login = org_login.strip().strip("/")
    slug = slug.strip().strip("/")
    if not org_login or "/" in org_login:
        raise ValueError(f"team_url: invalid org {org_login!r}")
    if not slug or "/" in slug:
        raise ValueError(f"team_url: invalid slug {slug!r}")
    return canonical_url(host, f"orgs/{org_login}/teams/{slug}")


def is_team_url(url: str) -> bool:
    """Return True for a team URL produced by :func:`team_url`."""
    path = _strip_path(url)
    parts = path.split("/")
    return len(parts) == 4 and parts[0] == "orgs" and parts[2] == "teams"


def extract_team_parts(url: str) -> Tuple[str, str]:
    """Return ``(org_login, slug)`` from a team URL."""
    path = _strip_path(url)
    parts = path.split("/")
    if not is_team_url(url):
        raise ValueError(f"extract_team_parts: not a team URL: {url!r}")
    return parts[1], parts[3]


def parse_seed(raw: str, default_host: str = DEFAULT_HOST) -> Tuple[NodeKind, str]:
    """Parse a seed string into ``(kind, canonical_url)``.

    Accepts any of these input forms:

      * Bare login: ``"torvalds"`` → ``(USER_OR_ORG, "https://github.com/torvalds")``.
      * Slash form: ``"torvalds/linux"`` → ``(REPO, "https://github.com/torvalds/linux")``.
      * Full URL: ``"https://github.com/torvalds/linux"`` →
        ``(REPO, "https://github.com/torvalds/linux")``.
      * URLs with mixed case host or trailing slash are normalized.

    Raises ``ValueError`` for empty input, URLs missing a host, or paths
    deeper than a repo (other than team URLs, which are recognized).
    """
    if raw is None:
        raise ValueError("parse_seed: seed is None")
    seed = raw.strip()
    if not seed:
        raise ValueError("parse_seed: empty seed")

    if seed.startswith("http://") or seed.startswith("https://"):
        parsed = urlparse(seed)
        host = (parsed.hostname or "").lower()
        if not host:
            raise ValueError(f"parse_seed: URL has no host: {raw!r}")
        path = parsed.path or ""
        url = canonical_url(host, path)
    else:
        if seed.count("/") == 1:
            url = repo_url(seed, host=default_host)
        elif "/" not in seed:
            url = user_url(seed, host=default_host)
        else:
            raise ValueError(
                f"parse_seed: bare seed must be 'login' or 'owner/repo', got {raw!r}"
            )

    if is_team_url(url):
        return NodeKind.TEAM, url
    return kind_of(url), url


# --- Zenodo DOI URL handling (Spec 2) -------------------------------

import re as _re

_ZENODO_DOI_RE = _re.compile(
    r"^https?://doi\.org/(?P<prefix>10\.5281|10\.5072)/zenodo\.(?P<id>\d+)/?$",
    _re.IGNORECASE,
)


def is_zenodo_doi_url(raw: str) -> bool:
    """True if ``raw`` is a Zenodo DOI URL (production or sandbox).

    Production DOIs use the ``10.5281`` prefix; sandbox uses ``10.5072``.
    Non-Zenodo DOIs (e.g., journal DOIs) and non-DOI URLs return False.
    """
    if not isinstance(raw, str):
        return False
    return bool(_ZENODO_DOI_RE.match(raw))


def rewrite_zenodo_doi_url(raw: str) -> Optional[str]:
    """Rewrite a Zenodo DOI URL to its canonical platform URL.

    ``https://doi.org/10.5281/zenodo.7234562`` → ``https://zenodo.org/records/7234562``
    ``https://doi.org/10.5072/zenodo.9999``    → ``https://sandbox.zenodo.org/records/9999``

    Returns ``None`` for non-Zenodo DOIs and non-DOI URLs so callers can
    fall through to the next normalization strategy.
    """
    if not isinstance(raw, str):
        return None
    m = _ZENODO_DOI_RE.match(raw)
    if not m:
        return None
    prefix = m.group("prefix")
    record_id = m.group("id")
    host = "zenodo.org" if prefix == "10.5281" else "sandbox.zenodo.org"
    return f"https://{host}/records/{record_id}"
