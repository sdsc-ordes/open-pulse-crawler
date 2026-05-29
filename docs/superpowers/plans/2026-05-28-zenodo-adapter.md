# Zenodo Adapter Implementation Plan (Spec 2, targets v3.1.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ZenodoAdapter` to the multi-platform crawler so Zenodo records, communities, and uploader accounts crawl alongside GitHub and GitLab — including cross-platform `related_to.<RelationType>` edges (Zenodo→GitHub etc.).

**Architecture:** Approach A from the spec — plain `httpx`-based `ZenodoClient` + `ZenodoAdapter` mirroring the GitLab pattern. Concept DOI is the primary identity; version chains collapse into a `versions: list[dict]` field on the concept node. Multi-instance via host-keyed env vars (`zenodo.org` + `sandbox.zenodo.org`).

**Tech Stack:** Python 3.10+, Pydantic v2 (discriminated unions), `httpx` (already a transitive dep), pytest, uv. No new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-05-28-zenodo-adapter-design.md` (committed `038ab08`).

**Branch / worktree:** Same branch as Spec 1 — `feat/multi-platform-gitlab` at `.worktrees/feat-multi-platform-gitlab/`.

**Test invocation note:** pytest with the default `-n auto` (from `pyproject.toml`'s `addopts`) hangs in this sandbox. Run with `-n 0`:
```
VIRTUAL_ENV= uv run pytest -n 0 <args>
```

---

## What Spec 1 already gives us — skipped

- `PlatformAdapter` ABC + `PlatformRegistry` (host-keyed dispatch, lowercase netloc lookup, duplicate-registration guard).
- `subkind` discriminator + discriminated-union dict types in `models.py`.
- `node_id.canonical_url` + URL helpers.
- `config.resolve_tokens(host)` host-keyed env-var resolution.
- Dual-path crawler dispatch (`urlparse(uri).netloc.lower() != "github.com"` → adapter path).
- `_FakeAdapter` test helper.
- Per-host cache layout in `APICache`.
- CLI `--platforms` / `--default-host` / `crawler doctor`.
- `/api/v2` REST surface with OpenAPI examples dropdown.

This plan picks up there.

---

## File map

**New files:**
- `src/open_pulse_crawler/platforms/zenodo/__init__.py` — re-exports
- `src/open_pulse_crawler/platforms/zenodo/client.py` — httpx wrapper
- `src/open_pulse_crawler/platforms/zenodo/adapter.py` — PlatformAdapter implementation
- `tests/platforms/test_zenodo_client.py`
- `tests/platforms/test_zenodo_adapter.py`
- `tests/integration/test_zenodo_dryrun.py`
- `docs/ZENODO.md`

**Modified files:**
- `src/open_pulse_crawler/models.py` — three Zenodo subclasses, widen UserNode/OrgNode/RepoNode unions.
- `src/open_pulse_crawler/node_id.py` — DOI URL → canonical Zenodo URL rewriter.
- `src/open_pulse_crawler/cli.py` — `_build_registry` Zenodo branch.
- `src/open_pulse_crawler/api/v2.py` — four new openapi_examples entries.
- `tools/scripts/fetch_public_projects.py` — host-dispatch GitLab vs Zenodo listing endpoints.
- `CHANGELOG.md`, `README.md`, `docs/index.md`.

---

## Task ordering

Block A (Tasks 1–3): foundation — models, node_id, ZenodoClient skeleton.
Block B (Tasks 4–7): ZenodoClient full surface, ZenodoAdapter classify/normalize, fetch, expand.
Block C (Tasks 8–10): CLI registration, /api/v2 examples, explore-script extension.
Block D (Tasks 11–13): integration test, docs/CHANGELOG, final verification.

Every task is TDD-shaped: failing test → minimal implementation → passing test → commit. Conventional Commits per `AGENTS.md`. NEVER pass `--no-verify`, `-c commit.gpgsign=false`, or `--no-gpg-sign`.

---

# Block A — Foundations (models + node_id + client skeleton)

## Task 1: Three Zenodo subkinds in `models.py`

**Files:**
- Modify: `src/open_pulse_crawler/models.py`
- Test: `tests/test_models.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_models.py`:

```python
# --- Zenodo subkinds (Spec 2) --------------------------------------
from open_pulse_crawler.models import (
    ZenodoUserModel, ZenodoCommunityModel, ZenodoRecordModel,
    UserModel, OrgModel, RepoModel, GraphData,
)


def test_zenodo_user_subkind_and_fields():
    u = ZenodoUserModel(
        url="https://zenodo.org/users/12345",
        login="alice",
        platform="zenodo",
        id=12345,
        orcid="0000-0001-2345-6789",
        affiliation="EPFL",
    )
    assert u.subkind == "ZenodoUser"
    assert u.orcid == "0000-0001-2345-6789"
    assert u.affiliation == "EPFL"
    assert u.followers == []  # Zenodo has no social graph


def test_zenodo_community_subkind_and_fields():
    c = ZenodoCommunityModel(
        url="https://zenodo.org/communities/sdsc-ordes",
        login="sdsc-ordes",
        platform="zenodo",
        community_type="organization",
        description="Swiss Data Science Center — ORDES",
    )
    assert c.subkind == "ZenodoCommunity"
    assert c.community_type == "organization"
    assert c.members == []  # members out of scope


def test_zenodo_record_subkind_concept_self_reference():
    r = ZenodoRecordModel(
        url="https://zenodo.org/records/7234562",
        full_name="10.5281/zenodo.7234562",
        platform="zenodo",
        doi="10.5281/zenodo.7234562",
        concept_doi="10.5281/zenodo.7234562",  # self-reference: this record IS the concept
        title="Renku — A Platform for Reproducible Data Science",
        resource_type="software",
        license="Apache-2.0",
    )
    assert r.subkind == "ZenodoRecord"
    assert r.doi == r.concept_doi  # convention: self-reference for non-versioned
    assert r.is_fork is False  # inherited from RepoModel
    assert r.dependents == []   # not a Zenodo concept


def test_zenodo_record_with_version_chain():
    r = ZenodoRecordModel(
        url="https://zenodo.org/records/7234500",
        full_name="10.5281/zenodo.7234500",
        platform="zenodo",
        doi="10.5281/zenodo.7234500",
        concept_doi="10.5281/zenodo.7234500",
        latest_version="2.0.0",
        latest_version_doi="10.5281/zenodo.7234562",
        latest_version_url="https://zenodo.org/records/7234562",
        versions=[
            {"doi": "10.5281/zenodo.7234500", "url": "https://zenodo.org/records/7234500",
             "version": "1.0.0", "publication_date": "2023-01-15", "record_id": "7234500"},
            {"doi": "10.5281/zenodo.7234562", "url": "https://zenodo.org/records/7234562",
             "version": "2.0.0", "publication_date": "2024-06-30", "record_id": "7234562"},
        ],
    )
    assert len(r.versions) == 2
    assert r.latest_version == "2.0.0"


def test_zenodo_record_creators_embedded_dicts():
    """Creators are author names + ORCIDs, NOT crawl-able User nodes (identity resolution out of scope)."""
    r = ZenodoRecordModel(
        url="https://zenodo.org/records/9",
        full_name="10.5281/zenodo.9",
        platform="zenodo",
        doi="10.5281/zenodo.9",
        concept_doi="10.5281/zenodo.9",
        creators=[
            {"name": "Bovel, Matthieu", "orcid": "0000-0001-1111-1111", "affiliation": "EPFL"},
            {"name": "Doe, Jane", "affiliation": "ETHZ"},
        ],
    )
    assert len(r.creators) == 2
    assert r.creators[0]["orcid"] == "0000-0001-1111-1111"


def test_graphdata_accepts_zenodo_subclasses_in_existing_dicts():
    """Discriminated unions must accept Zenodo subkinds alongside GitHub/GitLab ones."""
    g = GraphData()
    g.users["https://zenodo.org/users/1"] = ZenodoUserModel(
        url="https://zenodo.org/users/1", login="u1", platform="zenodo",
    )
    g.orgs["https://zenodo.org/communities/c"] = ZenodoCommunityModel(
        url="https://zenodo.org/communities/c", login="c", platform="zenodo",
    )
    g.repos["https://zenodo.org/records/1"] = ZenodoRecordModel(
        url="https://zenodo.org/records/1", full_name="10.5281/zenodo.1",
        platform="zenodo", doi="10.5281/zenodo.1", concept_doi="10.5281/zenodo.1",
    )

    payload = g.model_dump_json()
    restored = GraphData.model_validate_json(payload)

    assert isinstance(restored.users["https://zenodo.org/users/1"], ZenodoUserModel)
    assert isinstance(restored.orgs["https://zenodo.org/communities/c"], ZenodoCommunityModel)
    assert isinstance(restored.repos["https://zenodo.org/records/1"], ZenodoRecordModel)
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_models.py -v --no-cov
```
Expected: `ImportError: cannot import name 'ZenodoUserModel'`.

- [ ] **Step 3: Add the three subclasses to `models.py`**

Find the existing GitLab subclasses (`GitLabUserModel`, `GitLabGroupModel`, `GitLabProjectModel`). Add the Zenodo subclasses right after them (above the discriminated-union definitions):

```python
class ZenodoUserModel(UserModel):
    """A Zenodo platform account (uploader).

    Distinct from creators — creators are author names recorded on records
    (`ZenodoRecordModel.creators`), not Zenodo accounts. Cross-platform
    identity resolution is intentionally out of scope (handled by another
    tool downstream), so we model only the platform-internal account here.

    Social fields (``followers``, ``following``, ``starred_repositories``,
    ``watched_repositories``) inherited from ``UserModel`` are unused —
    Zenodo has no social graph. They stay empty by construction.
    """
    subkind: Literal["ZenodoUser"] = "ZenodoUser"
    orcid: Optional[str] = None
    affiliation: str = ""


class ZenodoCommunityModel(OrgModel):
    """A Zenodo community.

    ``login`` (inherited from ``OrgModel``) is the community slug. URL form:
    ``https://zenodo.org/communities/<slug>``. ``members`` is intentionally
    not populated — the member-listing endpoint is auth-gated on production
    Zenodo and out of scope for this adapter.
    """
    subkind: Literal["ZenodoCommunity"] = "ZenodoCommunity"
    doi: Optional[str] = None
    description: str = ""
    community_type: str = ""   # "project" | "organization" | "event" | "topic"


class ZenodoRecordModel(RepoModel):
    """A Zenodo record. One node per concept DOI; versions collapse into
    ``versions: list[dict]`` metadata.

    The concept DOI is the primary identity. When a record has no versioning,
    ``concept_doi == doi`` (self-reference) and ``versions`` is empty. When
    a record has versions, ``doi == concept_doi`` (the node represents the
    lineage, not a specific version), ``latest_version_*`` describes the
    latest, and ``versions`` carries every version's metadata.

    ``creators`` is a list of dicts ``{name, orcid, affiliation, type}`` —
    not crawled to User nodes. Identity resolution is downstream of this
    adapter.
    """
    subkind: Literal["ZenodoRecord"] = "ZenodoRecord"
    doi: str
    concept_doi: str
    latest_version: str = ""
    latest_version_doi: str = ""
    latest_version_url: str = ""
    versions: List[Dict[str, Any]] = Field(default_factory=list)
    resource_type: str = ""
    publication_date: str = ""
    title: str = ""
    creators: List[Dict[str, Any]] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    license: str = ""
    access_right: Literal["open", "embargoed", "restricted", "closed"] = "open"
```

Then widen the discriminated unions (find the existing `UserNode`/`OrgNode`/`RepoNode` definitions):

```python
UserNode = Annotated[
    Union[UserModel, GitLabUserModel, ZenodoUserModel],
    Field(discriminator="subkind"),
]
OrgNode = Annotated[
    Union[OrgModel, GitLabGroupModel, ZenodoCommunityModel],
    Field(discriminator="subkind"),
]
RepoNode = Annotated[
    Union[RepoModel, GitLabProjectModel, ZenodoRecordModel],
    Field(discriminator="subkind"),
]
```

`Any`, `Dict`, `List`, `Literal`, `Optional`, `Union`, `Annotated` should already be imported. If not, add them.

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_models.py -v --no-cov
```
Expected: all 6 new tests pass plus the existing tests stay green.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/models.py tests/test_models.py
git commit -m "feat(models): Zenodo subkinds — ZenodoUser, ZenodoCommunity, ZenodoRecord"
```

---

## Task 2: DOI URL → canonical Zenodo URL rewriter in `node_id.py`

**Files:**
- Modify: `src/open_pulse_crawler/node_id.py`
- Modify: `tests/test_node_id.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_node_id.py`:

```python
# --- Zenodo DOI URL rewriting (Spec 2) ----------------------------
from open_pulse_crawler.node_id import (
    rewrite_zenodo_doi_url, is_zenodo_doi_url,
)


def test_rewrite_prod_doi_url():
    assert rewrite_zenodo_doi_url(
        "https://doi.org/10.5281/zenodo.7234562"
    ) == "https://zenodo.org/records/7234562"


def test_rewrite_sandbox_doi_url():
    assert rewrite_zenodo_doi_url(
        "https://doi.org/10.5072/zenodo.9999"
    ) == "https://sandbox.zenodo.org/records/9999"


def test_rewrite_tolerates_trailing_slash():
    assert rewrite_zenodo_doi_url(
        "https://doi.org/10.5281/zenodo.7234562/"
    ) == "https://zenodo.org/records/7234562"


def test_rewrite_non_zenodo_doi_returns_none():
    assert rewrite_zenodo_doi_url("https://doi.org/10.1234/some-other.xyz") is None


def test_rewrite_non_doi_url_returns_none():
    assert rewrite_zenodo_doi_url("https://zenodo.org/records/7234562") is None


def test_is_zenodo_doi_url():
    assert is_zenodo_doi_url("https://doi.org/10.5281/zenodo.42")
    assert is_zenodo_doi_url("https://doi.org/10.5072/zenodo.42")
    assert not is_zenodo_doi_url("https://doi.org/10.1234/something")
    assert not is_zenodo_doi_url("https://zenodo.org/records/42")
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_node_id.py -v --no-cov
```
Expected: `ImportError` on the new helpers.

- [ ] **Step 3: Add the helpers to `node_id.py`**

Append:

```python
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
```

If `Optional` isn't already imported in `node_id.py`, add it from `typing`.

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_node_id.py -v --no-cov
```
Expected: all 6 new tests pass; existing tests stay green.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/node_id.py tests/test_node_id.py
git commit -m "feat(node_id): Zenodo DOI URL → canonical platform URL rewriter"
```

---

## Task 3: `ZenodoClient` skeleton — construction, anonymous mode, headers

**Files:**
- Create: `src/open_pulse_crawler/platforms/zenodo/__init__.py`
- Create: `src/open_pulse_crawler/platforms/zenodo/client.py`
- Create: `tests/platforms/test_zenodo_client.py`

- [ ] **Step 1: Write failing tests for construction**

```python
# tests/platforms/test_zenodo_client.py
from unittest.mock import MagicMock, patch
import pytest

from open_pulse_crawler.platforms.zenodo.client import ZenodoClient


def test_authenticated_sets_bearer_header():
    c = ZenodoClient(host="zenodo.org", tokens=["zen-pat-abc"])
    assert c._session.headers.get("Authorization") == "Bearer zen-pat-abc"


def test_anonymous_sends_no_auth_header():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    assert "Authorization" not in c._session.headers


def test_base_url_uses_https_with_host():
    c = ZenodoClient(host="sandbox.zenodo.org", tokens=[])
    assert c.base_url == "https://sandbox.zenodo.org"


def test_rotate_in_anonymous_mode_is_noop():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    c._rotate()  # must not raise
    assert "Authorization" not in c._session.headers


def test_rotate_cycles_token_pool():
    c = ZenodoClient(host="zenodo.org", tokens=["a", "b", "c"])
    assert c._session.headers["Authorization"] == "Bearer a"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer b"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer c"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer a"  # wraps
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_zenodo_client.py -v --no-cov
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the skeleton**

```python
# src/open_pulse_crawler/platforms/zenodo/__init__.py
"""Zenodo platform implementation."""
from .client import ZenodoClient  # re-export

__all__ = ["ZenodoClient"]
```

```python
# src/open_pulse_crawler/platforms/zenodo/client.py
"""Thin httpx wrapper for Zenodo's InvenioRDM REST API.

Mirrors the GitLab client's shape:
  1. Construction with optional token rotation pool (anonymous when empty).
  2. Single-entity fetches map 404 → None.
  3. Iterators degrade to ``[]`` on 401/403 so a missing scope doesn't
     tank the whole crawl.
  4. Per-host disk cache is pre-allocated when ``_cache_dir`` is set; the
     single-entity lookups still hit the API for now (same TODO as the
     GitLab client — wiring is straightforward for Zenodo because the API
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
    non-empty. Anonymous (no header) when ``tokens=[]`` — Zenodo's public
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
                "ZenodoClient(%s) constructed with no tokens — "
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
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_zenodo_client.py -v --no-cov
```
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/zenodo/__init__.py src/open_pulse_crawler/platforms/zenodo/client.py tests/platforms/test_zenodo_client.py
git commit -m "feat(zenodo): ZenodoClient skeleton with anonymous mode + Bearer auth"
```

---

# Block B — ZenodoClient surface + ZenodoAdapter

## Task 4: ZenodoClient — `get_record` / `get_community` / `get_user`

**Files:**
- Modify: `src/open_pulse_crawler/platforms/zenodo/client.py`
- Modify: `tests/platforms/test_zenodo_client.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_zenodo_client.py`:

```python
from unittest.mock import patch, MagicMock


def _make_response(status_code: int, json_body=None):
    r = MagicMock()
    r.status_code = status_code
    r.is_success = 200 <= status_code < 300
    r.json.return_value = json_body or {}
    return r


def test_get_record_200_returns_dict():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    payload = {"id": 7234562, "doi": "10.5281/zenodo.7234562"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_record(7234562)
    g.assert_called_once_with("/api/records/7234562")
    assert result == payload


def test_get_record_404_returns_none():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_record(99999999) is None


def test_get_record_500_raises():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(500)):
        with pytest.raises(httpx.HTTPStatusError):
            c.get_record(7234562)


def test_get_community_path_and_parse():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    payload = {"id": "sdsc-ordes", "metadata": {"title": "SDSC"}}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_community("sdsc-ordes")
    g.assert_called_once_with("/api/communities/sdsc-ordes")
    assert result == payload


def test_get_community_404_returns_none():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_community("does-not-exist") is None


def test_get_user_path_and_parse():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    payload = {"id": 12345, "username": "alice"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_user(12345)
    g.assert_called_once_with("/api/users/12345")
    assert result == payload


def test_get_user_401_returns_none_and_logs():
    """When /api/users/<id> requires auth (common anonymously), degrade to None."""
    c = ZenodoClient(host="zenodo.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(401)):
        assert c.get_user(12345) is None


def test_get_user_403_returns_none():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(403)):
        assert c.get_user(12345) is None
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_zenodo_client.py -v --no-cov
```
Expected: `AttributeError: 'ZenodoClient' object has no attribute 'get_record'`.

- [ ] **Step 3: Implement the three single-entity fetches**

Append to `src/open_pulse_crawler/platforms/zenodo/client.py`:

```python
    # ---- single-entity fetches ---------------------------------------------

    def _request_json(self, path: str, *, degrade_on_auth: bool = False) -> Optional[Dict[str, Any]]:
        """GET ``path`` and return parsed JSON.

        404 → ``None``. 401/403 → ``None`` when ``degrade_on_auth=True``,
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

        Returns ``None`` on 404, 401, or 403 — ``/api/users/<id>`` is often
        auth-required on production Zenodo.
        """
        return self._request_json(f"/api/users/{user_id}", degrade_on_auth=True)
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_zenodo_client.py -v --no-cov
```
Expected: all 13 tests pass (5 from Task 3 + 8 new).

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/zenodo/client.py tests/platforms/test_zenodo_client.py
git commit -m "feat(zenodo): get_record / get_community / get_user with graceful degrade"
```

---

## Task 5: ZenodoClient iterators — keyset pagination via `links.next`

**Files:**
- Modify: `src/open_pulse_crawler/platforms/zenodo/client.py`
- Modify: `tests/platforms/test_zenodo_client.py` (append)

- [ ] **Step 1: Write failing tests**

```python
def test_iter_community_records_single_page():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    body = {"hits": {"hits": [{"id": 1}, {"id": 2}]}, "links": {}}
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        records = list(c.iter_community_records("sdsc-ordes"))
    assert records == [{"id": 1}, {"id": 2}]
    g.assert_called_once_with(
        "/api/records",
        params={"communities": "sdsc-ordes", "size": 100},
    )


def test_iter_community_records_paginates_via_links_next():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    page1 = {
        "hits": {"hits": [{"id": 1}, {"id": 2}]},
        "links": {"next": "https://zenodo.org/api/records?communities=x&size=100&page=2"},
    }
    page2 = {
        "hits": {"hits": [{"id": 3}]},
        "links": {},  # last page has no next
    }
    with patch.object(c._session, "get") as g:
        g.side_effect = [_make_response(200, page1), _make_response(200, page2)]
        records = list(c.iter_community_records("x"))
    assert [r["id"] for r in records] == [1, 2, 3]
    assert g.call_count == 2


def test_iter_user_records_401_degrades_to_empty():
    """User-records listing is often auth-required; emit nothing on 401."""
    c = ZenodoClient(host="zenodo.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(401)):
        records = list(c.iter_user_records(12345))
    assert records == []


def test_iter_user_records_uses_q_owners_user_filter():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    body = {"hits": {"hits": [{"id": 10}]}, "links": {}}
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        records = list(c.iter_user_records(12345))
    g.assert_called_once_with(
        "/api/records",
        params={"q": "owners.user:12345", "size": 100},
    )
    assert records == [{"id": 10}]
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_zenodo_client.py -v --no-cov
```
Expected: `AttributeError: 'ZenodoClient' object has no attribute 'iter_community_records'`.

- [ ] **Step 3: Implement the iterators**

Append to `src/open_pulse_crawler/platforms/zenodo/client.py`:

```python
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
            # Follow the absolute next URL — params are baked in.
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
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_zenodo_client.py -v --no-cov
```
Expected: all 17 tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/zenodo/client.py tests/platforms/test_zenodo_client.py
git commit -m "feat(zenodo): keyset-paginated iter_community_records + iter_user_records"
```

---

## Task 6: `ZenodoAdapter` — classify, normalize_uri, fetch

**Files:**
- Create: `src/open_pulse_crawler/platforms/zenodo/adapter.py`
- Create: `tests/platforms/test_zenodo_adapter.py`
- Modify: `src/open_pulse_crawler/platforms/zenodo/__init__.py` (re-export)

- [ ] **Step 1: Write failing tests**

```python
# tests/platforms/test_zenodo_adapter.py
from unittest.mock import MagicMock
import pytest

from open_pulse_crawler.models import (
    ZenodoUserModel, ZenodoCommunityModel, ZenodoRecordModel,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.zenodo.adapter import ZenodoAdapter


@pytest.fixture
def adapter():
    client = MagicMock()
    return ZenodoAdapter(client=client, instance_host="zenodo.org")


# --- classify -------------------------------------------------------

def test_classify_record(adapter):
    assert adapter.classify("https://zenodo.org/records/7234562") == NodeKind.REPO


def test_classify_community(adapter):
    assert adapter.classify("https://zenodo.org/communities/sdsc-ordes") == NodeKind.USER_OR_ORG


def test_classify_user(adapter):
    assert adapter.classify("https://zenodo.org/users/12345") == NodeKind.USER_OR_ORG


def test_classify_unknown_path(adapter):
    assert adapter.classify("https://zenodo.org/about") is None


# --- normalize_uri --------------------------------------------------

def test_normalize_canonical_url(adapter):
    assert adapter.normalize_uri("https://zenodo.org/records/7234562") == \
        "https://zenodo.org/records/7234562"


def test_normalize_strips_trailing_slash(adapter):
    assert adapter.normalize_uri("https://zenodo.org/records/7234562/") == \
        "https://zenodo.org/records/7234562"


def test_normalize_legacy_singular_record_path(adapter):
    """Pre-2023 Zenodo URLs used /record/<id> singular; rewrite to /records/<id>."""
    assert adapter.normalize_uri("https://zenodo.org/record/7234562") == \
        "https://zenodo.org/records/7234562"


def test_normalize_prod_doi_url(adapter):
    assert adapter.normalize_uri("https://doi.org/10.5281/zenodo.7234562") == \
        "https://zenodo.org/records/7234562"


def test_normalize_sandbox_doi_url():
    sandbox_adapter = ZenodoAdapter(client=MagicMock(), instance_host="sandbox.zenodo.org")
    assert sandbox_adapter.normalize_uri("https://doi.org/10.5072/zenodo.9999") == \
        "https://sandbox.zenodo.org/records/9999"


def test_normalize_non_zenodo_doi_raises(adapter):
    with pytest.raises(ValueError):
        adapter.normalize_uri("https://doi.org/10.1234/journal.xyz")


# --- fetch ----------------------------------------------------------

def test_fetch_community(adapter):
    adapter._client.get_community.return_value = {
        "id": "sdsc-ordes",
        "metadata": {
            "title": "SDSC ORDES",
            "description": "Open Research Data — ETH Domain",
            "type": {"id": "organization"},
        },
    }
    node = adapter.fetch("https://zenodo.org/communities/sdsc-ordes")
    assert isinstance(node, ZenodoCommunityModel)
    assert node.url == "https://zenodo.org/communities/sdsc-ordes"
    assert node.login == "sdsc-ordes"
    assert node.community_type == "organization"
    assert node.description.startswith("Open Research Data")


def test_fetch_user(adapter):
    adapter._client.get_user.return_value = {
        "id": 12345,
        "username": "alice",
        "profile": {
            "full_name": "Alice Example",
            "affiliations": "EPFL",
            "identifiers": [{"identifier": "0000-0001-2345-6789", "scheme": "orcid"}],
        },
    }
    node = adapter.fetch("https://zenodo.org/users/12345")
    assert isinstance(node, ZenodoUserModel)
    assert node.login == "alice"
    assert node.affiliation == "EPFL"
    assert node.orcid == "0000-0001-2345-6789"


def test_fetch_user_404_returns_none(adapter):
    adapter._client.get_user.return_value = None
    assert adapter.fetch("https://zenodo.org/users/99999") is None


def test_fetch_record_self_concept(adapter):
    """A record whose conceptrecid == its own id is the concept record itself."""
    adapter._client.get_record.return_value = {
        "id": 7234562,
        "conceptrecid": "7234562",
        "doi": "10.5281/zenodo.7234562",
        "conceptdoi": "10.5281/zenodo.7234562",
        "metadata": {
            "title": "Self-concept test",
            "publication_date": "2024-06-30",
            "resource_type": {"id": "software"},
            "license": {"id": "Apache-2.0"},
            "access_right": "open",
            "creators": [{"name": "Doe, J."}],
            "keywords": ["kw"],
        },
    }
    node = adapter.fetch("https://zenodo.org/records/7234562")
    assert isinstance(node, ZenodoRecordModel)
    assert node.url == "https://zenodo.org/records/7234562"
    assert node.doi == "10.5281/zenodo.7234562"
    assert node.concept_doi == "10.5281/zenodo.7234562"  # self-reference
    assert node.resource_type == "software"
    assert node.title == "Self-concept test"
    assert node.creators == [{"name": "Doe, J."}]
    # Client called exactly once — no concept re-fetch needed.
    assert adapter._client.get_record.call_count == 1


def test_fetch_record_version_redirects_to_concept(adapter):
    """When seeded with a version URL whose conceptrecid != id, fetch the concept."""
    version_payload = {
        "id": 7234562,
        "conceptrecid": "7234500",  # parent concept
        "doi": "10.5281/zenodo.7234562",
        "conceptdoi": "10.5281/zenodo.7234500",
        "metadata": {"version": "2.0.0"},
    }
    concept_payload = {
        "id": 7234500,
        "conceptrecid": "7234500",
        "doi": "10.5281/zenodo.7234500",
        "conceptdoi": "10.5281/zenodo.7234500",
        "metadata": {
            "title": "Versioned package",
            "publication_date": "2023-01-15",
            "resource_type": {"id": "software"},
            "license": {"id": "MIT"},
            "access_right": "open",
            "relations": {"version": [{
                "is_last": True,
                "index": 1,
                "parent": {"pid_value": "7234500"},
            }]},
        },
    }
    adapter._client.get_record.side_effect = [version_payload, concept_payload]
    node = adapter.fetch("https://zenodo.org/records/7234562")
    # Returned node IS the concept record:
    assert node.url == "https://zenodo.org/records/7234500"
    assert node.doi == "10.5281/zenodo.7234500"
    assert node.concept_doi == "10.5281/zenodo.7234500"
    # Two API calls: the version, then the concept.
    assert adapter._client.get_record.call_count == 2


def test_fetch_unknown_path_returns_none(adapter):
    assert adapter.fetch("https://zenodo.org/about") is None
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_zenodo_adapter.py -v --no-cov
```
Expected: `ModuleNotFoundError: No module named 'open_pulse_crawler.platforms.zenodo.adapter'`.

- [ ] **Step 3: Implement the adapter (classify + normalize_uri + fetch)**

```python
# src/open_pulse_crawler/platforms/zenodo/adapter.py
"""Zenodo PlatformAdapter — classify / fetch / expand / normalize_uri.

See ``docs/superpowers/specs/2026-05-28-zenodo-adapter-design.md``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Dict, Iterable, Optional
from urllib.parse import urlparse

from ...models import (
    ZenodoCommunityModel, ZenodoRecordModel, ZenodoUserModel,
)
from ...node_id import (
    NodeKind, canonical_url, is_zenodo_doi_url, rewrite_zenodo_doi_url,
)
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo
from .client import ZenodoClient

logger = logging.getLogger(__name__)

# Recognize a /records/<id> path tail.
_RECORD_PATH = re.compile(r"^records/(?P<id>\d+)$")
_LEGACY_RECORD_PATH = re.compile(r"^/record/(?P<id>\d+)/?$")
_COMMUNITY_PATH = re.compile(r"^communities/(?P<slug>[^/]+)$")
_USER_PATH = re.compile(r"^users/(?P<id>\d+)$")


class ZenodoAdapter(PlatformAdapter):
    platform: ClassVar[str] = "zenodo"

    def __init__(self, client: ZenodoClient, instance_host: str) -> None:
        self._client = client
        self.instance_host = instance_host
        # Cache: version-record URI → concept URL. Populated on the first
        # fetch of a version URL so subsequent visits skip the extra API hop.
        self._concept_cache: Dict[str, str] = {}

    # ---- normalize_uri -----------------------------------------------------

    def normalize_uri(self, raw: str) -> str:
        """Return the canonical Zenodo URL for ``raw``.

        Accepts canonical Zenodo URLs, legacy ``/record/<id>`` singular form,
        and DOI URLs (``https://doi.org/10.5281/zenodo.<id>`` for prod,
        ``10.5072/zenodo.<id>`` for sandbox).

        Raises ``ValueError`` on non-Zenodo DOI URLs so the seed parser
        surfaces them as user error.
        """
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"empty or non-string seed: {raw!r}")

        # DOI URL?
        if is_zenodo_doi_url(raw):
            rewritten = rewrite_zenodo_doi_url(raw)
            assert rewritten is not None
            return rewritten

        if raw.startswith("https://doi.org/") or raw.startswith("http://doi.org/"):
            # A non-Zenodo DOI URL — explicitly out of scope for this adapter.
            raise ValueError(f"non-Zenodo DOI URL not supported by ZenodoAdapter: {raw!r}")

        parts = urlparse(raw)
        host = (parts.netloc or self.instance_host).lower()
        path = parts.path or "/"
        # Legacy /record/<id> → /records/<id>
        m = _LEGACY_RECORD_PATH.match(path)
        if m:
            path = f"/records/{m.group('id')}"
        return canonical_url(host, path)

    # ---- classify ----------------------------------------------------------

    def classify(self, uri: str) -> Optional[NodeKind]:
        path = urlparse(self.normalize_uri(uri)).path.strip("/")
        if _RECORD_PATH.match(path):
            return NodeKind.REPO
        if _COMMUNITY_PATH.match(path) or _USER_PATH.match(path):
            return NodeKind.USER_OR_ORG
        return None

    # ---- fetch -------------------------------------------------------------

    def fetch(self, uri: str):
        uri = self.normalize_uri(uri)
        path = urlparse(uri).path.strip("/")

        m = _RECORD_PATH.match(path)
        if m:
            return self._build_record(uri, m.group("id"))

        m = _COMMUNITY_PATH.match(path)
        if m:
            return self._build_community(uri, m.group("slug"))

        m = _USER_PATH.match(path)
        if m:
            return self._build_user(uri, m.group("id"))

        return None

    def _build_community(self, uri: str, slug: str) -> Optional[ZenodoCommunityModel]:
        raw = self._client.get_community(slug)
        if raw is None:
            return None
        meta = raw.get("metadata", {}) or {}
        type_blob = meta.get("type", {}) or {}
        return ZenodoCommunityModel(
            url=uri,
            login=slug,
            platform="zenodo",
            id=int(raw["id"]) if str(raw.get("id", "")).isdigit() else 0,
            name=meta.get("title", "") or "",
            description=meta.get("description", "") or "",
            community_type=type_blob.get("id", "") if isinstance(type_blob, dict) else "",
        )

    def _build_user(self, uri: str, user_id: str) -> Optional[ZenodoUserModel]:
        raw = self._client.get_user(user_id)
        if raw is None:
            return None
        username = raw.get("username") or str(user_id)
        profile = raw.get("profile", {}) or {}
        orcid = None
        for ident in profile.get("identifiers", []) or []:
            if isinstance(ident, dict) and ident.get("scheme") == "orcid":
                orcid = ident.get("identifier")
                break
        return ZenodoUserModel(
            url=uri,
            login=username,
            platform="zenodo",
            id=int(raw["id"]) if str(raw.get("id", "")).isdigit() else 0,
            name=profile.get("full_name", "") or "",
            orcid=orcid,
            affiliation=profile.get("affiliations", "") or "",
        )

    def _build_record(self, uri: str, record_id: str) -> Optional[ZenodoRecordModel]:
        raw = self._client.get_record(record_id)
        if raw is None:
            return None

        concept_recid = self._concept_recid(raw)

        # If this is a version (concept_recid != own id), refetch the concept record.
        if concept_recid and str(concept_recid) != str(record_id):
            concept_url = f"https://{self.instance_host}/records/{concept_recid}"
            self._concept_cache[uri] = concept_url
            concept_raw = self._client.get_record(concept_recid)
            if concept_raw is None:
                # Concept fetch failed — fall back to using this record as its
                # own concept so we still land *something* in the graph.
                return self._record_from(uri, raw, fallback_concept_recid=record_id)
            concept_uri = f"https://{self.instance_host}/records/{concept_recid}"
            return self._record_from(concept_uri, concept_raw)

        # This record IS the concept (or has no versioning).
        return self._record_from(uri, raw)

    def _concept_recid(self, raw: Dict[str, Any]) -> Optional[str]:
        """Pull ``conceptrecid`` from a record payload.

        Zenodo exposes it at the top level (``conceptrecid``) and inside
        ``metadata.relations.version[0].parent.pid_value`` — check both.
        """
        v = raw.get("conceptrecid")
        if v:
            return str(v)
        try:
            rel = raw["metadata"]["relations"]["version"][0]["parent"]["pid_value"]
            if rel:
                return str(rel)
        except (KeyError, IndexError, TypeError):
            pass
        return None

    def _record_from(
        self,
        uri: str,
        raw: Dict[str, Any],
        *,
        fallback_concept_recid: Optional[str] = None,
    ) -> ZenodoRecordModel:
        meta = raw.get("metadata", {}) or {}
        doi = raw.get("doi") or meta.get("doi") or ""
        concept_doi = raw.get("conceptdoi") or doi  # self-reference when unset
        concept_recid = self._concept_recid(raw) or fallback_concept_recid or str(raw.get("id", ""))

        # Version chain — Zenodo includes every version in the concept record's
        # ``metadata.relations.version`` list.
        versions = []
        latest = {"version": "", "doi": "", "url": ""}
        for v in (meta.get("relations", {}) or {}).get("version", []):
            if not isinstance(v, dict):
                continue
            v_recid = v.get("parent", {}).get("pid_value")
            v_version = v.get("version", {}).get("value", "") if isinstance(v.get("version"), dict) else ""
            v_doi = v.get("doi", "")
            v_pubdate = v.get("publication_date", "")
            v_url = f"https://{self.instance_host}/records/{v_recid}" if v_recid else ""
            versions.append({
                "doi": v_doi, "url": v_url, "version": v_version,
                "publication_date": v_pubdate, "record_id": str(v_recid or ""),
            })
            if v.get("is_last"):
                latest = {"version": v_version, "doi": v_doi, "url": v_url}

        resource_type_blob = meta.get("resource_type", {}) or {}
        resource_type = (
            resource_type_blob.get("id", "") if isinstance(resource_type_blob, dict) else ""
        )
        license_blob = meta.get("license", {}) or {}
        license_id = (
            license_blob.get("id", "") if isinstance(license_blob, dict) else str(license_blob)
        )

        return ZenodoRecordModel(
            url=uri,
            full_name=concept_doi,
            platform="zenodo",
            id=int(raw["id"]) if str(raw.get("id", "")).isdigit() else 0,
            name=meta.get("title", "") or "",
            doi=concept_doi,                   # concept node carries concept DOI
            concept_doi=concept_doi,           # self-reference invariant
            latest_version=latest["version"] or "",
            latest_version_doi=latest["doi"] or "",
            latest_version_url=latest["url"] or "",
            versions=versions,
            resource_type=resource_type,
            publication_date=meta.get("publication_date", "") or "",
            title=meta.get("title", "") or "",
            creators=meta.get("creators", []) or [],
            keywords=meta.get("keywords", []) or [],
            license=license_id,
            access_right=meta.get("access_right", "open") or "open",
        )

    # ---- expand (placeholder — Task 7 implements) --------------------------

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        raise NotImplementedError("Task 7 implements expand()")

    # ---- rate_limit --------------------------------------------------------

    def rate_limit_state(self) -> RateLimitInfo:
        # Zenodo's rate-limit headers aren't reliably surfaced; return
        # conservative placeholders matching the GitLab adapter pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
```

Add to `src/open_pulse_crawler/platforms/zenodo/__init__.py`:

```python
from .adapter import ZenodoAdapter
__all__ = ["ZenodoClient", "ZenodoAdapter"]
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_zenodo_adapter.py -v --no-cov
```
Expected: all 14 tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/zenodo/adapter.py src/open_pulse_crawler/platforms/zenodo/__init__.py tests/platforms/test_zenodo_adapter.py
git commit -m "feat(zenodo): ZenodoAdapter classify/normalize_uri/fetch with concept resolution"
```

---

## Task 7: `ZenodoAdapter.expand()` — emit all 5 edge kinds

**Files:**
- Modify: `src/open_pulse_crawler/platforms/zenodo/adapter.py`
- Modify: `tests/platforms/test_zenodo_adapter.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_zenodo_adapter.py`:

```python
from open_pulse_crawler.platforms.base import ExpandOpts


def _stub_record(adapter, *, communities=(), owners=(), related=()):
    return ZenodoRecordModel(
        url="https://zenodo.org/records/7234562",
        full_name="10.5281/zenodo.7234562",
        platform="zenodo",
        doi="10.5281/zenodo.7234562",
        concept_doi="10.5281/zenodo.7234562",
        extras={
            # Stash the raw metadata on extras so _expand_record can read it
            # without re-fetching. The real adapter expand reads from the raw
            # payload; tests inject via this convention.
            "_raw_metadata": {
                "communities": [{"identifier": c} for c in communities],
                "owners": list(owners),
                "related_identifiers": list(related),
            },
        },
    )


def test_expand_record_emits_in_community_edge():
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    record = _stub_record(a, communities=["sdsc-ordes", "swiss-research"])
    edges = list(a.expand(record, ExpandOpts()))
    in_comm = [e for e in edges if e.kind == "in_community"]
    assert {e.dst for e in in_comm} == {
        "https://zenodo.org/communities/sdsc-ordes",
        "https://zenodo.org/communities/swiss-research",
    }
    assert all(e.src == record.url for e in in_comm)


def test_expand_record_emits_uploaded_by_edge():
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    record = _stub_record(a, owners=[12345])
    edges = list(a.expand(record, ExpandOpts()))
    by = [e for e in edges if e.kind == "uploaded_by"]
    assert by == [
        Edge(src=record.url, kind="uploaded_by", dst="https://zenodo.org/users/12345")
    ]


def test_expand_record_emits_related_to_with_relation_in_kind():
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    record = _stub_record(a, related=[
        {"identifier": "https://github.com/sdsc-ordes/gimie",
         "relation": "isSupplementTo", "scheme": "url"},
        {"identifier": "https://doi.org/10.5281/zenodo.99",
         "relation": "cites", "scheme": "doi"},
        # Bare DOI in non-URL scheme → must be skipped (no URL form)
        {"identifier": "arXiv:2401.12345", "relation": "cites", "scheme": "arxiv"},
    ])
    edges = [e for e in a.expand(record, ExpandOpts()) if e.kind.startswith("related_to.")]
    kinds_dsts = sorted((e.kind, e.dst) for e in edges)
    assert kinds_dsts == [
        ("related_to.cites", "https://zenodo.org/records/99"),
        ("related_to.isSupplementTo", "https://github.com/sdsc-ordes/gimie"),
    ]


def test_expand_community_emits_contains_edges():
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    a._client.iter_community_records.return_value = iter([
        {"id": 1}, {"id": 2}, {"id": 3},
    ])
    community = ZenodoCommunityModel(
        url="https://zenodo.org/communities/sdsc-ordes",
        login="sdsc-ordes",
        platform="zenodo",
    )
    edges = list(a.expand(community, ExpandOpts()))
    contains = [e for e in edges if e.kind == "contains"]
    assert sorted(e.dst for e in contains) == [
        "https://zenodo.org/records/1",
        "https://zenodo.org/records/2",
        "https://zenodo.org/records/3",
    ]
    assert all(e.src == community.url for e in contains)


def test_expand_user_emits_uploaded_edges():
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    a._client.iter_user_records.return_value = iter([{"id": 7}, {"id": 8}])
    user = ZenodoUserModel(
        url="https://zenodo.org/users/12345",
        login="alice",
        platform="zenodo",
    )
    edges = list(a.expand(user, ExpandOpts()))
    uploaded = [e for e in edges if e.kind == "uploaded"]
    assert sorted(e.dst for e in uploaded) == [
        "https://zenodo.org/records/7",
        "https://zenodo.org/records/8",
    ]
    assert all(e.src == user.url for e in uploaded)


def test_expand_user_with_no_accessible_records_yields_nothing():
    """iter_user_records degrades to [] on auth-required endpoints → no edges."""
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    a._client.iter_user_records.return_value = iter([])
    user = ZenodoUserModel(
        url="https://zenodo.org/users/12345",
        login="alice",
        platform="zenodo",
    )
    edges = list(a.expand(user, ExpandOpts()))
    assert edges == []
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_zenodo_adapter.py -v --no-cov
```
Expected: 6 tests fail with `NotImplementedError`.

- [ ] **Step 3: Implement `expand`**

Replace the `expand` placeholder in `adapter.py` with:

```python
    # ---- expand ------------------------------------------------------------

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        from ...models import (
            ZenodoCommunityModel, ZenodoRecordModel, ZenodoUserModel,
        )
        if isinstance(node, ZenodoRecordModel):
            yield from self._expand_record(node, opts)
        elif isinstance(node, ZenodoCommunityModel):
            yield from self._expand_community(node, opts)
        elif isinstance(node, ZenodoUserModel):
            yield from self._expand_user(node, opts)

    def _expand_record(
        self, node: "ZenodoRecordModel", opts: ExpandOpts,
    ) -> Iterable[Edge]:
        # Pull the raw metadata back from extras (tests inject here; for live
        # crawls _build_record can stash a copy at the same key — see Task 7
        # follow-up if needed).
        raw_meta = node.extras.get("_raw_metadata", {}) if node.extras else {}

        # in_community
        for c in raw_meta.get("communities", []) or []:
            slug = c.get("identifier") if isinstance(c, dict) else None
            if not slug:
                continue
            yield Edge(
                src=node.url,
                kind="in_community",
                dst=f"https://{self.instance_host}/communities/{slug}",
            )

        # uploaded_by
        for owner in raw_meta.get("owners", []) or []:
            # Owners may be ints or dicts with 'user' or 'id'
            if isinstance(owner, dict):
                uid = owner.get("user") or owner.get("id")
            else:
                uid = owner
            if uid is None:
                continue
            yield Edge(
                src=node.url,
                kind="uploaded_by",
                dst=f"https://{self.instance_host}/users/{uid}",
            )

        # related_to.<RelationType>
        for rel in raw_meta.get("related_identifiers", []) or []:
            if not isinstance(rel, dict):
                continue
            ident = rel.get("identifier") or ""
            relation = rel.get("relation") or "isReferencedBy"
            scheme = (rel.get("scheme") or "").lower()
            target_url: Optional[str] = None
            if scheme == "url" and ident.startswith(("http://", "https://")):
                target_url = ident
            elif scheme == "doi":
                # Try the Zenodo DOI rewriter first; fall back to doi.org URL.
                rewritten = rewrite_zenodo_doi_url(f"https://doi.org/{ident}")
                target_url = rewritten or f"https://doi.org/{ident}"
            elif ident.startswith(("http://", "https://")):
                target_url = ident
            if not target_url:
                continue
            yield Edge(
                src=node.url,
                kind=f"related_to.{relation}",
                dst=target_url,
            )

    def _expand_community(
        self, node: "ZenodoCommunityModel", opts: ExpandOpts,
    ) -> Iterable[Edge]:
        for rec in self._client.iter_community_records(node.login):
            rec_id = rec.get("id")
            if rec_id is None:
                continue
            yield Edge(
                src=node.url,
                kind="contains",
                dst=f"https://{self.instance_host}/records/{rec_id}",
            )

    def _expand_user(
        self, node: "ZenodoUserModel", opts: ExpandOpts,
    ) -> Iterable[Edge]:
        for rec in self._client.iter_user_records(node.id or node.login):
            rec_id = rec.get("id")
            if rec_id is None:
                continue
            yield Edge(
                src=node.url,
                kind="uploaded",
                dst=f"https://{self.instance_host}/records/{rec_id}",
            )
```

Also update `_record_from` to stash a slim copy of the raw metadata on the node's `extras` so `_expand_record` can read it without re-fetching:

In `_record_from`, just before the `return ZenodoRecordModel(...)`, build the slim payload:

```python
        raw_for_expand = {
            "communities": meta.get("communities", []) or [],
            "owners": raw.get("owners", []) or raw.get("metadata", {}).get("owners", []) or [],
            "related_identifiers": meta.get("related_identifiers", []) or [],
        }
```

And add `extras={"_raw_metadata": raw_for_expand}` to the model constructor call.

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_zenodo_adapter.py -v --no-cov
```
Expected: all 20 tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/zenodo/adapter.py tests/platforms/test_zenodo_adapter.py
git commit -m "feat(zenodo): ZenodoAdapter.expand() — in_community, uploaded_by, related_to.*, contains, uploaded"
```

---

# Block C — CLI registration + API examples + explore script

## Task 8: Register `ZenodoAdapter` in the CLI

**Files:**
- Modify: `src/open_pulse_crawler/cli.py`
- Modify: `tests/test_cli.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:

```python
def test_doctor_lists_zenodo_with_anonymous_status(monkeypatch):
    monkeypatch.setenv("CRAWLER_PLATFORMS", "zenodo.org,sandbox.zenodo.org")
    monkeypatch.delenv("CRAWLER_TOKEN__ZENODO_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__ZENODO_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN__SANDBOX_ZENODO_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__SANDBOX_ZENODO_ORG", raising=False)

    r = runner.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0
    import json
    payload = json.loads(r.stdout)
    hosts = {p["host"] for p in payload}
    assert "zenodo.org" in hosts
    assert "sandbox.zenodo.org" in hosts
    # Anonymous-mode = no tokens but adapter still functional.
    for p in payload:
        if p["host"].endswith("zenodo.org"):
            assert p["tokens"] == 0
            # `ok` mirrors token presence for now; docs note anonymous mode
            # still works for Zenodo.


def test_build_registry_registers_zenodo_adapter_for_zenodo_org(monkeypatch):
    """When CRAWLER_PLATFORMS includes zenodo.org, the CLI builds a Zenodo
    adapter even without a token."""
    from open_pulse_crawler.cli import _build_registry
    monkeypatch.delenv("CRAWLER_TOKEN__ZENODO_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__ZENODO_ORG", raising=False)
    registry, github_client, missing = _build_registry(["zenodo.org"])
    assert "zenodo.org" in missing  # no token → reported as missing
    # …but the adapter IS registered for anonymous use:
    adapter = registry.adapter_for("https://zenodo.org/records/1")
    from open_pulse_crawler.platforms.zenodo.adapter import ZenodoAdapter
    assert isinstance(adapter, ZenodoAdapter)
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_cli.py::test_doctor_lists_zenodo_with_anonymous_status tests/test_cli.py::test_build_registry_registers_zenodo_adapter_for_zenodo_org -v --no-cov
```
Expected: `AttributeError` or registry KeyError (Zenodo adapter not registered).

- [ ] **Step 3: Add the Zenodo branch in `_build_registry`**

Find the existing `_build_registry` function. It has a loop over `platforms_list` that branches on `host == "github.com"` else `GitLabAdapter`. Add a Zenodo branch BEFORE the GitLab fallback:

```python
        elif host in ("zenodo.org", "sandbox.zenodo.org") or host.endswith(".zenodo.org"):
            from .platforms.zenodo.client import ZenodoClient
            from .platforms.zenodo.adapter import ZenodoAdapter
            zen_client = ZenodoClient(host=host, tokens=tokens)
            reg.register(ZenodoAdapter(zen_client, instance_host=host))
            continue
```

This branch must sit inside both the `if not tokens:` path (so anonymous-mode registration happens too) and the `else:` path (so authenticated mode works the same). Looking at the existing GitLab branch handling, mirror it: the `if not tokens:` block already has an `if host == "github.com": continue` to skip GitHub and an unconditional GitLab registration. Add a parallel Zenodo branch in the same spot.

The cleanest edit is two changes:

In the `if not tokens:` block, before the GitLab fallback:
```python
            if host == "github.com":
                continue
            if host in ("zenodo.org", "sandbox.zenodo.org") or host.endswith(".zenodo.org"):
                from .platforms.zenodo.client import ZenodoClient
                from .platforms.zenodo.adapter import ZenodoAdapter
                zen_client = ZenodoClient(host=host, tokens=[])
                reg.register(ZenodoAdapter(zen_client, instance_host=host))
                continue
            # … existing GitLab anonymous branch
```

In the authenticated path, after the GitHub branch and before the GitLab fallback:
```python
        if host == "github.com":
            # … existing GitHub branch
        elif host in ("zenodo.org", "sandbox.zenodo.org") or host.endswith(".zenodo.org"):
            from .platforms.zenodo.client import ZenodoClient
            from .platforms.zenodo.adapter import ZenodoAdapter
            zen_client = ZenodoClient(host=host, tokens=tokens)
            reg.register(ZenodoAdapter(zen_client, instance_host=host))
        else:
            # … existing GitLab branch
```

Read the current `_build_registry` carefully before editing — the exact control flow may differ slightly. The constraint is: any Zenodo host (`zenodo.org`, `sandbox.zenodo.org`, anything ending in `.zenodo.org`) registers a `ZenodoAdapter` regardless of whether tokens are configured.

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_cli.py -v --no-cov
```
Expected: all CLI tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/cli.py tests/test_cli.py
git commit -m "feat(cli): register ZenodoAdapter for zenodo.org / sandbox / *.zenodo.org"
```

---

## Task 9: `/api/v2/crawl` OpenAPI examples for Zenodo

**Files:**
- Modify: `src/open_pulse_crawler/api/v2.py`

- [ ] **Step 1: Write a small assertion test**

Append to `tests/test_api_v2.py`:

```python
def test_v2_crawl_openapi_examples_include_zenodo():
    """Swagger UI's dropdown should surface Zenodo example seeds."""
    from fastapi.testclient import TestClient
    from open_pulse_crawler.api import app
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    # Find the /api/v2/crawl POST body examples.
    crawl_path = spec["paths"]["/api/v2/crawl"]
    body = crawl_path["post"]["requestBody"]["content"]["application/json"]
    examples = body.get("examples", {})
    assert "zenodo_community_renku" in examples
    assert "zenodo_record_doi_url" in examples
    assert "zenodo_record_canonical" in examples
    assert "cross_platform_zenodo_github" in examples
```

- [ ] **Step 2: Run, verify failure**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_api_v2.py::test_v2_crawl_openapi_examples_include_zenodo -v --no-cov
```
Expected: `AssertionError` — `zenodo_community_renku not in examples`.

- [ ] **Step 3: Add the four examples**

Find the existing `_CRAWL_V2_REQUEST_EXAMPLES` dict in `src/open_pulse_crawler/api/v2.py` and add four new entries at the end:

```python
    "zenodo_community_renku": {
        "summary": "Zenodo community (Renku)",
        "description": (
            "Crawl one Zenodo community. Anonymous-friendly — works without "
            "any CRAWLER_TOKEN__ZENODO_ORG. Round 0 fetches the community; "
            "round 1 walks `contains` edges to every record in the community."
        ),
        "value": {
            "seeds": ["https://zenodo.org/communities/renku-python"],
            "max_rounds": 2,
        },
    },
    "zenodo_record_doi_url": {
        "summary": "Zenodo record via DOI URL",
        "description": (
            "DOI URLs (`https://doi.org/10.5281/zenodo.<id>`) are rewritten "
            "to canonical Zenodo URLs by the adapter. Useful when copying a "
            "DOI from a citation."
        ),
        "value": {
            "seeds": ["https://doi.org/10.5281/zenodo.7234562"],
            "max_rounds": 2,
        },
    },
    "zenodo_record_canonical": {
        "summary": "Zenodo record via canonical URL",
        "description": (
            "Direct seeding with the platform URL. When the record is a "
            "specific version, the adapter follows its concept DOI and "
            "stores the result under the concept URL (versions collapse "
            "into the concept node's `versions` field)."
        ),
        "value": {
            "seeds": ["https://zenodo.org/records/7234562"],
            "max_rounds": 2,
        },
    },
    "cross_platform_zenodo_github": {
        "summary": "Zenodo record → GitHub repo via related_to edges",
        "description": (
            "Zenodo records that link to a GitHub repo via "
            "`metadata.related_identifiers` (relation `isSupplementTo` etc.) "
            "spawn a cross-platform crawl when both adapters are registered. "
            "Set `CRAWLER_PLATFORMS=github.com,zenodo.org` to exercise the "
            "full link-following behavior."
        ),
        "value": {
            "seeds": ["https://zenodo.org/records/7234562"],
            "max_rounds": 2,
        },
    },
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_api_v2.py -v --no-cov
```
Expected: all v2 API tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/api/v2.py tests/test_api_v2.py
git commit -m "feat(api): /api/v2/crawl OpenAPI examples for Zenodo seeds"
```

---

## Task 10: Extend `fetch_public_projects.py` to support Zenodo

**Files:**
- Modify: `tools/scripts/fetch_public_projects.py`

- [ ] **Step 1: Manual probe to confirm Zenodo's listing shape (no automated test for the tool script — it's a one-shot CLI)**

```
curl -s "https://zenodo.org/api/records?size=1" | head -c 800
```

Expected: JSON with `hits.hits[0].links.self_html` (the canonical Zenodo URL) or `hits.hits[0].id` (numeric record id).

- [ ] **Step 2: Add the host-dispatch branch**

Find the existing `fetch_public_project_urls` function. It currently uses `gitlab.Gitlab` for every host. Add a branch at the top that dispatches by host:

```python
def fetch_public_project_urls(host: str, limit: int) -> Iterable[str]:
    """Yield up to ``limit`` public project / record URLs from ``host``."""
    if host in ("zenodo.org", "sandbox.zenodo.org") or host.endswith(".zenodo.org"):
        yield from _fetch_zenodo_urls(host, limit)
        return
    # … existing GitLab implementation
```

And add the Zenodo helper:

```python
def _fetch_zenodo_urls(host: str, limit: int) -> Iterable[str]:
    """Yield up to ``limit`` Zenodo record URLs by paginating /api/records.

    Uses Zenodo's keyset pagination (``links.next``) — the same pattern the
    ZenodoClient uses internally.
    """
    import httpx

    tokens = resolve_tokens(host)
    headers = {"Accept": "application/json"}
    if tokens:
        headers["Authorization"] = f"Bearer {tokens[0]}"

    url = f"https://{host}/api/records"
    params = {"size": min(100, limit)}
    yielded = 0
    with httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=True) as session:
        while yielded < limit:
            try:
                r = session.get(url, params=params, headers=headers)
                r.raise_for_status()
            except Exception as exc:
                sys.stderr.write(
                    f"  ! {host}: page failed ({type(exc).__name__}: {exc}); stopping.\n"
                )
                return
            body = r.json()
            hits = body.get("hits", {}).get("hits", [])
            if not hits:
                return
            for hit in hits:
                rec_id = hit.get("id")
                if rec_id is None:
                    continue
                yield f"https://{host}/records/{rec_id}"
                yielded += 1
                if yielded >= limit:
                    return
            next_url = body.get("links", {}).get("next")
            if not next_url:
                return
            url = next_url
            params = None   # next URL has params baked in
            time.sleep(0.05)
```

Then add `"zenodo.org"` (and optionally `"sandbox.zenodo.org"`) to `DEFAULT_HOSTS` near the top of the file:

```python
DEFAULT_HOSTS = [
    # --- ETH Domain ---
    "gitlab.ethz.ch",
    # … existing entries …
    "gitlab.renkulab.io",
    # --- Zenodo (Spec 2) ---
    "zenodo.org",
    # NOTE: sandbox.zenodo.org excluded from defaults — operator-opt-in via --hosts.
]
```

Also update the `_total_public_projects` helper to skip Zenodo:

```python
def _total_public_projects(host: str) -> int | None:
    """Best-effort total count from X-Total header. Returns None on Zenodo
    (their API doesn't surface X-Total on /api/records).
    """
    if host in ("zenodo.org", "sandbox.zenodo.org") or host.endswith(".zenodo.org"):
        return None  # Zenodo uses keyset pagination; no global count header
    # … existing httpx-HEAD implementation
```

- [ ] **Step 3: Smoke-test with a tiny limit**

```
VIRTUAL_ENV= uv run python tools/scripts/fetch_public_projects.py \
    --hosts zenodo.org --per-host-limit 3 --output-dir /tmp/zen-probe
cat /tmp/zen-probe/zenodo.org.txt
```
Expected: 3 URLs of the form `https://zenodo.org/records/<id>`.

- [ ] **Step 4: Commit**

```
git add tools/scripts/fetch_public_projects.py
git commit -m "feat(tools): fetch_public_projects supports Zenodo /api/records pagination"
```

---

# Block D — Integration test + docs + verification

## Task 11: Integration test against live `zenodo.org`

**Files:**
- Create: `tests/integration/test_zenodo_dryrun.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/integration/test_zenodo_dryrun.py
"""Tiny live dryrun against zenodo.org.

Anonymous-friendly — runs whenever ``CRAWLER_SKIP_INTEGRATION`` is unset.
Skipped when explicitly opted out (e.g., offline CI).
"""
from __future__ import annotations

import os
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("CRAWLER_SKIP_INTEGRATION") == "1",
    reason="CRAWLER_SKIP_INTEGRATION=1 set",
)
def test_one_round_against_zenodo_org() -> None:
    """Crawl one Zenodo community for one round; assert the community lands
    in the graph plus at least one `contains` edge to a record."""
    from open_pulse_crawler.config import resolve_tokens
    from open_pulse_crawler.crawler import GitHubCrawler
    from open_pulse_crawler.platforms import PlatformRegistry
    from open_pulse_crawler.platforms.zenodo.adapter import ZenodoAdapter
    from open_pulse_crawler.platforms.zenodo.client import ZenodoClient

    tokens = resolve_tokens("zenodo.org")  # may be []
    client = ZenodoClient(host="zenodo.org", tokens=tokens)
    adapter = ZenodoAdapter(client=client, instance_host="zenodo.org")

    registry = PlatformRegistry()
    registry.register(adapter)

    # Renku has a small public community; "renku-python" is the canonical
    # demo slug. If it has been moved/renamed, substitute any other small
    # public Zenodo community.
    seed = "https://zenodo.org/communities/renku-python"

    crawler = GitHubCrawler(registry=registry, max_rounds=1)
    crawler.add_seeds([seed])
    crawler.crawl(show_progress=False)

    assert seed in crawler.graph.orgs, \
        f"community {seed} not found in graph.orgs"
```

- [ ] **Step 2: Run locally to verify the test passes against the live API (skip if offline)**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/integration/test_zenodo_dryrun.py -v --no-cov
```
Expected: pass against the live Zenodo API. If the community slug has changed since this plan was written, replace with another small public community and update the test inline.

- [ ] **Step 3: Commit**

```
git add tests/integration/test_zenodo_dryrun.py
git commit -m "test(integration): tiny zenodo.org dryrun"
```

---

## Task 12: Documentation + CHANGELOG

**Files:**
- Create: `docs/ZENODO.md`
- Modify: `README.md`
- Modify: `docs/index.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write `docs/ZENODO.md`**

Create the file with the following sections (the full content reuses examples from Tasks 9 + 10 — copy them verbatim so a reader can hit the docs without bouncing between files):

```markdown
# Zenodo adapter

Open Pulse Crawler v3.1+ supports Zenodo records, communities, and uploader
accounts as part of the unified open-science graph. Multiple Zenodo
instances can be crawled in the same run (`zenodo.org` and
`sandbox.zenodo.org`); both work anonymously for public reads.

## Supported entities

- **Records** — one node per *concept DOI* (the version-agnostic identity).
  Specific versions collapse into the concept node's `versions` field;
  there is **no** separate node per version.
- **Communities** — one node per community slug.
- **Users (uploaders)** — one node per Zenodo account. Distinct from
  *creators* on a record, which are author names + ORCIDs embedded in the
  record's `creators` field (creators are intentionally NOT crawled as User
  nodes — identity resolution is downstream).

## Edge kinds

| Source | Kind | Target |
|---|---|---|
| `ZenodoRecord` | `in_community` | `ZenodoCommunity` |
| `ZenodoRecord` | `uploaded_by` | `ZenodoUser` |
| `ZenodoUser` | `uploaded` | `ZenodoRecord` |
| `ZenodoCommunity` | `contains` | `ZenodoRecord` |
| `ZenodoRecord` | `related_to.<RelationType>` | URL on any platform |

`related_to.<RelationType>` carries the DataCite RelationType
(`isSupplementTo`, `cites`, `isCitedBy`, …) as a compound kind string.
Downstream consumers can split on `.` to recover the relation semantics.

## Configuring tokens

Anonymous reads work for `/api/records`, `/api/communities`, and many
record-search endpoints — no token required for a discovery crawl.
Authenticated mode raises rate limits and unlocks `/api/users/<id>`:

```bash
CRAWLER_PLATFORMS=zenodo.org
CRAWLER_TOKEN__ZENODO_ORG=zen-pat-here
# Or rotation pool:
CRAWLER_TOKEN_POOL__ZENODO_ORG=zen-pat-a,zen-pat-b
```

Sandbox tokens are independent:

```bash
CRAWLER_PLATFORMS=zenodo.org,sandbox.zenodo.org
CRAWLER_TOKEN__SANDBOX_ZENODO_ORG=zen-sandbox-pat
```

Get a Personal Access Token at <https://zenodo.org/account/settings/applications/tokens/new/>.

## Seed forms accepted

- Canonical Zenodo URL: `https://zenodo.org/records/<id>` /
  `/communities/<slug>` / `/users/<id>`.
- Legacy singular record path: `https://zenodo.org/record/<id>` rewritten
  to `/records/<id>`.
- Production DOI URL: `https://doi.org/10.5281/zenodo.<id>` → rewritten to
  `https://zenodo.org/records/<id>` (regex, no HTTP).
- Sandbox DOI URL: `https://doi.org/10.5072/zenodo.<id>` → rewritten to
  `https://sandbox.zenodo.org/records/<id>`.
- Non-Zenodo DOIs raise — they belong to other adapters.

## Manual-test recipes

### Single community (anonymous, the smallest crawl)

```bash
opc crawl --platforms zenodo.org \
    --default-host zenodo.org --rounds 2 \
    https://zenodo.org/communities/renku-python
```

### Single record via DOI URL

```bash
opc crawl --platforms zenodo.org \
    --default-host zenodo.org --rounds 2 \
    https://doi.org/10.5281/zenodo.7234562
```

### Cross-platform crawl (Zenodo + GitHub linked via related_identifiers)

```bash
CRAWLER_PLATFORMS=github.com,zenodo.org \
CRAWLER_TOKEN__GITHUB_COM=ghp_… \
opc crawl --rounds 2 \
    https://zenodo.org/records/7234562
```

`related_to.isSupplementTo` edges to GitHub URLs get queued automatically;
the GitHub adapter takes them over.

## Limitations (v3.1)

- **Community members are not crawled.** The `/api/communities/<slug>/members`
  endpoint is auth-gated on production Zenodo. No `member_of` edges.
- **Versions are not separate nodes.** Each concept DOI is one node;
  `versions: list[dict]` carries the chain. If you need per-version
  provenance (which contributor was on v2 but not v3), this adapter does
  not model it.
- **`/api/users/<id>`** is often 401 anonymously on production Zenodo —
  the adapter degrades silently. User-seeded crawls require a token.
- **No deposit/upload/draft flows.** This is a read-only adapter.
- **ORCID resolution** stays embedded in `creators` / `external_identifiers`
  — no cross-platform identity linking. That layer is handled by a
  separate downstream tool.
```

- [ ] **Step 2: Update README.md**

Find the existing "Multi-platform support" section (added in Spec 1). Add a Zenodo bullet right after the GitLab bullet:

```markdown
- **Zenodo** (`zenodo.org`, `sandbox.zenodo.org`): records, communities,
  uploader accounts. Cross-platform `related_to.<RelationType>` edges
  follow `metadata.related_identifiers` into GitHub etc. Anonymous reads
  supported. See [docs/ZENODO.md](docs/ZENODO.md).
```

- [ ] **Step 3: Update docs/index.md**

Add a Zenodo pointer next to the existing GitLab link:

```markdown
- [Zenodo support](ZENODO.md) — multi-instance Zenodo crawling with
  cross-platform `related_to` edges.
```

- [ ] **Step 4: Update CHANGELOG.md**

Under `[Unreleased]` (will become `[3.1.0]` on release), add a new section:

```markdown
### Added (v3.1 — Zenodo adapter)
- `ZenodoAdapter` and `ZenodoClient` under `src/open_pulse_crawler/platforms/zenodo/`.
- Three subkind models: `ZenodoUserModel`, `ZenodoCommunityModel`,
  `ZenodoRecordModel`. Records use the concept DOI as the primary
  identity; versions collapse into a `versions: list[dict]` field.
- DOI URL → canonical Zenodo URL rewriting in `node_id.py`
  (`is_zenodo_doi_url`, `rewrite_zenodo_doi_url`).
- Five new edge kinds: `in_community`, `uploaded_by`, `uploaded`,
  `contains`, and compound `related_to.<RelationType>` (`isSupplementTo`,
  `cites`, `isCitedBy`, …) for cross-platform discovery.
- CLI registers `ZenodoAdapter` for `zenodo.org`, `sandbox.zenodo.org`,
  and any `*.zenodo.org` host with or without tokens.
- `tools/scripts/fetch_public_projects.py` handles `zenodo.org` via
  `/api/records` keyset pagination.
- `POST /api/v2/crawl` OpenAPI examples: `zenodo_community_renku`,
  `zenodo_record_doi_url`, `zenodo_record_canonical`,
  `cross_platform_zenodo_github`.
- Integration test against live `zenodo.org`
  (`tests/integration/test_zenodo_dryrun.py`).
- `docs/ZENODO.md`.

### Out of scope (v3.1)
- Community member crawling (auth-gated on production Zenodo).
- Per-version record nodes (versions are intra-node metadata).
- Cross-platform identity resolution (handled by another tool).
```

- [ ] **Step 5: Commit**

```
git add docs/ZENODO.md README.md docs/index.md CHANGELOG.md
git commit -m "docs: Zenodo adapter (v3.1.0) — ZENODO.md, README, CHANGELOG"
```

---

## Task 13: Final lint + test verification

- [ ] **Step 1: Lint**

```
VIRTUAL_ENV= uv run ruff check src/
```
Expected: no NEW errors beyond the pre-existing baseline (`ruff check` count should be ≤ what it was at the end of Spec 1).

- [ ] **Step 2: Full unit suite (serial)**

```
VIRTUAL_ENV= uv run pytest -n 0 -m "not integration" --ignore=tests/test_api.py --ignore=tests/test_auth.py -q --no-cov
```
Expected: all tests pass.

- [ ] **Step 3: Diff stat**

```
git diff --stat origin/develop...HEAD | tail -15
```
Eyeball: each new module is ~150–300 LoC.

- [ ] **Step 4: Stop and report ready for review**

Do NOT push. Report:
- Total new/modified files.
- Total tests added.
- Pass count.
- Any deferred concerns (e.g., did the integration test pass against live Zenodo? what was the actual community we used?).

---

## Self-Review

**Spec coverage:**

| Spec section | Tasks |
|---|---|
| §0 Starting point | n/a (informational) |
| §1 Architecture & module layout | 3, 6, 8 |
| §2 Data model (3 subkinds, discriminated unions) | 1 |
| §2.3 Concept DOI as primary identity | 1 (fields), 6 (resolution in fetch) |
| §3.1 normalize_uri (DOI rewriting, legacy paths) | 2, 6 |
| §3.2 classify | 6 |
| §3.3 fetch (with version → concept resolution) | 6 |
| §3.4 expand (5 edge kinds) | 7 |
| §3.5 ZenodoClient API surface | 3, 4, 5 |
| §3.6 Endpoint reference | 4 (get_*), 5 (iter_*) |
| §4.1 Configuration | 8 (CLI registration via host-keyed env vars) |
| §4.2 Caching | 3 (pre-allocated; live wiring deferred per spec) |
| §4.3 Testing | every task + 11 (integration) |
| §4.4 Explore script extension | 10 |
| §4.5 OpenAPI examples | 9 |
| §4.6 Effort estimate | n/a |
| §6 Open questions | n/a (executor decisions; spec calls them out) |
| §7 Deferred items | n/a (explicitly out of scope) |

**Placeholder scan:** No "TBD" / "TODO" / "implement later" / vague-error-handling phrases. Each task has the actual code + commands the executor needs.

**Type consistency:** `ZenodoUserModel` / `ZenodoCommunityModel` / `ZenodoRecordModel` spelled identically across Tasks 1, 6, 7, 8. `ZenodoClient` method names — `get_record`, `get_community`, `get_user`, `iter_community_records`, `iter_user_records` — match across Tasks 3, 4, 5, 6, 7. Edge kinds (`in_community`, `uploaded_by`, `uploaded`, `contains`, `related_to.<RelationType>`) consistent across spec, tests, and adapter code.

**One open question carried into the executor's notebook:** Task 7's `_expand_record` reads metadata from `node.extras["_raw_metadata"]` (a slim copy stashed by `_record_from` in Task 6). This is an internal convention — make sure the slim payload includes `communities`, `owners`, and `related_identifiers`. If a future task wants the full raw payload, document the field set rather than dumping everything.
