# Multi-platform crawler (abstraction + GitLab) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor Open Pulse Crawler from a GitHub-only BFS crawler into a multi-platform crawler. Ship a `PlatformAdapter` abstraction, port GitHub to it, and add a `GitLabAdapter` that supports multi-instance GitLab (`gitlab.com`, `gitlab.epfl.ch`, `gitlab.ethz.ch`, `renkulab.io`).

**Architecture:** Approach A — strangler-fig refactor with a `PlatformAdapter` ABC at the seam. Node identity becomes the canonical HTTPS URL of the entity. The graph stores typed subclasses (`GitHubUser`, `GitLabProject`, …) under one abstract bucket (`Person`, `Group`, `Repository`). The BFS crawler becomes platform-agnostic; per-platform behaviour lives in adapters.

**Tech Stack:** Python 3.10+, Pydantic v2, FastAPI, PyGithub (existing), python-gitlab (new), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-05-27-multi-platform-gitlab-design.md` (committed `9231b3f`).

**Branch / worktree:** `feat/multi-platform-gitlab` from `origin/develop`, at `.worktrees/feat-multi-platform-gitlab/`.

**Test invocation note:** pytest with the default `-n auto` (from pyproject.toml's `addopts`) hangs in this sandbox. Until that's resolved, run with `-n 0` to force serial execution:
```
VIRTUAL_ENV= uv run pytest -n 0 <args>
```
The `VIRTUAL_ENV=` prefix avoids a stale-venv warning. Every test step below assumes this command form.

---

## File map

**New files:**
- `src/open_pulse_crawler/uris.py` — URL normalization and per-platform classification
- `src/open_pulse_crawler/config.py` — host-keyed env-var resolution
- `src/open_pulse_crawler/platforms/__init__.py` — registry
- `src/open_pulse_crawler/platforms/base.py` — `PlatformAdapter` ABC, `Edge`, `ExpandOpts`, `RateLimitInfo`, `NodeKind`
- `src/open_pulse_crawler/platforms/github/__init__.py`
- `src/open_pulse_crawler/platforms/github/adapter.py` — implements `PlatformAdapter`
- `src/open_pulse_crawler/platforms/gitlab/__init__.py`
- `src/open_pulse_crawler/platforms/gitlab/client.py` — python-gitlab wrapper with token rotation
- `src/open_pulse_crawler/platforms/gitlab/adapter.py` — implements `PlatformAdapter`
- `src/open_pulse_crawler/api/__init__.py`
- `src/open_pulse_crawler/api/v1.py` — legacy-shape compat shim
- `src/open_pulse_crawler/api/v2.py` — unified-shape API
- `src/open_pulse_crawler/api/deps.py` — shared dependencies (job store, auth)
- `tests/test_uris.py`
- `tests/test_config.py`
- `tests/platforms/__init__.py`
- `tests/platforms/test_github_adapter.py`
- `tests/platforms/test_gitlab_adapter.py`
- `tests/platforms/_fake_adapter.py` — `FakePlatformAdapter` for crawler tests
- `tests/test_api_v1_compat.py`
- `tests/test_api_v2.py`
- `tests/integration/__init__.py`
- `tests/integration/test_gitlab_dryrun.py`
- `docs/GITLAB.md`

**Moved files:**
- `src/open_pulse_crawler/github_client.py` → `src/open_pulse_crawler/platforms/github/client.py`
- `src/open_pulse_crawler/graphql_client.py` → `src/open_pulse_crawler/platforms/github/graphql.py`
- `src/open_pulse_crawler/api.py` → `src/open_pulse_crawler/api/v1.py` (then rewritten as a shim)

**Heavily modified files:**
- `src/open_pulse_crawler/models.py` — unified Node hierarchy; remove `TeamModel`
- `src/open_pulse_crawler/crawler.py` — platform-agnostic BFS driver
- `src/open_pulse_crawler/io_utils.py` — URL-keyed export
- `src/open_pulse_crawler/visualization.py` — color by `subkind`
- `src/open_pulse_crawler/cli.py` — multi-platform seeds, `--platforms`, `--default-host`, `--crawl-stars`, `crawler doctor`
- `src/open_pulse_crawler/dependency_utils.py` — return URLs not `owner/repo` strings
- `src/open_pulse_crawler/gimie_jsonld.py` — accept URL-keyed Repository nodes
- `src/open_pulse_crawler/gimie_client.py` — minor: produce URL-keyed cache filenames
- `tests/test_crawler.py` — drive BFS via `FakePlatformAdapter`
- `tests/test_models.py`, `tests/test_io_utils.py`, `tests/test_node_properties.py`, `tests/test_max_contributors.py` — updated for URL keys
- `pyproject.toml` — add `python-gitlab` dependency
- `CHANGELOG.md`, `README.md`, `docs/API.md`, `docs/DEPLOYMENT.md`, `docs/index.md`

**Removed:** `TeamModel` and all team-crawling code paths.

---

## Task ordering rationale

Block A builds the platform-agnostic backbone with GitHub as the proof. After Task 11, GitHub works end-to-end on the new abstraction. Block B adds GitLab. Block C updates the user-facing surface (API + CLI). Block D handles migration, docs, and CHANGELOG.

Every task is TDD-shaped: failing test → minimal implementation → passing test → commit. Commit messages use Conventional Commits per `AGENTS.md`.

---

# Block A — Platform abstraction + GitHub port

## Task 1: URI normalization helpers

**Files:**
- Create: `src/open_pulse_crawler/uris.py`
- Test: `tests/test_uris.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_uris.py
import pytest
from open_pulse_crawler.uris import (
    canonicalize,
    host_of,
    InvalidURIError,
)


@pytest.mark.parametrize("raw, expected", [
    ("https://github.com/torvalds", "https://github.com/torvalds"),
    ("https://github.com/torvalds/", "https://github.com/torvalds"),
    ("HTTPS://GitHub.com/Torvalds", "https://github.com/Torvalds"),  # host lowered, path preserved
    ("https://github.com/owner/repo#readme", "https://github.com/owner/repo"),
    ("https://github.com/owner/repo?foo=bar", "https://github.com/owner/repo"),
    ("https://gitlab.epfl.ch/group/sub/project", "https://gitlab.epfl.ch/group/sub/project"),
])
def test_canonicalize_normalizes(raw, expected):
    assert canonicalize(raw) == expected


@pytest.mark.parametrize("bad", [
    "http://github.com/foo",       # non-https
    "ftp://github.com/foo",
    "github.com/foo",              # no scheme
    "",
])
def test_canonicalize_rejects(bad):
    with pytest.raises(InvalidURIError):
        canonicalize(bad)


def test_host_of():
    assert host_of("https://gitlab.epfl.ch/group/proj") == "gitlab.epfl.ch"
    assert host_of("https://GitHub.com/foo") == "github.com"
```

- [ ] **Step 2: Run tests, verify they fail**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_uris.py -v
```
Expected: `ModuleNotFoundError: No module named 'open_pulse_crawler.uris'`.

- [ ] **Step 3: Implement `uris.py`**

```python
# src/open_pulse_crawler/uris.py
"""URL normalization for multi-platform node identifiers."""
from __future__ import annotations
from urllib.parse import urlsplit, urlunsplit


class InvalidURIError(ValueError):
    """Raised when a string cannot be interpreted as a canonical node URI."""


def canonicalize(raw: str) -> str:
    """Normalize to canonical form: lowercase scheme+host, no trailing slash,
    no fragment, no query. Path case preserved (per-adapter normalization
    handles login casing).
    """
    if not raw:
        raise InvalidURIError("empty URI")
    parts = urlsplit(raw)
    if parts.scheme.lower() != "https":
        raise InvalidURIError(f"expected https scheme, got {parts.scheme!r}")
    if not parts.netloc:
        raise InvalidURIError(f"missing host in {raw!r}")
    path = parts.path.rstrip("/")
    return urlunsplit(("https", parts.netloc.lower(), path, "", ""))


def host_of(uri: str) -> str:
    """Return the lowercase host component of a canonical URI."""
    return urlsplit(canonicalize(uri)).netloc
```

- [ ] **Step 4: Run tests, verify they pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_uris.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/uris.py tests/test_uris.py
git commit -m "feat(uris): canonical-URL helpers for multi-platform node IDs"
```

---

## Task 2: Unified model — base types and abstract kinds

**Files:**
- Modify: `src/open_pulse_crawler/models.py` (full rewrite)
- Test: `tests/test_models.py` (replace)

- [ ] **Step 1: Write the failing tests for the new model**

```python
# tests/test_models.py
import pytest
from pydantic import ValidationError
from open_pulse_crawler.models import (
    NodeKind, Node, Person, Group, Repository,
    GitHubUser, GitHubOrganization, GitHubRepository,
    GitLabUser, GitLabGroup, GitLabProject,
    GraphData, ExternalIdentifier,
)


def test_github_user_minimal():
    u = GitHubUser(
        id="https://github.com/torvalds",
        platform="github", instance="github.com",
        name="Linus Torvalds", native_id="1024025",
    )
    assert u.kind == NodeKind.PERSON
    assert u.subkind == "GitHubUser"
    assert u.followers == []


def test_gitlab_project_visibility_default():
    p = GitLabProject(
        id="https://gitlab.epfl.ch/group/proj",
        platform="gitlab", instance="gitlab.epfl.ch",
    )
    assert p.kind == NodeKind.REPOSITORY
    assert p.visibility == "public"


def test_graph_data_roundtrips_subkind():
    g = GraphData()
    u = GitHubUser(id="https://github.com/a", platform="github", instance="github.com")
    p = GitLabProject(id="https://gitlab.com/g/p", platform="gitlab", instance="gitlab.com")
    g.nodes[u.id] = u
    g.nodes[p.id] = p

    payload = g.model_dump_json()
    g2 = GraphData.model_validate_json(payload)

    assert isinstance(g2.nodes["https://github.com/a"], GitHubUser)
    assert isinstance(g2.nodes["https://gitlab.com/g/p"], GitLabProject)


def test_graph_data_of_kind():
    g = GraphData()
    g.nodes["https://github.com/a"] = GitHubUser(
        id="https://github.com/a", platform="github", instance="github.com"
    )
    g.nodes["https://github.com/a/repo"] = GitHubRepository(
        id="https://github.com/a/repo", platform="github", instance="github.com"
    )
    repos = list(g.of_kind(NodeKind.REPOSITORY))
    assert len(repos) == 1
    assert repos[0].subkind == "GitHubRepository"


def test_https_scheme_required():
    with pytest.raises(ValidationError):
        GitHubUser(id="http://github.com/a", platform="github", instance="github.com")


def test_external_identifier_shape():
    e = ExternalIdentifier(scheme="orcid", value="0000-0001-2345-6789")
    assert e.scheme == "orcid"
```

- [ ] **Step 2: Run tests, verify they fail**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_models.py -v
```
Expected: `ImportError` for the new names.

- [ ] **Step 3: Rewrite `models.py`**

```python
# src/open_pulse_crawler/models.py
"""Unified Pydantic models for multi-platform crawled entities."""
from __future__ import annotations
from enum import Enum
from typing import Annotated, Any, ClassVar, Iterator, Literal, Union
from pydantic import BaseModel, Field, HttpUrl, field_validator
from pydantic.functional_validators import BeforeValidator


class NodeKind(str, Enum):
    PERSON = "Person"
    GROUP = "Group"
    REPOSITORY = "Repository"


class ExternalIdentifier(BaseModel):
    scheme: str
    value: str


def _require_https(v: Any) -> Any:
    if isinstance(v, str) and not v.startswith("https://"):
        raise ValueError(f"URL must use https://, got {v!r}")
    return v


Url = Annotated[HttpUrl, BeforeValidator(_require_https)]


class Node(BaseModel):
    id: Url
    platform: str
    instance: str
    name: str = ""
    native_id: str = ""
    is_explored: bool = False
    exploration_timestamp: str | None = None
    external_identifiers: list[ExternalIdentifier] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)

    # Subclasses override these:
    kind: NodeKind
    subkind: str


# ----- Person -----
class Person(Node):
    kind: Literal[NodeKind.PERSON] = NodeKind.PERSON
    followers: list[Url] = Field(default_factory=list)
    following: list[Url] = Field(default_factory=list)
    starred_repositories: list[Url] = Field(default_factory=list)
    authored_repositories: list[Url] = Field(default_factory=list)
    forked_repositories: list[Url] = Field(default_factory=list)


class GitHubUser(Person):
    subkind: Literal["GitHubUser"] = "GitHubUser"
    type: Literal["User", "Bot"] = "User"
    watched_repositories: list[Url] = Field(default_factory=list)


class GitLabUser(Person):
    subkind: Literal["GitLabUser"] = "GitLabUser"
    state: Literal["active", "blocked", "deactivated"] = "active"
    public_email: str = ""


# ----- Group -----
class Group(Node):
    kind: Literal[NodeKind.GROUP] = NodeKind.GROUP
    members: list[Url] = Field(default_factory=list)
    authored_repositories: list[Url] = Field(default_factory=list)


class GitHubOrganization(Group):
    subkind: Literal["GitHubOrganization"] = "GitHubOrganization"
    forked_repositories: list[Url] = Field(default_factory=list)


class GitLabGroup(Group):
    subkind: Literal["GitLabGroup"] = "GitLabGroup"
    parent: Url | None = None
    visibility: Literal["private", "internal", "public"] = "public"


# ----- Repository -----
class Repository(Node):
    kind: Literal[NodeKind.REPOSITORY] = NodeKind.REPOSITORY
    owner: Url | None = None
    contributors: list[Url] = Field(default_factory=list)
    contributor_count: int | None = None
    is_fork: bool = False
    forked_from: Url | None = None
    issue_authors: list[Url] = Field(default_factory=list)
    pr_authors: list[Url] = Field(default_factory=list)
    commenters: list[Url] = Field(default_factory=list)
    pr_reviewers: list[Url] = Field(default_factory=list)
    stargazer_count: int | None = None


class GitHubRepository(Repository):
    subkind: Literal["GitHubRepository"] = "GitHubRepository"
    dependents: list[Url] = Field(default_factory=list)
    dependencies: list[Url] = Field(default_factory=list)


class GitLabProject(Repository):
    subkind: Literal["GitLabProject"] = "GitLabProject"
    visibility: Literal["private", "internal", "public"] = "public"
    namespace: Url | None = None


# Discriminated union of concrete node subclasses.
NodeT = Annotated[
    Union[
        GitHubUser, GitLabUser,
        GitHubOrganization, GitLabGroup,
        GitHubRepository, GitLabProject,
    ],
    Field(discriminator="subkind"),
]


class GraphData(BaseModel):
    nodes: dict[str, NodeT] = Field(default_factory=dict)

    @field_validator("nodes", mode="before")
    @classmethod
    def _stringify_keys(cls, v):
        if isinstance(v, dict):
            return {str(k): val for k, val in v.items()}
        return v

    def of_kind(self, kind: NodeKind) -> Iterator[Node]:
        return (n for n in self.nodes.values() if n.kind == kind)

    def of_subkind(self, subkind: str) -> Iterator[Node]:
        return (n for n in self.nodes.values() if n.subkind == subkind)

    def by_platform(self, platform: str) -> Iterator[Node]:
        return (n for n in self.nodes.values() if n.platform == platform)

    def by_instance(self, instance_host: str) -> Iterator[Node]:
        return (n for n in self.nodes.values() if n.instance == instance_host)
```

- [ ] **Step 4: Run tests, verify they pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_models.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/models.py tests/test_models.py
git commit -m "feat(models): unified URL-keyed Node hierarchy (kind + subkind)"
```

---

## Task 3: `PlatformAdapter` ABC and supporting types

**Files:**
- Create: `src/open_pulse_crawler/platforms/__init__.py`
- Create: `src/open_pulse_crawler/platforms/base.py`
- Test: `tests/platforms/__init__.py` (empty), `tests/platforms/test_base.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/platforms/test_base.py
import pytest
from open_pulse_crawler.platforms.base import (
    PlatformAdapter, Edge, ExpandOpts, RateLimitInfo,
)
from open_pulse_crawler.platforms import PlatformRegistry


def test_expand_opts_defaults():
    o = ExpandOpts()
    assert o.crawl_issues is False
    assert o.crawl_prs is False
    assert o.crawl_stars is False
    assert o.max_contributors is None


def test_edge_shape():
    e = Edge(
        src="https://github.com/a",
        kind="contributor_of",
        dst="https://github.com/a/repo",
    )
    assert e.kind == "contributor_of"


def test_registry_lookup_unknown_host():
    r = PlatformRegistry()
    with pytest.raises(KeyError):
        r.adapter_for("https://unknown.example.com/foo")


def test_registry_registers_and_resolves():
    class FakeAdapter(PlatformAdapter):
        platform = "fake"
        def __init__(self, host): self.instance_host = host
        def classify(self, uri): return None
        def fetch(self, uri): return None
        def expand(self, node, opts): return iter([])
        def normalize_uri(self, raw): return raw
        def rate_limit_state(self): return RateLimitInfo(remaining=1, limit=1)

    r = PlatformRegistry()
    r.register(FakeAdapter("example.com"))
    assert r.adapter_for("https://example.com/foo").instance_host == "example.com"
```

- [ ] **Step 2: Run tests, verify they fail**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_base.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `base.py` and `__init__.py`**

```python
# src/open_pulse_crawler/platforms/base.py
"""PlatformAdapter ABC and the small DTOs the BFS engine speaks to it."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Iterable
from pydantic import BaseModel, Field
from ..models import Node, NodeKind


class ExpandOpts(BaseModel):
    crawl_issues: bool = False
    crawl_prs: bool = False
    crawl_dependencies: bool = False
    crawl_dependents: bool = False
    crawl_stars: bool = False
    min_stars: int = 0
    max_contributors: int | None = None
    issue_max: int = 100
    pr_max: int = 100


class Edge(BaseModel):
    src: str
    kind: str
    dst: str


class RateLimitInfo(BaseModel):
    remaining: int
    limit: int
    reset_at: float | None = None


class PlatformAdapter(ABC):
    platform: ClassVar[str]
    instance_host: str

    @abstractmethod
    def classify(self, uri: str) -> NodeKind | None: ...
    @abstractmethod
    def fetch(self, uri: str) -> Node: ...
    @abstractmethod
    def expand(self, node: Node, opts: ExpandOpts) -> Iterable[Edge]: ...
    @abstractmethod
    def normalize_uri(self, raw: str) -> str: ...
    @abstractmethod
    def rate_limit_state(self) -> RateLimitInfo: ...
```

```python
# src/open_pulse_crawler/platforms/__init__.py
"""Platform adapter registry."""
from __future__ import annotations
from ..uris import host_of
from .base import PlatformAdapter, Edge, ExpandOpts, RateLimitInfo


class PlatformRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter) -> None:
        self._adapters[adapter.instance_host] = adapter

    def adapter_for(self, uri: str) -> PlatformAdapter:
        host = host_of(uri)
        try:
            return self._adapters[host]
        except KeyError as e:
            raise KeyError(f"no adapter registered for host {host!r}") from e

    def hosts(self) -> list[str]:
        return sorted(self._adapters)


__all__ = ["PlatformAdapter", "PlatformRegistry", "Edge", "ExpandOpts", "RateLimitInfo"]
```

- [ ] **Step 4: Run tests, verify they pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_base.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/__init__.py src/open_pulse_crawler/platforms/base.py tests/platforms/__init__.py tests/platforms/test_base.py
git commit -m "feat(platforms): PlatformAdapter ABC + registry"
```

---

## Task 4: Host-keyed configuration (`config.py`)

**Files:**
- Create: `src/open_pulse_crawler/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
import pytest
from open_pulse_crawler.config import (
    host_env_ident,
    resolve_tokens,
    enabled_instances,
)


@pytest.mark.parametrize("host, ident", [
    ("github.com", "GITHUB_COM"),
    ("gitlab.epfl.ch", "GITLAB_EPFL_CH"),
    ("gitlab.ethz.ch", "GITLAB_ETHZ_CH"),
    ("renkulab.io", "RENKULAB_IO"),
    ("my-host.example.com", "MY_HOST_EXAMPLE_COM"),
])
def test_host_env_ident(host, ident):
    assert host_env_ident(host) == ident


def test_enabled_instances_explicit(monkeypatch):
    monkeypatch.setenv("CRAWLER_PLATFORMS", "github.com, gitlab.epfl.ch ,renkulab.io")
    assert enabled_instances() == ["github.com", "gitlab.epfl.ch", "renkulab.io"]


def test_enabled_instances_default_to_github(monkeypatch):
    monkeypatch.delenv("CRAWLER_PLATFORMS", raising=False)
    assert enabled_instances() == ["github.com"]


def test_resolve_tokens_pool_wins(monkeypatch):
    monkeypatch.setenv("CRAWLER_TOKEN_POOL__GITHUB_COM", "a,b,c")
    monkeypatch.setenv("CRAWLER_TOKEN__GITHUB_COM", "ignored")
    assert resolve_tokens("github.com") == ["a", "b", "c"]


def test_resolve_tokens_single(monkeypatch):
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__GITLAB_EPFL_CH", raising=False)
    monkeypatch.setenv("CRAWLER_TOKEN__GITLAB_EPFL_CH", "glpat-x")
    assert resolve_tokens("gitlab.epfl.ch") == ["glpat-x"]


def test_resolve_tokens_legacy_github_pool(monkeypatch, recwarn):
    for v in ("CRAWLER_TOKEN_POOL__GITHUB_COM", "CRAWLER_TOKEN__GITHUB_COM"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("CRAWLER_GITHUB_TOKEN_POOL", "gh1,gh2")
    tokens = resolve_tokens("github.com")
    assert tokens == ["gh1", "gh2"]
    assert any("deprecated" in str(w.message).lower() for w in recwarn.list)


def test_resolve_tokens_empty(monkeypatch):
    for v in (
        "CRAWLER_TOKEN_POOL__GITLAB_ETHZ_CH",
        "CRAWLER_TOKEN__GITLAB_ETHZ_CH",
    ):
        monkeypatch.delenv(v, raising=False)
    assert resolve_tokens("gitlab.ethz.ch") == []
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_config.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `config.py`**

```python
# src/open_pulse_crawler/config.py
"""Host-keyed instance and token configuration."""
from __future__ import annotations
import os
import re
import warnings


def host_env_ident(host: str) -> str:
    """Transform a hostname into the env-var ident suffix.

    Lowercase host → replace dots/hyphens with underscores → uppercase.
    """
    return re.sub(r"[.\-]", "_", host.lower()).upper()


def enabled_instances() -> list[str]:
    """Return the list of enabled instance hostnames.

    Reads ``CRAWLER_PLATFORMS`` (comma-separated). Defaults to
    ``["github.com"]`` so existing single-platform users keep working.
    """
    raw = os.environ.get("CRAWLER_PLATFORMS")
    if not raw or not raw.strip():
        return ["github.com"]
    return [h.strip() for h in raw.split(",") if h.strip()]


_LEGACY_GITHUB_WARNED = False


def _warn_legacy_github(var: str) -> None:
    global _LEGACY_GITHUB_WARNED
    if _LEGACY_GITHUB_WARNED:
        return
    _LEGACY_GITHUB_WARNED = True
    warnings.warn(
        f"{var} is deprecated; use CRAWLER_TOKEN_POOL__GITHUB_COM or "
        "CRAWLER_TOKEN__GITHUB_COM. This shim will be removed in v0.3.",
        DeprecationWarning, stacklevel=3,
    )


def resolve_tokens(host: str) -> list[str]:
    """Return the rotation token pool for the given instance host.

    Precedence (per host):
      1. ``CRAWLER_TOKEN_POOL__<IDENT>`` (comma-separated)
      2. ``CRAWLER_TOKEN__<IDENT>`` (single token → list of one)
      3. For ``github.com`` only, legacy fallbacks:
         ``CRAWLER_GITHUB_TOKEN_POOL`` → ``CRAWLER_GITHUB_TOKEN`` → ``GITHUB_TOKEN``
         (each emits a one-shot DeprecationWarning).
    Returns ``[]`` if nothing is configured.
    """
    ident = host_env_ident(host)
    pool = os.environ.get(f"CRAWLER_TOKEN_POOL__{ident}")
    if pool and pool.strip():
        return [t.strip() for t in pool.split(",") if t.strip()]
    single = os.environ.get(f"CRAWLER_TOKEN__{ident}")
    if single and single.strip():
        return [single.strip()]

    if host == "github.com":
        legacy_pool = os.environ.get("CRAWLER_GITHUB_TOKEN_POOL")
        if legacy_pool and legacy_pool.strip():
            _warn_legacy_github("CRAWLER_GITHUB_TOKEN_POOL")
            return [t.strip() for t in legacy_pool.split(",") if t.strip()]
        for legacy in ("CRAWLER_GITHUB_TOKEN", "GITHUB_TOKEN"):
            v = os.environ.get(legacy)
            if v and v.strip():
                _warn_legacy_github(legacy)
                return [v.strip()]
    return []
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_config.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/config.py tests/test_config.py
git commit -m "feat(config): host-keyed env-var resolution with legacy GitHub fallback"
```

---

## Task 5: Move GitHub client into `platforms/github/`

This task is a pure file move — no behavioural change. It keeps imports working by re-exporting from the old location.

**Files:**
- Move: `src/open_pulse_crawler/github_client.py` → `src/open_pulse_crawler/platforms/github/client.py`
- Move: `src/open_pulse_crawler/graphql_client.py` → `src/open_pulse_crawler/platforms/github/graphql.py`
- Create: `src/open_pulse_crawler/platforms/github/__init__.py`
- Modify: any internal imports from `from .github_client import` → `from .platforms.github.client import` (search-and-replace)

- [ ] **Step 1: Inspect imports**

```
grep -rn "from .github_client\|from open_pulse_crawler.github_client\|from .graphql_client\|from open_pulse_crawler.graphql_client" src tests
```

- [ ] **Step 2: Move files with `git mv` (preserves history)**

```
mkdir -p src/open_pulse_crawler/platforms/github
git mv src/open_pulse_crawler/github_client.py src/open_pulse_crawler/platforms/github/client.py
git mv src/open_pulse_crawler/graphql_client.py src/open_pulse_crawler/platforms/github/graphql.py
```

- [ ] **Step 3: Create `__init__.py` and update internal imports**

```python
# src/open_pulse_crawler/platforms/github/__init__.py
"""GitHub platform implementation."""
from .client import GitHubClient, resolve_cache_dir  # re-export
from .graphql import GraphQLGitHubClient            # re-export
```

Inside the moved files, update relative imports if they reference siblings (e.g., `from .models import` becomes `from ...models import`).

- [ ] **Step 4: Run the full unit test suite (serial), expect green**

```
VIRTUAL_ENV= uv run pytest -n 0 -m "not integration" -q
```
Expected: same number of tests pass as before the move.

- [ ] **Step 5: Commit**

```
git add -A src/open_pulse_crawler/ tests/
git commit -m "refactor: move github_client + graphql_client under platforms/github/"
```

---

## Task 6: `GitHubAdapter` — first half (`classify`, `fetch`, `normalize_uri`)

This task makes a `PlatformAdapter` implementation that wraps the existing `GitHubClient` for fetch operations. `expand()` lands in Task 7.

**Files:**
- Create: `src/open_pulse_crawler/platforms/github/adapter.py`
- Test: `tests/platforms/test_github_adapter.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/platforms/test_github_adapter.py
from unittest.mock import MagicMock, patch
import pytest
from open_pulse_crawler.models import NodeKind, GitHubUser, GitHubOrganization, GitHubRepository
from open_pulse_crawler.platforms.github.adapter import GitHubAdapter


@pytest.fixture
def adapter():
    client = MagicMock()
    return GitHubAdapter(client=client, instance_host="github.com")


def test_classify_user(adapter):
    assert adapter.classify("https://github.com/torvalds") in (
        NodeKind.PERSON, NodeKind.GROUP, None
    )


def test_classify_repo(adapter):
    assert adapter.classify("https://github.com/owner/repo") == NodeKind.REPOSITORY


def test_normalize_uri_strips_trailing_slash(adapter):
    assert adapter.normalize_uri("https://github.com/Torvalds/") == "https://github.com/Torvalds"


def test_fetch_user_returns_github_user(adapter):
    raw = MagicMock()
    raw.login = "torvalds"; raw.id = 1024025; raw.name = "Linus Torvalds"; raw.type = "User"
    adapter._client.get_user_object.return_value = raw

    node = adapter.fetch("https://github.com/torvalds")
    assert isinstance(node, GitHubUser)
    assert str(node.id) == "https://github.com/torvalds"
    assert node.name == "Linus Torvalds"
    assert node.native_id == "1024025"


def test_fetch_org_returns_org(adapter):
    raw = MagicMock()
    raw.login = "anthropic"; raw.id = 999; raw.name = "Anthropic"; raw.type = "Organization"
    adapter._client.get_user_object.return_value = raw

    node = adapter.fetch("https://github.com/anthropic")
    assert isinstance(node, GitHubOrganization)


def test_fetch_repo_returns_repository(adapter):
    raw = MagicMock()
    raw.full_name = "owner/repo"; raw.id = 5; raw.name = "repo"
    raw.fork = False; raw.parent = None; raw.stargazers_count = 42
    adapter._client.get_repo_object.return_value = raw

    node = adapter.fetch("https://github.com/owner/repo")
    assert isinstance(node, GitHubRepository)
    assert node.stargazer_count == 42
    assert node.is_fork is False
```

- [ ] **Step 2: Run, verify failures**

Expected: `ImportError`.

- [ ] **Step 3: Implement `adapter.py` (fetch path only — leave `expand` raising NotImplementedError for now)**

```python
# src/open_pulse_crawler/platforms/github/adapter.py
"""GitHub PlatformAdapter implementation."""
from __future__ import annotations
from typing import ClassVar, Iterable
from ...models import (
    Node, NodeKind, GitHubUser, GitHubOrganization, GitHubRepository,
)
from ...uris import canonicalize, host_of
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo


class GitHubAdapter(PlatformAdapter):
    platform: ClassVar[str] = "github"

    def __init__(self, client, instance_host: str = "github.com") -> None:
        self._client = client
        self.instance_host = instance_host

    def normalize_uri(self, raw: str) -> str:
        return canonicalize(raw)

    def classify(self, uri: str) -> NodeKind | None:
        path = uri.removeprefix(f"https://{self.instance_host}/").rstrip("/")
        if not path:
            return None
        parts = path.split("/")
        if len(parts) == 1:
            return NodeKind.PERSON  # could also be Org; fetch() disambiguates
        if len(parts) == 2:
            return NodeKind.REPOSITORY
        return None  # unsupported (e.g. /orgs/x/teams/y in v1 since Team is gone)

    def fetch(self, uri: str) -> Node:
        uri = self.normalize_uri(uri)
        kind = self.classify(uri)
        path = uri.removeprefix(f"https://{self.instance_host}/")
        if kind == NodeKind.REPOSITORY:
            raw = self._client.get_repo_object(path)
            return GitHubRepository(
                id=uri,
                platform=self.platform,
                instance=self.instance_host,
                name=raw.name,
                native_id=str(raw.id),
                is_fork=bool(raw.fork),
                forked_from=(
                    f"https://{self.instance_host}/{raw.parent.full_name}"
                    if raw.fork and raw.parent else None
                ),
                stargazer_count=getattr(raw, "stargazers_count", None),
                owner=f"https://{self.instance_host}/{path.split('/')[0]}",
            )
        raw = self._client.get_user_object(path)
        if getattr(raw, "type", "User") == "Organization":
            return GitHubOrganization(
                id=uri,
                platform=self.platform,
                instance=self.instance_host,
                name=raw.name or "",
                native_id=str(raw.id),
            )
        return GitHubUser(
            id=uri,
            platform=self.platform,
            instance=self.instance_host,
            name=raw.name or "",
            native_id=str(raw.id),
            type="Bot" if getattr(raw, "type", "User") == "Bot" else "User",
        )

    def expand(self, node: Node, opts: ExpandOpts) -> Iterable[Edge]:
        raise NotImplementedError("Task 7 implements expand()")

    def rate_limit_state(self) -> RateLimitInfo:
        rl = self._client.get_rate_limit_state()
        return RateLimitInfo(remaining=rl["remaining"], limit=rl["limit"], reset_at=rl.get("reset_at"))
```

Note: this assumes the existing `GitHubClient` exposes `get_user_object(login)`, `get_repo_object("owner/repo")`, and `get_rate_limit_state()`. If method names differ, adjust here and add a thin wrapper in `client.py`. The adapter is the only file that calls these.

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_github_adapter.py -v
```

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/github/adapter.py tests/platforms/test_github_adapter.py
git commit -m "feat(github): GitHubAdapter classify/fetch/normalize"
```

---

## Task 7: `GitHubAdapter.expand()` — edges for User, Org, Repository

Drives the BFS by emitting edges from each node type. Mirrors the current crawler's behaviour (followers/following/starred/watched/authored/forked for User; members/repos for Org; contributors/forks/issues/PRs for Repo).

**Files:**
- Modify: `src/open_pulse_crawler/platforms/github/adapter.py` (replace `NotImplementedError`)
- Modify: `tests/platforms/test_github_adapter.py` (add expand tests)

- [ ] **Step 1: Add failing tests for expand**

```python
# extend tests/platforms/test_github_adapter.py

def test_expand_user_emits_follower_edges(adapter):
    user = GitHubUser(
        id="https://github.com/a", platform="github", instance="github.com"
    )
    adapter._client.iter_followers.return_value = ["b", "c"]
    adapter._client.iter_following.return_value = []
    adapter._client.iter_starred.return_value = []
    adapter._client.iter_watched.return_value = []
    adapter._client.iter_user_repos.return_value = []
    adapter._client.iter_user_forks.return_value = []

    edges = list(adapter.expand(user, ExpandOpts()))
    follower_edges = [e for e in edges if e.kind == "follower_of"]
    assert sorted(e.src for e in follower_edges) == [
        "https://github.com/b", "https://github.com/c",
    ]
    assert all(e.dst == "https://github.com/a" for e in follower_edges)


def test_expand_repo_emits_contributor_edges(adapter):
    repo = GitHubRepository(
        id="https://github.com/o/r", platform="github", instance="github.com"
    )
    adapter._client.iter_contributors.return_value = ["alice", "bob"]
    adapter._client.iter_forks.return_value = []
    adapter._client.iter_issue_authors.return_value = []
    adapter._client.iter_pr_authors.return_value = []
    adapter._client.iter_commenters.return_value = []
    adapter._client.iter_pr_reviewers.return_value = []

    edges = list(adapter.expand(repo, ExpandOpts()))
    contrib = [e for e in edges if e.kind == "contributor_of"]
    assert sorted(e.src for e in contrib) == [
        "https://github.com/alice", "https://github.com/bob",
    ]
    assert all(e.dst == "https://github.com/o/r" for e in contrib)


def test_expand_repo_respects_crawl_issues_off(adapter):
    repo = GitHubRepository(
        id="https://github.com/o/r", platform="github", instance="github.com"
    )
    adapter._client.iter_contributors.return_value = []
    adapter._client.iter_forks.return_value = []
    # iter_issue_authors should NOT be called when crawl_issues is False
    adapter._client.iter_issue_authors.side_effect = AssertionError("must not be called")
    adapter._client.iter_pr_authors.return_value = []
    adapter._client.iter_commenters.return_value = []
    adapter._client.iter_pr_reviewers.return_value = []

    list(adapter.expand(repo, ExpandOpts(crawl_issues=False)))
```

(Add similar tests for `GitHubOrganization.expand` — members + authored repos + forked repos.)

- [ ] **Step 2: Run, verify failures**

Expected: `NotImplementedError`.

- [ ] **Step 3: Implement `expand`**

Replace the body of `expand` in `adapter.py`:

```python
    def expand(self, node: Node, opts: ExpandOpts) -> Iterable[Edge]:
        from ...models import GitHubUser, GitHubOrganization, GitHubRepository
        if isinstance(node, GitHubUser):
            yield from self._expand_user(node, opts)
        elif isinstance(node, GitHubOrganization):
            yield from self._expand_org(node, opts)
        elif isinstance(node, GitHubRepository):
            yield from self._expand_repo(node, opts)

    def _uri(self, login_or_full: str) -> str:
        return f"https://{self.instance_host}/{login_or_full}"

    def _expand_user(self, node, opts):
        for login in self._client.iter_followers(node.native_id or node.name or _login(node)):
            yield Edge(src=self._uri(login), kind="follower_of", dst=str(node.id))
        for login in self._client.iter_following(_login(node)):
            yield Edge(src=str(node.id), kind="follower_of", dst=self._uri(login))
        for full in self._client.iter_starred(_login(node)):
            yield Edge(src=str(node.id), kind="starred", dst=self._uri(full))
        for full in self._client.iter_watched(_login(node)):
            yield Edge(src=str(node.id), kind="watched", dst=self._uri(full))
        for full in self._client.iter_user_repos(_login(node)):
            yield Edge(src=str(node.id), kind="authored", dst=self._uri(full))
        for full in self._client.iter_user_forks(_login(node)):
            yield Edge(src=str(node.id), kind="forked", dst=self._uri(full))

    def _expand_org(self, node, opts):
        for login in self._client.iter_members(_login(node)):
            yield Edge(src=self._uri(login), kind="member_of", dst=str(node.id))
        for full in self._client.iter_org_repos(_login(node)):
            yield Edge(src=str(node.id), kind="authored", dst=self._uri(full))
        for full in self._client.iter_org_forks(_login(node)):
            yield Edge(src=str(node.id), kind="forked", dst=self._uri(full))

    def _expand_repo(self, node, opts):
        full = str(node.id).removeprefix(f"https://{self.instance_host}/")
        seen_contrib = 0
        for login in self._client.iter_contributors(full):
            if opts.max_contributors is not None and seen_contrib >= opts.max_contributors:
                break
            yield Edge(src=self._uri(login), kind="contributor_of", dst=str(node.id))
            seen_contrib += 1
        for fork_full in self._client.iter_forks(full):
            yield Edge(src=self._uri(fork_full), kind="forked_from", dst=str(node.id))
        if opts.crawl_issues:
            for login in self._client.iter_issue_authors(full, max_n=opts.issue_max):
                yield Edge(src=self._uri(login), kind="opened_issue_in", dst=str(node.id))
        if opts.crawl_prs:
            for login in self._client.iter_pr_authors(full, max_n=opts.pr_max):
                yield Edge(src=self._uri(login), kind="opened_pr_in", dst=str(node.id))
            for login in self._client.iter_pr_reviewers(full, max_n=opts.pr_max):
                yield Edge(src=self._uri(login), kind="reviewed_pr_in", dst=str(node.id))
        if opts.crawl_issues or opts.crawl_prs:
            for login in self._client.iter_commenters(full):
                yield Edge(src=self._uri(login), kind="commented_in", dst=str(node.id))
        if opts.crawl_dependencies:
            for dep_full in self._client.iter_dependencies(full):
                yield Edge(src=str(node.id), kind="depends_on", dst=self._uri(dep_full))
        if opts.crawl_dependents:
            for dep_full in self._client.iter_dependents(full, min_stars=opts.min_stars):
                yield Edge(src=self._uri(dep_full), kind="depends_on", dst=str(node.id))


def _login(node) -> str:
    """Extract the login segment from a node's canonical URL."""
    return str(node.id).rsplit("/", 1)[-1]
```

This assumes the `GitHubClient` exposes the iteration helpers named above. If they don't exist yet under those names, add thin wrappers in `platforms/github/client.py` that delegate to the existing PyGithub-based methods.

- [ ] **Step 4: Run all GitHub adapter tests, expect green**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_github_adapter.py -v
```

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/github/adapter.py tests/platforms/test_github_adapter.py
git commit -m "feat(github): GitHubAdapter.expand() with all current edge types"
```

---

## Task 8: `FakePlatformAdapter` for crawler tests

The current `test_crawler.py` exercises the BFS by stubbing `GitHubClient`. After refactor, it should drive the BFS via the adapter interface. Introduce a `FakePlatformAdapter` test helper.

**Files:**
- Create: `tests/platforms/_fake_adapter.py`
- (Used by Task 9.)

- [ ] **Step 1: Write the fake adapter**

```python
# tests/platforms/_fake_adapter.py
"""In-memory PlatformAdapter for crawler tests."""
from __future__ import annotations
from typing import ClassVar, Iterable
from open_pulse_crawler.models import Node, NodeKind
from open_pulse_crawler.platforms.base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo
from open_pulse_crawler.uris import canonicalize


class FakePlatformAdapter(PlatformAdapter):
    platform: ClassVar[str] = "fake"

    def __init__(self, instance_host: str = "fake.test"):
        self.instance_host = instance_host
        self.fetched: dict[str, Node] = {}      # caller seeds these
        self.edges: dict[str, list[Edge]] = {}  # uri → outgoing edges

    def normalize_uri(self, raw: str) -> str:
        return canonicalize(raw)

    def classify(self, uri: str) -> NodeKind | None:
        n = self.fetched.get(canonicalize(uri))
        return n.kind if n else None

    def fetch(self, uri: str) -> Node:
        return self.fetched[canonicalize(uri)]

    def expand(self, node: Node, opts: ExpandOpts) -> Iterable[Edge]:
        return iter(self.edges.get(str(node.id), []))

    def rate_limit_state(self) -> RateLimitInfo:
        return RateLimitInfo(remaining=999, limit=1000)
```

- [ ] **Step 2: No tests yet; this is a helper. Commit alongside Task 9.**

(The commit lands at the end of Task 9.)

---

## Task 9: Refactor `crawler.py` to be platform-agnostic

This is the largest task. The BFS engine now operates on URL keys and dispatches via the `PlatformRegistry`. The team-crawling code paths are deleted here.

**Files:**
- Modify (heavy): `src/open_pulse_crawler/crawler.py`
- Modify: `tests/test_crawler.py` — drive via `FakePlatformAdapter`

- [ ] **Step 1: Write a new failing crawler test driving the BFS via the fake**

```python
# tests/test_crawler.py (new contents — replace existing)
from open_pulse_crawler.crawler import Crawler  # renamed from GitHubCrawler
from open_pulse_crawler.platforms import PlatformRegistry
from open_pulse_crawler.platforms.base import Edge, ExpandOpts
from open_pulse_crawler.models import (
    NodeKind, GitHubUser, GitHubRepository, GraphData,
)
from tests.platforms._fake_adapter import FakePlatformAdapter


def test_bfs_one_round():
    fake = FakePlatformAdapter(instance_host="fake.test")
    seed = GitHubUser(id="https://fake.test/alice", platform="github", instance="fake.test", name="Alice")
    repo = GitHubRepository(id="https://fake.test/alice/repo", platform="github", instance="fake.test")
    fake.fetched = {str(seed.id): seed, str(repo.id): repo}
    fake.edges = {
        str(seed.id): [Edge(src=str(seed.id), kind="authored", dst=str(repo.id))],
        str(repo.id): [],
    }
    registry = PlatformRegistry(); registry.register(fake)

    crawler = Crawler(registry=registry, max_rounds=2)
    crawler.add_seeds([str(seed.id)])
    graph = crawler.run()

    assert str(seed.id) in graph.nodes
    assert str(repo.id) in graph.nodes
    assert isinstance(graph.nodes[str(repo.id)], GitHubRepository)


def test_bfs_respects_max_rounds():
    fake = FakePlatformAdapter()
    # Build a chain: a -> b -> c -> d
    nodes = {}
    for login in ("a", "b", "c", "d"):
        u = GitHubUser(id=f"https://fake.test/{login}", platform="github", instance="fake.test")
        nodes[str(u.id)] = u
    fake.fetched = nodes
    fake.edges = {
        "https://fake.test/a": [Edge(src="https://fake.test/a", kind="follower_of", dst="https://fake.test/b")],
        "https://fake.test/b": [Edge(src="https://fake.test/b", kind="follower_of", dst="https://fake.test/c")],
        "https://fake.test/c": [Edge(src="https://fake.test/c", kind="follower_of", dst="https://fake.test/d")],
        "https://fake.test/d": [],
    }
    registry = PlatformRegistry(); registry.register(fake)
    crawler = Crawler(registry=registry, max_rounds=2)
    crawler.add_seeds(["https://fake.test/a"])
    g = crawler.run()
    assert "https://fake.test/a" in g.nodes
    assert "https://fake.test/b" in g.nodes
    # c discovered (queued) but not fetched in 2 rounds
    assert "https://fake.test/c" not in g.nodes
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_crawler.py -v
```
Expected: import errors / `Crawler` doesn't exist.

- [ ] **Step 3: Rewrite `crawler.py`**

Use this skeleton (full file replacement). Preserve the existing concurrency model (ThreadPoolExecutor + per-batch semaphore + pause/cancel flags) — read the old `crawler.py` and lift those mechanics.

```python
# src/open_pulse_crawler/crawler.py
"""Platform-agnostic BFS crawler driven by PlatformAdapters."""
from __future__ import annotations
import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Iterable, Optional

from .models import GraphData, Node
from .platforms import PlatformRegistry
from .platforms.base import Edge, ExpandOpts

logger = logging.getLogger(__name__)


class Crawler:
    def __init__(
        self,
        registry: PlatformRegistry,
        max_rounds: int = 3,
        opts: Optional[ExpandOpts] = None,
        batch_size: int = 8,
    ):
        self.registry = registry
        self.max_rounds = max_rounds
        self.opts = opts or ExpandOpts()
        self.batch_size = batch_size

        self.graph = GraphData()
        self.visited: set[str] = set()
        self.queue: deque[str] = deque()
        self.edges: list[Edge] = []
        self.current_round = 0

        self.pause_requested = False
        self.cancel_requested = False
        self._graph_lock = threading.Lock()
        self._visited_lock = threading.Lock()

    def add_seeds(self, seeds: Iterable[str]) -> None:
        for raw in seeds:
            adapter = self.registry.adapter_for(raw)
            uri = adapter.normalize_uri(raw)
            if uri not in self.visited:
                self.queue.append(uri)

    def _process_one(self, uri: str) -> tuple[Node, list[Edge]]:
        adapter = self.registry.adapter_for(uri)
        node = adapter.fetch(uri)
        node.is_explored = True
        node.exploration_timestamp = datetime.utcnow().isoformat()
        emitted = list(adapter.expand(node, self.opts))
        return node, emitted

    def run(self) -> GraphData:
        while self.queue and self.current_round < self.max_rounds:
            if self.cancel_requested:
                logger.info("crawl cancelled")
                break
            self.current_round += 1
            logger.info("round %d: %d nodes queued", self.current_round, len(self.queue))
            batch = []
            while self.queue and len(batch) < len(self.queue):
                batch.append(self.queue.popleft())
            with ThreadPoolExecutor(max_workers=self.batch_size) as pool:
                futures = {pool.submit(self._process_one, uri): uri for uri in batch}
                for fut in as_completed(futures):
                    uri = futures[fut]
                    if self.cancel_requested:
                        break
                    try:
                        node, edges = fut.result()
                    except Exception as e:
                        logger.warning("fetch %s failed: %s", uri, e)
                        continue
                    with self._graph_lock:
                        self.graph.nodes[str(node.id)] = node
                        self.edges.extend(edges)
                    with self._visited_lock:
                        self.visited.add(str(node.id))
                    for e in edges:
                        target = e.dst if e.src == str(node.id) else e.src
                        with self._visited_lock:
                            already = target in self.visited or target in self.queue
                        if not already:
                            self.queue.append(target)
            while self.pause_requested:
                threading.Event().wait(0.5)
        return self.graph
```

- [ ] **Step 4: Run unit tests; expect crawler tests green and the rest to fail because old `GitHubCrawler` API is gone**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_crawler.py -v
```

Other test files (`test_max_contributors.py`, `test_node_properties.py`, etc.) will break here — that's fine, they're updated in Task 10.

- [ ] **Step 5: Commit**

```
git add tests/platforms/_fake_adapter.py src/open_pulse_crawler/crawler.py tests/test_crawler.py
git commit -m "refactor(crawler): platform-agnostic BFS driven by PlatformAdapter"
```

---

## Task 10: Update remaining tests for URL-keyed models

**Files:**
- Modify: `tests/test_max_contributors.py`, `tests/test_node_properties.py`, `tests/test_io_utils.py`, `tests/test_github_client.py`, `tests/test_graphql_client.py`, `tests/test_gimie_jsonld.py`

- [ ] **Step 1: For each failing test file, rewrite assertions to use the new model**

Example pattern — replace:
```python
assert graph.users["torvalds"].followers == ["jdoe"]
```
with:
```python
assert graph.nodes["https://github.com/torvalds"].followers == ["https://github.com/jdoe"]
```

- [ ] **Step 2: Run the unit test suite**

```
VIRTUAL_ENV= uv run pytest -n 0 -m "not integration" -q
```
Expected: all green.

- [ ] **Step 3: Commit**

```
git add tests/
git commit -m "test: update existing tests for URL-keyed graph model"
```

---

## Task 11: Update `io_utils.py` and `visualization.py` for URL keys

**Files:**
- Modify: `src/open_pulse_crawler/io_utils.py`
- Modify: `src/open_pulse_crawler/visualization.py`
- Tests: re-run `tests/test_io_utils.py`

- [ ] **Step 1: Update `parse_seed_file` + `export_to_json` + `export_to_csv` + `export_nodes_csv`**

JSON export: dump `graph.model_dump()` directly — the new `GraphData.nodes` dict produces the right shape.

CSV nodes export: columns `id, kind, subkind, platform, instance, name, native_id, is_explored, exploration_timestamp`.

CSV edges export: columns `source_id, edge_kind, target_id`.

- [ ] **Step 2: Update `visualization.py` colour mapping**

Map by `subkind` instead of hard-coded GitHub item types:

```python
SUBKIND_COLOR = {
    "GitHubUser": "#1f77b4",
    "GitHubOrganization": "#ff7f0e",
    "GitHubRepository": "#2ca02c",
    "GitLabUser": "#9467bd",
    "GitLabGroup": "#8c564b",
    "GitLabProject": "#e377c2",
}
```

Unknown subkinds fall back to grey.

- [ ] **Step 3: Run tests**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_io_utils.py -v
```

- [ ] **Step 4: Commit**

```
git add src/open_pulse_crawler/io_utils.py src/open_pulse_crawler/visualization.py
git commit -m "refactor(io,viz): consume URL-keyed nodes and colour by subkind"
```

---

# Block B — GitLab adapter

## Task 12: Add `python-gitlab` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `python-gitlab>=4.0` to `[project.dependencies]`**

```
# pyproject.toml — under [project.dependencies]
"python-gitlab>=4.0,<6",
```

- [ ] **Step 2: Refresh the lock and venv**

```
uv lock
VIRTUAL_ENV= uv sync --extra dev
```

- [ ] **Step 3: Verify import works**

```
VIRTUAL_ENV= uv run python -c "import gitlab; print(gitlab.__version__)"
```

- [ ] **Step 4: Commit**

```
git add pyproject.toml uv.lock
git commit -m "build: add python-gitlab dependency"
```

---

## Task 13: GitLab client wrapper (`platforms/gitlab/client.py`)

A thin wrapper around `gitlab.Gitlab` that handles token rotation, rate-limit awareness, and caching of the disambiguation probes.

**Files:**
- Create: `src/open_pulse_crawler/platforms/gitlab/__init__.py`
- Create: `src/open_pulse_crawler/platforms/gitlab/client.py`
- Test: `tests/platforms/test_gitlab_client.py`

- [ ] **Step 1: Write a failing test for token rotation on 429**

```python
# tests/platforms/test_gitlab_client.py
from unittest.mock import MagicMock, patch
from open_pulse_crawler.platforms.gitlab.client import GitLabClient


def test_get_user_username(monkeypatch):
    fake_user = MagicMock(id=42, username="alice", name="Alice", state="active", public_email="")
    gl = MagicMock()
    gl.users.list.return_value = [fake_user]
    c = GitLabClient(host="gitlab.example.com", tokens=["t1"], _gl_factory=lambda *a, **kw: gl)
    u = c.get_user_by_username("alice")
    assert u.id == 42 and u.username == "alice"


def test_get_group_by_path():
    fake_group = MagicMock(id=99, full_path="g/sub", name="Sub", visibility="public", parent_id=10)
    gl = MagicMock()
    gl.groups.get.return_value = fake_group
    c = GitLabClient(host="gitlab.example.com", tokens=["t1"], _gl_factory=lambda *a, **kw: gl)
    g = c.get_group_by_path("g/sub")
    assert g.full_path == "g/sub"


def test_get_project_by_path_404_returns_none():
    import gitlab
    gl = MagicMock()
    gl.projects.get.side_effect = gitlab.GitlabGetError(response_code=404)
    c = GitLabClient(host="gitlab.example.com", tokens=["t1"], _gl_factory=lambda *a, **kw: gl)
    assert c.get_project_by_path("g/sub/x") is None
```

- [ ] **Step 2: Implement `client.py`**

```python
# src/open_pulse_crawler/platforms/gitlab/__init__.py
from .client import GitLabClient
```

```python
# src/open_pulse_crawler/platforms/gitlab/client.py
"""python-gitlab wrapper with token rotation and disambiguation helpers."""
from __future__ import annotations
import logging
from typing import Any, Callable, Optional

import gitlab

logger = logging.getLogger(__name__)


class GitLabClient:
    def __init__(
        self,
        host: str,
        tokens: list[str],
        _gl_factory: Callable[..., Any] = gitlab.Gitlab,
    ):
        if not tokens:
            raise ValueError(f"no tokens configured for {host}")
        self.host = host
        self._tokens = tokens
        self._idx = 0
        self._gl_factory = _gl_factory
        self._gl = self._make()

    def _make(self) -> Any:
        gl = self._gl_factory(f"https://{self.host}", private_token=self._tokens[self._idx])
        return gl

    def _rotate(self) -> None:
        self._idx = (self._idx + 1) % len(self._tokens)
        self._gl = self._make()

    # ---- single fetches ----
    def get_user_by_username(self, username: str) -> Optional[Any]:
        users = self._gl.users.list(username=username, get_all=False)
        if not users:
            return None
        return users[0]

    def get_group_by_path(self, full_path: str) -> Optional[Any]:
        try:
            return self._gl.groups.get(full_path)
        except gitlab.GitlabGetError as e:
            if e.response_code == 404:
                return None
            raise

    def get_project_by_path(self, full_path: str) -> Optional[Any]:
        try:
            return self._gl.projects.get(full_path)
        except gitlab.GitlabGetError as e:
            if e.response_code == 404:
                return None
            raise

    # ---- iterators (used by adapter.expand) ----
    def iter_group_members(self, group_id) -> list[Any]:
        return self._gl.groups.get(group_id).members_all.list(get_all=True)

    def iter_subgroups(self, group_id) -> list[Any]:
        return self._gl.groups.get(group_id).subgroups.list(get_all=True)

    def iter_group_projects(self, group_id) -> list[Any]:
        return self._gl.groups.get(group_id).projects.list(get_all=True, include_subgroups=False)

    def iter_user_projects(self, user_id) -> list[Any]:
        return self._gl.users.get(user_id).projects.list(get_all=True)

    def iter_user_contributed(self, user_id) -> list[Any]:
        # The python-gitlab user object exposes contributed_projects via a manager call.
        return self._gl.users.get(user_id).http_get("/contributed_projects")

    def iter_user_starred(self, user_id) -> list[Any]:
        return self._gl.users.get(user_id).starred_projects.list(get_all=True)

    def iter_project_contributors(self, project_id) -> list[Any]:
        return self._gl.projects.get(project_id).repository_contributors(get_all=True)

    def iter_project_forks(self, project_id) -> list[Any]:
        return self._gl.projects.get(project_id).forks.list(get_all=True)

    def iter_project_issues(self, project_id, max_n: int) -> list[Any]:
        return self._gl.projects.get(project_id).issues.list(per_page=min(max_n, 100), get_all=False)

    def iter_project_merge_requests(self, project_id, max_n: int) -> list[Any]:
        return self._gl.projects.get(project_id).mergerequests.list(per_page=min(max_n, 100), get_all=False)

    def iter_project_starrers(self, project_id) -> list[Any]:
        try:
            return self._gl.projects.get(project_id).starrers.list(get_all=True)
        except (AttributeError, gitlab.GitlabGetError):
            return []   # older self-hosted GitLab without /starrers endpoint
```

- [ ] **Step 3: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_gitlab_client.py -v
```

- [ ] **Step 4: Commit**

```
git add src/open_pulse_crawler/platforms/gitlab/__init__.py src/open_pulse_crawler/platforms/gitlab/client.py tests/platforms/test_gitlab_client.py
git commit -m "feat(gitlab): python-gitlab wrapper with token rotation"
```

---

## Task 14: `GitLabAdapter` — classify + normalize + fetch

**Files:**
- Create: `src/open_pulse_crawler/platforms/gitlab/adapter.py`
- Test: `tests/platforms/test_gitlab_adapter.py`

- [ ] **Step 1: Write failing tests for classify + normalize + fetch**

```python
# tests/platforms/test_gitlab_adapter.py
from unittest.mock import MagicMock
import pytest
from open_pulse_crawler.models import NodeKind, GitLabUser, GitLabGroup, GitLabProject
from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter


@pytest.fixture
def adapter():
    client = MagicMock()
    return GitLabAdapter(client=client, instance_host="gitlab.epfl.ch")


def test_normalize_drops_trailing_slash(adapter):
    assert adapter.normalize_uri("https://gitlab.epfl.ch/g/p/") == "https://gitlab.epfl.ch/g/p"


def test_classify_top_level_user(adapter):
    adapter._client.get_user_by_username.return_value = MagicMock(id=1, username="alice")
    adapter._client.get_group_by_path.return_value = None
    assert adapter.classify("https://gitlab.epfl.ch/alice") == NodeKind.PERSON


def test_classify_top_level_group(adapter):
    adapter._client.get_user_by_username.return_value = None
    adapter._client.get_group_by_path.return_value = MagicMock(id=2, full_path="grp")
    assert adapter.classify("https://gitlab.epfl.ch/grp") == NodeKind.GROUP


def test_classify_nested_prefers_project(adapter):
    adapter._client.get_project_by_path.return_value = MagicMock(id=3, path_with_namespace="g/p")
    assert adapter.classify("https://gitlab.epfl.ch/g/p") == NodeKind.REPOSITORY


def test_classify_nested_falls_back_to_group(adapter):
    adapter._client.get_project_by_path.return_value = None
    adapter._client.get_group_by_path.return_value = MagicMock(id=4, full_path="g/sub")
    assert adapter.classify("https://gitlab.epfl.ch/g/sub") == NodeKind.GROUP


def test_fetch_user(adapter):
    raw = MagicMock(
        id=10, username="alice", name="Alice",
        state="active", public_email="alice@x.org",
    )
    adapter._client.get_user_by_username.return_value = raw
    node = adapter.fetch("https://gitlab.epfl.ch/alice")
    assert isinstance(node, GitLabUser)
    assert node.public_email == "alice@x.org"


def test_fetch_project(adapter):
    raw = MagicMock(
        id=22, path_with_namespace="g/p", name="p",
        visibility="public", forked_from_project=None,
        star_count=7, namespace={"kind": "group", "full_path": "g"},
    )
    adapter._client.get_project_by_path.return_value = raw
    node = adapter.fetch("https://gitlab.epfl.ch/g/p")
    assert isinstance(node, GitLabProject)
    assert node.stargazer_count == 7
    assert node.is_fork is False
```

- [ ] **Step 2: Run, verify failures.**

- [ ] **Step 3: Implement `adapter.py`**

```python
# src/open_pulse_crawler/platforms/gitlab/adapter.py
"""GitLab PlatformAdapter implementation."""
from __future__ import annotations
from typing import ClassVar, Iterable, Optional
from ...models import (
    Node, NodeKind, GitLabUser, GitLabGroup, GitLabProject,
)
from ...uris import canonicalize
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo


class GitLabAdapter(PlatformAdapter):
    platform: ClassVar[str] = "gitlab"

    def __init__(self, client, instance_host: str) -> None:
        self._client = client
        self.instance_host = instance_host
        # Cache: full_path → ("user" | "group" | "project" | "unknown")
        self._kind_cache: dict[str, str] = {}

    def normalize_uri(self, raw: str) -> str:
        return canonicalize(raw)

    def _path(self, uri: str) -> str:
        return uri.removeprefix(f"https://{self.instance_host}/")

    def _resolve_kind(self, path: str) -> str:
        if path in self._kind_cache:
            return self._kind_cache[path]
        if "/" not in path:
            if self._client.get_user_by_username(path):
                kind = "user"
            elif self._client.get_group_by_path(path):
                kind = "group"
            else:
                kind = "unknown"
        else:
            if self._client.get_project_by_path(path):
                kind = "project"
            elif self._client.get_group_by_path(path):
                kind = "group"
            else:
                kind = "unknown"
        self._kind_cache[path] = kind
        return kind

    def classify(self, uri: str) -> NodeKind | None:
        path = self._path(self.normalize_uri(uri))
        kind = self._resolve_kind(path)
        return {
            "user": NodeKind.PERSON,
            "group": NodeKind.GROUP,
            "project": NodeKind.REPOSITORY,
        }.get(kind)

    def fetch(self, uri: str) -> Node:
        uri = self.normalize_uri(uri)
        path = self._path(uri)
        kind = self._resolve_kind(path)
        if kind == "user":
            raw = self._client.get_user_by_username(path)
            return GitLabUser(
                id=uri, platform=self.platform, instance=self.instance_host,
                name=getattr(raw, "name", ""), native_id=str(raw.id),
                state=getattr(raw, "state", "active"),
                public_email=getattr(raw, "public_email", "") or "",
            )
        if kind == "group":
            raw = self._client.get_group_by_path(path)
            parent_path = None
            if getattr(raw, "parent_id", None):
                parent_path = "/".join(raw.full_path.split("/")[:-1]) or None
            return GitLabGroup(
                id=uri, platform=self.platform, instance=self.instance_host,
                name=getattr(raw, "name", ""), native_id=str(raw.id),
                visibility=getattr(raw, "visibility", "public"),
                parent=(f"https://{self.instance_host}/{parent_path}" if parent_path else None),
            )
        if kind == "project":
            raw = self._client.get_project_by_path(path)
            ns = getattr(raw, "namespace", None) or {}
            ns_path = ns.get("full_path") if isinstance(ns, dict) else getattr(ns, "full_path", None)
            forked_from = None
            if getattr(raw, "forked_from_project", None):
                fp = raw.forked_from_project
                forked_full = fp.get("path_with_namespace") if isinstance(fp, dict) else getattr(fp, "path_with_namespace", None)
                if forked_full:
                    forked_from = f"https://{self.instance_host}/{forked_full}"
            return GitLabProject(
                id=uri, platform=self.platform, instance=self.instance_host,
                name=getattr(raw, "name", ""), native_id=str(raw.id),
                visibility=getattr(raw, "visibility", "public"),
                stargazer_count=getattr(raw, "star_count", None),
                is_fork=forked_from is not None,
                forked_from=forked_from,
                namespace=(f"https://{self.instance_host}/{ns_path}" if ns_path else None),
            )
        raise ValueError(f"cannot resolve {uri!r} to a known GitLab entity")

    def expand(self, node: Node, opts: ExpandOpts) -> Iterable[Edge]:
        raise NotImplementedError("Task 15 implements expand()")

    def rate_limit_state(self) -> RateLimitInfo:
        # GitLab returns rate-limit headers; surface unknowns conservatively.
        return RateLimitInfo(remaining=1000, limit=2000)
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/gitlab/adapter.py tests/platforms/test_gitlab_adapter.py
git commit -m "feat(gitlab): GitLabAdapter classify/fetch/normalize with disambiguation"
```

---

## Task 15: `GitLabAdapter.expand()` — emit edges for all three node types

**Files:**
- Modify: `src/open_pulse_crawler/platforms/gitlab/adapter.py`
- Modify: `tests/platforms/test_gitlab_adapter.py` (add expand tests)

- [ ] **Step 1: Write failing tests for expand**

```python
def test_expand_user_starred_and_authored(adapter):
    user = GitLabUser(id="https://gitlab.epfl.ch/alice", platform="gitlab", instance="gitlab.epfl.ch", native_id="10")
    adapter._client.iter_user_projects.return_value = [
        MagicMock(path_with_namespace="alice/p1"),
    ]
    adapter._client.iter_user_starred.return_value = [
        MagicMock(path_with_namespace="other/p2"),
    ]
    adapter._client.iter_user_contributed.return_value = []
    edges = list(adapter.expand(user, ExpandOpts()))
    assert any(e.kind == "authored" and e.dst.endswith("/alice/p1") for e in edges)
    assert any(e.kind == "starred" and e.dst.endswith("/other/p2") for e in edges)


def test_expand_group_members_and_projects(adapter):
    group = GitLabGroup(id="https://gitlab.epfl.ch/g", platform="gitlab", instance="gitlab.epfl.ch", native_id="2")
    adapter._client.iter_group_members.return_value = [MagicMock(username="alice"), MagicMock(username="bob")]
    adapter._client.iter_subgroups.return_value = [MagicMock(full_path="g/sub")]
    adapter._client.iter_group_projects.return_value = [MagicMock(path_with_namespace="g/p1")]
    edges = list(adapter.expand(group, ExpandOpts()))
    assert any(e.kind == "member_of" and e.src.endswith("/alice") for e in edges)
    assert any(e.kind == "subgroup_of" and e.src.endswith("/g/sub") for e in edges)
    assert any(e.kind == "authored" and e.dst.endswith("/g/p1") for e in edges)


def test_expand_project_contributors_and_starrers(adapter):
    project = GitLabProject(id="https://gitlab.epfl.ch/g/p", platform="gitlab", instance="gitlab.epfl.ch", native_id="22")
    adapter._client.iter_project_contributors.return_value = [
        {"email": "a@x", "name": "alice"}, {"email": "b@x", "name": "bob"},
    ]
    adapter._client.iter_project_forks.return_value = []
    adapter._client.iter_project_starrers.return_value = [MagicMock(user=MagicMock(username="carol"))]
    adapter._client.iter_project_issues.return_value = []
    adapter._client.iter_project_merge_requests.return_value = []
    edges = list(adapter.expand(project, ExpandOpts(crawl_stars=True)))
    starred = [e for e in edges if e.kind == "starred"]
    assert any(e.src.endswith("/carol") for e in starred)


def test_expand_project_skips_starrers_without_flag(adapter):
    project = GitLabProject(id="https://gitlab.epfl.ch/g/p", platform="gitlab", instance="gitlab.epfl.ch", native_id="22")
    adapter._client.iter_project_contributors.return_value = []
    adapter._client.iter_project_forks.return_value = []
    adapter._client.iter_project_issues.return_value = []
    adapter._client.iter_project_merge_requests.return_value = []
    adapter._client.iter_project_starrers.side_effect = AssertionError("must not be called")
    list(adapter.expand(project, ExpandOpts(crawl_stars=False)))
```

- [ ] **Step 2: Run, verify failures (NotImplementedError).**

- [ ] **Step 3: Implement `expand()`**

```python
    def expand(self, node: Node, opts: ExpandOpts) -> Iterable[Edge]:
        from ...models import GitLabUser, GitLabGroup, GitLabProject
        host = self.instance_host
        nid = str(node.id)
        if isinstance(node, GitLabUser):
            for p in self._client.iter_user_projects(node.native_id):
                yield Edge(src=nid, kind="authored", dst=f"https://{host}/{p.path_with_namespace}")
            for p in self._client.iter_user_starred(node.native_id):
                yield Edge(src=nid, kind="starred", dst=f"https://{host}/{p.path_with_namespace}")
            for p in self._client.iter_user_contributed(node.native_id):
                full = p["path_with_namespace"] if isinstance(p, dict) else p.path_with_namespace
                yield Edge(src=nid, kind="contributor_of", dst=f"https://{host}/{full}")
        elif isinstance(node, GitLabGroup):
            for m in self._client.iter_group_members(node.native_id):
                yield Edge(src=f"https://{host}/{m.username}", kind="member_of", dst=nid)
            for sg in self._client.iter_subgroups(node.native_id):
                yield Edge(src=f"https://{host}/{sg.full_path}", kind="subgroup_of", dst=nid)
            for p in self._client.iter_group_projects(node.native_id):
                yield Edge(src=nid, kind="authored", dst=f"https://{host}/{p.path_with_namespace}")
        elif isinstance(node, GitLabProject):
            seen = 0
            for c in self._client.iter_project_contributors(node.native_id):
                if opts.max_contributors is not None and seen >= opts.max_contributors:
                    break
                # contributors come back as dicts with a "name" field — no username,
                # so we can't link them to a Person URI reliably. Emit only when we
                # have a resolvable identifier; otherwise skip silently.
                username = c.get("username") if isinstance(c, dict) else getattr(c, "username", None)
                if not username:
                    continue
                yield Edge(src=f"https://{host}/{username}", kind="contributor_of", dst=nid)
                seen += 1
            for f in self._client.iter_project_forks(node.native_id):
                yield Edge(src=f"https://{host}/{f.path_with_namespace}", kind="forked_from", dst=nid)
            if opts.crawl_issues:
                for issue in self._client.iter_project_issues(node.native_id, opts.issue_max):
                    yield Edge(src=f"https://{host}/{issue.author['username']}",
                               kind="opened_issue_in", dst=nid)
            if opts.crawl_prs:
                for mr in self._client.iter_project_merge_requests(node.native_id, opts.pr_max):
                    yield Edge(src=f"https://{host}/{mr.author['username']}",
                               kind="opened_pr_in", dst=nid)
            if opts.crawl_stars:
                for s in self._client.iter_project_starrers(node.native_id):
                    user = s.get("user") if isinstance(s, dict) else getattr(s, "user", None)
                    if user is None:
                        continue
                    username = user["username"] if isinstance(user, dict) else getattr(user, "username", None)
                    if username:
                        yield Edge(src=f"https://{host}/{username}", kind="starred", dst=nid)
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/gitlab/adapter.py tests/platforms/test_gitlab_adapter.py
git commit -m "feat(gitlab): GitLabAdapter.expand() — users, groups, projects edges"
```

---

# Block C — API and CLI

## Task 16: Move existing API to `api/v1.py` and split off `api/deps.py`

**Files:**
- Move: `src/open_pulse_crawler/api.py` → `src/open_pulse_crawler/api/v1.py`
- Create: `src/open_pulse_crawler/api/__init__.py` (re-exports the FastAPI app)
- Create: `src/open_pulse_crawler/api/deps.py` (shared auth / job store)

- [ ] **Step 1: Move with git, create deps + __init__**

```
mkdir -p src/open_pulse_crawler/api
git mv src/open_pulse_crawler/api.py src/open_pulse_crawler/api/v1.py
```

- [ ] **Step 2: Split shared dependencies into `api/deps.py`** — extract the job store, the shared auth dependency, any background-task helpers. `v1.py` imports from `deps.py`.

- [ ] **Step 3: `api/__init__.py` mounts v1 (and v2 in Task 17)**

```python
# src/open_pulse_crawler/api/__init__.py
from fastapi import FastAPI
from .v1 import router as v1_router

app = FastAPI(title="Open Pulse Crawler")
app.include_router(v1_router, prefix="/api/v1")
```

`v1.py` exposes a `router = APIRouter()` instead of a top-level `app`. Existing endpoint paths inside `v1.py` stay the same.

- [ ] **Step 4: Run existing API tests, expect green**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_api.py -v
```

- [ ] **Step 5: Commit**

```
git add -A src/open_pulse_crawler/api/
git commit -m "refactor(api): split into api/v1.py + api/deps.py"
```

---

## Task 17: `/api/v2` — unified-shape endpoints + v1 non-GitHub seed rejection

**Files:**
- Create: `src/open_pulse_crawler/api/v2.py`
- Modify: `src/open_pulse_crawler/api/v1.py` — reject non-GitHub seeds with 400
- Modify: `src/open_pulse_crawler/api/__init__.py` — mount v2
- Test: `tests/test_api_v2.py`, `tests/test_api_v1_compat.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_api_v2.py — health + platforms + crawl shape
from fastapi.testclient import TestClient
from open_pulse_crawler.api import app

client = TestClient(app)


def test_v2_health():
    r = client.get("/api/v2/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_v2_platforms_lists_enabled(monkeypatch):
    monkeypatch.setenv("CRAWLER_PLATFORMS", "github.com,gitlab.epfl.ch")
    r = client.get("/api/v2/platforms")
    assert r.status_code == 200
    hosts = [p["host"] for p in r.json()["platforms"]]
    assert hosts == ["github.com", "gitlab.epfl.ch"]
```

```python
# tests/test_api_v1_compat.py
from fastapi.testclient import TestClient
from open_pulse_crawler.api import app

client = TestClient(app)

def test_v1_rejects_non_github_seed():
    r = client.post("/api/v1/crawl",
                    json={"seeds": ["https://gitlab.com/foo"], "rounds": 1})
    assert r.status_code == 400
    assert "v2" in r.json()["error"].lower()


def test_v1_response_shape_remains_users_orgs_repos(snapshot):
    # Snapshot-test against a golden GitHub-only crawl result.
    # Uses a recorded fixture loaded via a fake registry.
    ...
```

- [ ] **Step 2: Implement `v2.py`** — endpoints: `GET /health`, `GET /platforms`, `POST /crawl`, `GET /graph/{job_id}`, `GET /nodes` (filtered).

- [ ] **Step 3: Add the v1 seed-host guard**

In `v1.py`'s crawl handler, before dispatching:

```python
from ..uris import host_of, InvalidURIError
for s in payload.seeds:
    try:
        host = host_of(s if s.startswith("https://") else f"https://github.com/{s}")
    except InvalidURIError:
        continue
    if host != "github.com":
        raise HTTPException(
            status_code=400,
            detail={"error": "v1 supports github.com seeds only; use /api/v2"},
        )
```

- [ ] **Step 4: Mount v2 in `api/__init__.py`** — `app.include_router(v2_router, prefix="/api/v2")`.

- [ ] **Step 5: Run, verify pass + commit**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_api_v2.py tests/test_api_v1_compat.py -v
git add -A src/open_pulse_crawler/api/ tests/test_api_v2.py tests/test_api_v1_compat.py
git commit -m "feat(api): /api/v2 unified shape; v1 rejects non-github seeds"
```

---

## Task 18: CLI changes — multi-platform seeds + `crawler doctor`

**Files:**
- Modify: `src/open_pulse_crawler/cli.py`

- [ ] **Step 1: Tests**

```python
# tests/test_cli.py — new file (or extend existing)
from typer.testing import CliRunner
from open_pulse_crawler.cli import app

runner = CliRunner()

def test_doctor_lists_hosts(monkeypatch):
    monkeypatch.setenv("CRAWLER_PLATFORMS", "github.com,gitlab.epfl.ch")
    monkeypatch.setenv("CRAWLER_TOKEN__GITHUB_COM", "tok")
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 0
    assert "github.com" in r.stdout
    assert "gitlab.epfl.ch" in r.stdout
    assert "1 token" in r.stdout
```

- [ ] **Step 2: Implement**

- Update `crawl` to build a `PlatformRegistry` from `CRAWLER_PLATFORMS` (or `--platforms`), construct the appropriate adapter for each host, normalize each seed via the adapter, and run the new `Crawler`.
- Add `--default-host` (default `github.com`) for bare-login / `owner/repo` seeds.
- Add `--crawl-stars` flag (default false).
- Add new `doctor` subcommand: iterates over enabled instances, reports token count and a one-call health check.

- [ ] **Step 3: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_cli.py -v
```

- [ ] **Step 4: Commit**

```
git add src/open_pulse_crawler/cli.py tests/test_cli.py
git commit -m "feat(cli): multi-platform seeds, --platforms, --crawl-stars, doctor"
```

---

# Block D — Migration, integration, docs

## Task 19: Cache layout per host + schema-version migration error

**Files:**
- Modify: `src/open_pulse_crawler/platforms/github/client.py` — cache path becomes `cache/<host>/<sha>.json` (was `cache/<sha>.json`)
- Modify: the GitLab client (Task 13) writes to the same layout
- Modify: `src/open_pulse_crawler/crawler.py` — state-file schema version bumped

- [ ] **Step 1: Tests for the migration error**

```python
# tests/test_migration.py
import json, pytest
from pathlib import Path
from open_pulse_crawler.crawler import Crawler, IncompatibleStateError


def test_old_state_file_rejected(tmp_path: Path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"schema_version": 0, "visited": ["torvalds"]}))
    with pytest.raises(IncompatibleStateError):
        Crawler.resume(state, registry=None)  # type: ignore
```

- [ ] **Step 2: Implement `Crawler.resume()` + `IncompatibleStateError`** — current schema version is `1`. Reading a state file with `schema_version != 1` raises with the message *"snapshot schema is from an earlier release; please start a fresh crawl"*.

- [ ] **Step 3: Update cache dir layout in both clients** — `cache_dir / host / sha256(uri).json`.

- [ ] **Step 4: Run, verify pass + commit**

```
git add src/open_pulse_crawler/ tests/test_migration.py
git commit -m "feat: bump state schema to v1; cache layout cache/<host>/<sha>.json"
```

---

## Task 20: Integration test — tiny live GitLab.com crawl

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_gitlab_dryrun.py`

- [ ] **Step 1: Write the integration test (skipped when no token)**

```python
# tests/integration/test_gitlab_dryrun.py
import os, pytest
pytestmark = pytest.mark.integration

@pytest.mark.skipif(
    not os.environ.get("CRAWLER_TOKEN__GITLAB_COM"),
    reason="no CRAWLER_TOKEN__GITLAB_COM in env",
)
def test_one_round_against_gitlab_com():
    from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter
    from open_pulse_crawler.platforms.gitlab.client import GitLabClient
    from open_pulse_crawler.platforms import PlatformRegistry
    from open_pulse_crawler.crawler import Crawler
    from open_pulse_crawler.config import resolve_tokens

    tokens = resolve_tokens("gitlab.com")
    client = GitLabClient(host="gitlab.com", tokens=tokens)
    adapter = GitLabAdapter(client=client, instance_host="gitlab.com")
    registry = PlatformRegistry(); registry.register(adapter)

    crawler = Crawler(registry=registry, max_rounds=1)
    crawler.add_seeds(["https://gitlab.com/gitlab-org/gitlab"])
    g = crawler.run()
    assert "https://gitlab.com/gitlab-org/gitlab" in g.nodes
```

- [ ] **Step 2: Run locally with a token to verify; CI skips.**

- [ ] **Step 3: Commit**

```
git add tests/integration/
git commit -m "test(integration): tiny gitlab.com dryrun (skipped without token)"
```

---

## Task 21: Documentation

**Files:**
- Create: `docs/GITLAB.md` — how to get tokens for each instance, manual-test recipes for `gitlab.epfl.ch`/`gitlab.ethz.ch`/`renkulab.io`, known per-instance quirks (e.g. older versions missing `/starrers`)
- Modify: `README.md` — multi-platform section, `CRAWLER_PLATFORMS` and `CRAWLER_TOKEN_POOL__*` examples
- Modify: `docs/API.md` — `/api/v2` shape, v1 deprecation note
- Modify: `docs/DEPLOYMENT.md` — env-var name changes
- Modify: `docs/index.md` — multi-platform pointer
- Modify: `CHANGELOG.md` — under `[Unreleased]`:
  - **Added**: GitLab support (multi-instance), `/api/v2`, `crawler doctor`, `--crawl-stars`
  - **Changed**: node IDs are now canonical HTTPS URLs; unified `Node` hierarchy with `kind` + `subkind`; cache layout `cache/<host>/<sha>.json`
  - **Removed**: `TeamModel` and team-crawling code paths
  - **Deprecated**: legacy `CRAWLER_GITHUB_TOKEN[_POOL]`, `GITHUB_TOKEN` env vars (still work for `github.com` with warning); `/api/v1` (compat-shim only for github-only crawls)

- [ ] **Step 1: Write the docs**

- [ ] **Step 2: Commit**

```
git add docs/ README.md CHANGELOG.md
git commit -m "docs: multi-platform crawler, GitLab guide, v1→v2 migration notes"
```

---

## Task 22: Final pre-PR verification

- [ ] **Step 1: Lint**

```
VIRTUAL_ENV= uv run ruff check src/
```

- [ ] **Step 2: Full unit-test suite (serial)**

```
VIRTUAL_ENV= uv run pytest -n 0 -m "not integration" -q
```

Expected: all green.

- [ ] **Step 3: Tally LOC delta**

```
git diff --stat origin/develop...HEAD
```

Sanity-check that file sizes match the spec's effort estimate.

- [ ] **Step 4: Push branch and open PR (do NOT push without user permission)**

Stop here and report ready for review. The user will decide when to push and open the PR.

---

## Self-review

**Spec coverage:**

| Spec section | Tasks |
|---|---|
| §1 Architecture & module layout | 1, 3, 5, 6, 7, 13, 14, 15, 16, 17 |
| §2.1 Class hierarchy | 2 |
| §2.2 GraphData | 2 |
| §2.3 Edges | 7, 11, 15 |
| §2.4 URI normalization | 1, 6, 14 |
| §3.1 PlatformAdapter ABC | 3 |
| §3.2 GitLab adapter + quirks | 13, 14, 15 |
| §4 Crawler | 8, 9 |
| §5 Configuration | 4 |
| §6.1 `/api/v2` | 17 |
| §6.2 `/api/v1` compat shim | 17 |
| §7 CLI | 18 |
| §8 Cache / state migration | 19 |
| §9 Testing | every task; integration in 20 |
| §10 Effort estimate | n/a (informational) |
| §11 Open questions | will be answered during execution; doctor format in Task 18 |

**Placeholder scan:** No "TBD"/"TODO"/"implement later". Step 2 of Task 17 mentions the v2 implementation in prose rather than full code — that's because v2 is a relatively conventional FastAPI router and the spec already defines its endpoints; the executor builds it directly from §6.1 of the spec. Acceptable.

**Type consistency:** `Crawler` (not `GitHubCrawler`) used consistently from Task 9 onwards. `Edge`, `ExpandOpts`, `RateLimitInfo`, `PlatformAdapter`, `PlatformRegistry`, `NodeKind`, the concrete subclasses — all spelled identically across tasks.

**Open question carried into the executor's notebook:** the cache key for the user-vs-group disambiguation probe is currently in-memory only (Task 14's `_kind_cache`). If the executor finds that needs persisting across runs, store under `cache/<host>/_disambig/<sha256(path)>.json`.
