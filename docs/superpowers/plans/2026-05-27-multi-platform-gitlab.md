# Multi-platform crawler (abstraction + GitLab, targets v3.0.0) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `PlatformAdapter` abstraction and a GitLab adapter (multi-instance) on top of the v2.0.0 URL-keyed model. Land `/api/v2` and host-keyed env vars. Targets v3.0.0.

**Architecture:** Approach A — strangler-fig refactor. Reuse existing `node_id.py` for URL helpers. Extend the flat v2 models with typed subkind subclasses for GitLab. Move GitHub clients under `platforms/github/`. Add `platforms/gitlab/` with a python-gitlab wrapper. Crawler dispatches by host via a `PlatformRegistry`.

**Tech Stack:** Python 3.10+, Pydantic v2 discriminated unions, FastAPI, PyGithub (existing), python-gitlab (new), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-05-27-multi-platform-gitlab-design.md`.

**Branch / worktree:** `feat/multi-platform-gitlab` from `origin/develop` (post-v2.0.0), at `.worktrees/feat-multi-platform-gitlab/`.

**Test invocation note:** pytest with the default `-n auto` hangs in this sandbox. Run with `-n 0`:
```
VIRTUAL_ENV= uv run pytest -n 0 <args>
```

---

## What's already in v2.0.0 — skipped

- URL-keyed graph + `node_id.py` helpers
- Flat `UserModel`/`OrgModel`/`RepoModel`/`TeamModel` with `url` + `platform: str` fields
- `GRAPH_SCHEMA_VERSION = 2`; URL-keyed BFS queue/visited; URL-keyed JSON+CSV export
- `token_env.py` with `CRAWLER_GITHUB_TOKEN_POOL`/`CRAWLER_GITHUB_TOKEN`/`GITHUB_TOKEN`

This plan picks up from there.

---

## File map

**New files:**
- `src/open_pulse_crawler/config.py` — host-keyed env-var resolution
- `src/open_pulse_crawler/platforms/__init__.py` — `PlatformRegistry`
- `src/open_pulse_crawler/platforms/base.py` — `PlatformAdapter`, `Edge`, `ExpandOpts`, `RateLimitInfo`
- `src/open_pulse_crawler/platforms/github/__init__.py`
- `src/open_pulse_crawler/platforms/github/adapter.py`
- `src/open_pulse_crawler/platforms/gitlab/__init__.py`
- `src/open_pulse_crawler/platforms/gitlab/client.py`
- `src/open_pulse_crawler/platforms/gitlab/adapter.py`
- `src/open_pulse_crawler/api/__init__.py`
- `src/open_pulse_crawler/api/v1.py` (from `api.py`)
- `src/open_pulse_crawler/api/v2.py`
- `src/open_pulse_crawler/api/deps.py`
- `tests/test_config.py`
- `tests/platforms/__init__.py`
- `tests/platforms/test_base.py`
- `tests/platforms/test_github_adapter.py`
- `tests/platforms/test_gitlab_client.py`
- `tests/platforms/test_gitlab_adapter.py`
- `tests/platforms/_fake_adapter.py`
- `tests/test_api_v1_compat.py`
- `tests/test_api_v2.py`
- `tests/integration/__init__.py`
- `tests/integration/test_gitlab_dryrun.py`
- `docs/GITLAB.md`

**Moved files (git mv):**
- `src/open_pulse_crawler/github_client.py` → `src/open_pulse_crawler/platforms/github/client.py`
- `src/open_pulse_crawler/graphql_client.py` → `src/open_pulse_crawler/platforms/github/graphql.py`
- `src/open_pulse_crawler/api.py` → `src/open_pulse_crawler/api/v1.py`

**Modified:**
- `src/open_pulse_crawler/models.py` — add `subkind` discriminator + `extras` + `external_identifiers` to `BaseEntityModel`; set `Literal["…"]` subkind on each existing model; add `GitLabUserModel/GitLabGroupModel/GitLabProjectModel`. Bump `GRAPH_SCHEMA_VERSION` to 3.
- `src/open_pulse_crawler/crawler.py` — add `registry: PlatformRegistry` parameter; dispatch fetch/expand by host. State-file load rejects schema != 3.
- `src/open_pulse_crawler/token_env.py` — delegate to `config.py` for `github.com`; keep public API for backcompat.
- `src/open_pulse_crawler/cli.py` — `--platforms`, `--default-host`, `--crawl-stars`, `doctor` subcommand.
- `src/open_pulse_crawler/visualization.py` — color by `subkind`.
- `pyproject.toml` — add `python-gitlab>=4,<6`. Bump version to `3.0.0`.
- `CHANGELOG.md`, `README.md`, `docs/API.md`, `docs/DEPLOYMENT.md`, `docs/index.md`.

---

## Task ordering

Block A (Tasks 1–7): foundations — config, subkind models, ABC, GitHub move + adapter, crawler dispatch.
Block B (Tasks 8–11): GitLab adapter.
Block C (Tasks 12–14): API split + `/api/v2`, CLI changes.
Block D (Tasks 15–18): per-host cache layout + migration, integration test, docs, final verification.

Every task is TDD-shaped: failing test → minimal implementation → passing test → commit. Conventional Commits per `AGENTS.md`.

---

# Block A — Foundations and GitHub port

## Task 1: `config.py` — host-keyed env-var resolution

**Files:**
- Create: `src/open_pulse_crawler/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
import pytest
from open_pulse_crawler.config import (
    host_env_ident, resolve_tokens, enabled_instances,
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
    for v in ("CRAWLER_TOKEN_POOL__GITLAB_ETHZ_CH", "CRAWLER_TOKEN__GITLAB_ETHZ_CH"):
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
    return re.sub(r"[.\-]", "_", host.lower()).upper()


def enabled_instances() -> list[str]:
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
        "CRAWLER_TOKEN__GITHUB_COM. Will be removed in v4.",
        DeprecationWarning, stacklevel=3,
    )


def resolve_tokens(host: str) -> list[str]:
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

- [ ] **Step 4: Delegate from token_env.py and run all token-related tests**

Modify `token_env.resolve_github_tokens` to call `config.resolve_tokens("github.com")`. Keep `POOL_ENV`, `TOKEN_ENV`, `LEGACY_ENV`, `tokens_not_set_message()` exported unchanged.

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_config.py tests/test_token_env.py -v
```
(Skip the second file if it doesn't exist on develop.)

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/config.py src/open_pulse_crawler/token_env.py tests/test_config.py
git commit -m "feat(config): host-keyed env-var resolution with legacy GitHub fallback"
```

---

## Task 2: Subkind discriminator + GitLab subclasses in `models.py`

**Files:**
- Modify: `src/open_pulse_crawler/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_models.py (append)
from open_pulse_crawler.models import (
    GitLabUserModel, GitLabGroupModel, GitLabProjectModel,
    UserModel, OrgModel, RepoModel, GraphData, GRAPH_SCHEMA_VERSION,
)


def test_existing_models_have_subkind():
    u = UserModel(login="torvalds")
    assert u.subkind == "GitHubUser"
    o = OrgModel(login="anthropic")
    assert o.subkind == "GitHubOrganization"
    r = RepoModel(full_name="owner/repo")
    assert r.subkind == "GitHubRepository"


def test_gitlab_user_subkind_and_fields():
    u = GitLabUserModel(
        url="https://gitlab.epfl.ch/alice", login="alice",
        platform="gitlab", state="active",
    )
    assert u.subkind == "GitLabUser"
    assert u.state == "active"


def test_gitlab_project_defaults():
    p = GitLabProjectModel(
        url="https://gitlab.epfl.ch/g/p", full_name="g/p",
        platform="gitlab",
    )
    assert p.subkind == "GitLabProject"
    assert p.visibility == "public"


def test_graphdata_roundtrips_gitlab_subclass():
    g = GraphData()
    p = GitLabProjectModel(
        url="https://gitlab.com/x/y", full_name="x/y", platform="gitlab"
    )
    g.repos[p.url] = p
    g2 = GraphData.model_validate_json(g.model_dump_json())
    assert isinstance(g2.repos["https://gitlab.com/x/y"], GitLabProjectModel)


def test_schema_version_bumped_to_3():
    assert GRAPH_SCHEMA_VERSION == 3
```

- [ ] **Step 2: Run, verify failures**

- [ ] **Step 3: Modify `models.py`**

Add to `BaseEntityModel`:
```python
from typing import Any
class ExternalIdentifier(BaseModel):
    scheme: str
    value: str
# ... in BaseEntityModel:
external_identifiers: list["ExternalIdentifier"] = Field(default_factory=list)
extras: dict[str, Any] = Field(default_factory=dict)
```

Set `subkind` on each existing model:
```python
class UserModel(BaseEntityModel):
    subkind: Literal["GitHubUser"] = "GitHubUser"
    # ... unchanged
class OrgModel(BaseEntityModel):
    subkind: Literal["GitHubOrganization"] = "GitHubOrganization"
class RepoModel(BaseEntityModel):
    subkind: Literal["GitHubRepository"] = "GitHubRepository"
class TeamModel(BaseEntityModel):
    subkind: Literal["GitHubTeam"] = "GitHubTeam"
```

Add GitLab subclasses:
```python
class GitLabUserModel(UserModel):
    subkind: Literal["GitLabUser"] = "GitLabUser"
    state: Literal["active", "blocked", "deactivated"] = "active"
    public_email: str = ""

class GitLabGroupModel(OrgModel):
    subkind: Literal["GitLabGroup"] = "GitLabGroup"
    parent: Optional[str] = None
    visibility: Literal["private", "internal", "public"] = "public"

class GitLabProjectModel(RepoModel):
    subkind: Literal["GitLabProject"] = "GitLabProject"
    visibility: Literal["private", "internal", "public"] = "public"
    namespace: Optional[str] = None
```

Convert `GraphData` dict types to discriminated unions:
```python
from typing import Annotated, Union
UserNode = Annotated[Union[UserModel, GitLabUserModel], Field(discriminator="subkind")]
OrgNode  = Annotated[Union[OrgModel,  GitLabGroupModel], Field(discriminator="subkind")]
RepoNode = Annotated[Union[RepoModel, GitLabProjectModel], Field(discriminator="subkind")]

class GraphData(BaseModel):
    schema_version: int = GRAPH_SCHEMA_VERSION
    users: dict[str, UserNode] = Field(default_factory=dict)
    orgs:  dict[str, OrgNode]  = Field(default_factory=dict)
    repos: dict[str, RepoNode] = Field(default_factory=dict)
    teams: dict[str, TeamModel] = Field(default_factory=dict)

    def of_subkind(self, subkind: str):
        for d in (self.users, self.orgs, self.repos, self.teams):
            for n in d.values():
                if n.subkind == subkind:
                    yield n

    def by_platform(self, platform: str):
        for d in (self.users, self.orgs, self.repos, self.teams):
            for n in d.values():
                if n.platform == platform:
                    yield n
```

Bump: `GRAPH_SCHEMA_VERSION = 3`.

- [ ] **Step 4: Pass + commit**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_models.py -v
git add src/open_pulse_crawler/models.py tests/test_models.py
git commit -m "feat(models): subkind discriminator + GitLab subclasses (schema v3)"
```

---

## Task 3: `PlatformAdapter` ABC + `PlatformRegistry`

**Files:**
- Create: `src/open_pulse_crawler/platforms/__init__.py`
- Create: `src/open_pulse_crawler/platforms/base.py`
- Create: `tests/platforms/__init__.py` (empty)
- Test: `tests/platforms/test_base.py`

- [ ] **Step 1: Write failing tests**

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
    assert o.crawl_stars is False
    assert o.max_contributors is None


def test_edge_shape():
    e = Edge(src="https://github.com/a", kind="contributor_of",
             dst="https://github.com/a/repo")
    assert e.kind == "contributor_of"


def test_registry_unknown_host_raises():
    r = PlatformRegistry()
    with pytest.raises(KeyError):
        r.adapter_for("https://unknown.example.com/foo")


def test_registry_resolves_by_host():
    class FakeAdapter(PlatformAdapter):
        platform = "fake"
        def __init__(self, host): self.instance_host = host
        def classify(self, uri): return None
        def fetch(self, uri): return None
        def expand(self, n, o): return iter([])
        def normalize_uri(self, raw): return raw
        def rate_limit_state(self): return RateLimitInfo(remaining=1, limit=1)
    r = PlatformRegistry(); r.register(FakeAdapter("example.com"))
    assert r.adapter_for("https://example.com/x").instance_host == "example.com"
```

- [ ] **Step 2: Run, verify failures**

- [ ] **Step 3: Implement**

```python
# src/open_pulse_crawler/platforms/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import ClassVar, Iterable
from pydantic import BaseModel


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
    def classify(self, uri: str): ...
    @abstractmethod
    def fetch(self, uri: str): ...
    @abstractmethod
    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]: ...
    @abstractmethod
    def normalize_uri(self, raw: str) -> str: ...
    @abstractmethod
    def rate_limit_state(self) -> RateLimitInfo: ...
```

```python
# src/open_pulse_crawler/platforms/__init__.py
from __future__ import annotations
from urllib.parse import urlparse
from .base import PlatformAdapter, Edge, ExpandOpts, RateLimitInfo


class PlatformRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter) -> None:
        self._adapters[adapter.instance_host] = adapter

    def adapter_for(self, uri: str) -> PlatformAdapter:
        host = urlparse(uri).netloc.lower()
        if host not in self._adapters:
            raise KeyError(f"no adapter registered for host {host!r}")
        return self._adapters[host]

    def hosts(self) -> list[str]:
        return sorted(self._adapters)


__all__ = ["PlatformAdapter", "PlatformRegistry", "Edge", "ExpandOpts", "RateLimitInfo"]
```

- [ ] **Step 4: Pass + commit**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_base.py -v
git add src/open_pulse_crawler/platforms/ tests/platforms/
git commit -m "feat(platforms): PlatformAdapter ABC + registry"
```

---

## Task 4: Move GitHub clients under `platforms/github/`

- [ ] **Step 1: Inspect imports**

```
grep -rn "from .github_client\|from open_pulse_crawler.github_client\|from .graphql_client\|from open_pulse_crawler.graphql_client" src tests
```

- [ ] **Step 2: `git mv`**

```
mkdir -p src/open_pulse_crawler/platforms/github
git mv src/open_pulse_crawler/github_client.py src/open_pulse_crawler/platforms/github/client.py
git mv src/open_pulse_crawler/graphql_client.py src/open_pulse_crawler/platforms/github/graphql.py
```

- [ ] **Step 3: Re-export shim**

```python
# src/open_pulse_crawler/platforms/github/__init__.py
from .client import GitHubClient, resolve_cache_dir
from .graphql import GitHubGraphQLClient   # adjust to actual class name
```

Inside the moved files, update relative imports: `from .models` → `from ...models`, `from .token_env` → `from ...token_env`, etc.

For top-level callers (`cli.py`, `crawler.py`, `api.py`, `gui.py`), either update imports to the new path, or add to the package `__init__.py`:

```python
# src/open_pulse_crawler/__init__.py
from .platforms.github import GitHubClient   # back-compat re-export
```

- [ ] **Step 4: Full unit suite green**

```
VIRTUAL_ENV= uv run pytest -n 0 -m "not integration" -q
```

- [ ] **Step 5: Commit**

```
git add -A src/ tests/
git commit -m "refactor: move github_client + graphql_client under platforms/github/"
```

---

## Task 5: `GitHubAdapter`

**Files:**
- Create: `src/open_pulse_crawler/platforms/github/adapter.py`
- Test: `tests/platforms/test_github_adapter.py`

- [ ] **Step 1: Tests** — for normalize, classify, fetch (User/Org/Repo), and a representative expand (User and Repo).

```python
# tests/platforms/test_github_adapter.py
from unittest.mock import MagicMock
import pytest
from open_pulse_crawler.models import UserModel, OrgModel, RepoModel
from open_pulse_crawler.platforms.base import ExpandOpts
from open_pulse_crawler.platforms.github.adapter import GitHubAdapter


@pytest.fixture
def adapter():
    return GitHubAdapter(client=MagicMock(), instance_host="github.com")


def test_normalize_strips_trailing_slash(adapter):
    assert adapter.normalize_uri("https://github.com/Torvalds/") == "https://github.com/Torvalds"


def test_fetch_repo(adapter):
    raw = MagicMock(
        full_name="owner/repo", id=5, name="repo",
        fork=False, parent=None, stargazers_count=42,
    )
    adapter._client.get_repo_object.return_value = raw
    node = adapter.fetch("https://github.com/owner/repo")
    assert isinstance(node, RepoModel)
    assert node.subkind == "GitHubRepository"
    assert node.full_name == "owner/repo"


def test_expand_user_emits_authored_edges(adapter):
    user = UserModel(url="https://github.com/a", login="a", platform="github")
    for m in ("iter_followers", "iter_following", "iter_starred",
              "iter_watched", "iter_user_forks"):
        getattr(adapter._client, m).return_value = []
    adapter._client.iter_user_repos.return_value = ["a/r1", "a/r2"]
    edges = list(adapter.expand(user, ExpandOpts()))
    authored = [e for e in edges if e.kind == "authored"]
    assert sorted(e.dst for e in authored) == [
        "https://github.com/a/r1", "https://github.com/a/r2",
    ]
```

- [ ] **Step 2: Run, verify failures**

- [ ] **Step 3: Implement** (full file from spec §3.1 / §3.2). If the existing `GitHubClient` exposes different method names than the adapter calls (`iter_followers`, `iter_user_repos`, etc.), add thin shim methods in `platforms/github/client.py` that delegate.

- [ ] **Step 4: Pass + commit**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_github_adapter.py -v
git add src/open_pulse_crawler/platforms/github/adapter.py tests/platforms/test_github_adapter.py
git commit -m "feat(github): GitHubAdapter — classify/fetch/expand"
```

---

## Task 6: Crawler dispatch through registry + `FakePlatformAdapter`

**Files:**
- Create: `tests/platforms/_fake_adapter.py`
- Modify: `src/open_pulse_crawler/crawler.py`
- Create: `tests/test_crawler_dispatch.py`

- [ ] **Step 1: Fake adapter**

```python
# tests/platforms/_fake_adapter.py
from __future__ import annotations
from typing import ClassVar, Iterable
from open_pulse_crawler.platforms.base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo


class FakePlatformAdapter(PlatformAdapter):
    platform: ClassVar[str] = "fake"
    def __init__(self, instance_host="fake.test"):
        self.instance_host = instance_host
        self.fetched: dict[str, object] = {}
        self.edges: dict[str, list[Edge]] = {}
    def normalize_uri(self, raw): return raw.rstrip("/")
    def classify(self, uri): return None
    def fetch(self, uri): return self.fetched[uri]
    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        return iter(self.edges.get(getattr(node, "url", ""), []))
    def rate_limit_state(self): return RateLimitInfo(remaining=999, limit=1000)
```

- [ ] **Step 2: Add `registry` kwarg to the crawler**

Keep the existing `client=` kwarg working: when `registry` isn't passed, construct one from the supplied client. Replace per-node calls into `self.client.*` with `adapter = self.registry.adapter_for(uri); node = adapter.fetch(uri); for e in adapter.expand(node, self.opts): ...`.

- [ ] **Step 3: Tests**

```python
# tests/test_crawler_dispatch.py
from open_pulse_crawler.crawler import GitHubCrawler
from open_pulse_crawler.platforms import PlatformRegistry
from open_pulse_crawler.platforms.base import Edge
from open_pulse_crawler.models import UserModel, RepoModel
from tests.platforms._fake_adapter import FakePlatformAdapter


def test_dispatch_uses_registered_adapter():
    fake = FakePlatformAdapter()
    seed = UserModel(url="https://fake.test/a", login="a", platform="fake")
    repo = RepoModel(url="https://fake.test/a/r", full_name="a/r", platform="fake")
    fake.fetched = {seed.url: seed, repo.url: repo}
    fake.edges = {seed.url: [Edge(src=seed.url, kind="authored", dst=repo.url)], repo.url: []}
    reg = PlatformRegistry(); reg.register(fake)
    c = GitHubCrawler(registry=reg, max_rounds=2)
    c.add_seeds([seed.url])
    g = c.run()
    assert seed.url in g.users
    assert repo.url in g.repos
```

- [ ] **Step 4: Pass + commit**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_crawler_dispatch.py -v
git add tests/platforms/_fake_adapter.py src/open_pulse_crawler/crawler.py tests/test_crawler_dispatch.py
git commit -m "feat(crawler): dispatch via PlatformRegistry; keep client kwarg for compat"
```

---

## Task 7: Visualization — color by `subkind`

- [ ] **Step 1: Test** (extend `tests/test_io_utils.py` or add a new small one): a node with `subkind="GitLabProject"` gets the GitLab project color, not the GitHub repo color.

- [ ] **Step 2: Update `visualization.py`** — replace `GitHubItemType`-based color logic with a subkind map; unknown subkinds → grey.

```python
SUBKIND_COLOR = {
    "GitHubUser": "#1f77b4",
    "GitHubOrganization": "#ff7f0e",
    "GitHubRepository": "#2ca02c",
    "GitHubTeam": "#bcbd22",
    "GitLabUser": "#9467bd",
    "GitLabGroup": "#8c564b",
    "GitLabProject": "#e377c2",
}
DEFAULT_COLOR = "#7f7f7f"
```

- [ ] **Step 3: Commit**

```
git commit -m "refactor(viz): color nodes by subkind"
```

---

# Block B — GitLab adapter

## Task 8: `python-gitlab` dependency + version bump

- [ ] **Step 1: Edit `pyproject.toml`** — add `"python-gitlab>=4,<6"` to `[project.dependencies]`; bump `version = "3.0.0"`.

- [ ] **Step 2: Lock + sync**

```
uv lock
VIRTUAL_ENV= uv sync --extra dev
VIRTUAL_ENV= uv run python -c "import gitlab; print(gitlab.__version__)"
```

- [ ] **Step 3: Commit**

```
git add pyproject.toml uv.lock
git commit -m "build: add python-gitlab; bump version to 3.0.0"
```

---

## Task 9: GitLab client wrapper

**Files:**
- Create: `src/open_pulse_crawler/platforms/gitlab/__init__.py`
- Create: `src/open_pulse_crawler/platforms/gitlab/client.py`
- Test: `tests/platforms/test_gitlab_client.py`

- [ ] **Step 1: Tests**

```python
# tests/platforms/test_gitlab_client.py
from unittest.mock import MagicMock
import gitlab
import pytest
from open_pulse_crawler.platforms.gitlab.client import GitLabClient


def make_client(gl_mock):
    return GitLabClient(host="gitlab.example.com", tokens=["t1"],
                        _gl_factory=lambda *a, **kw: gl_mock)


def test_get_user_username():
    gl = MagicMock()
    gl.users.list.return_value = [MagicMock(id=42, username="alice")]
    assert make_client(gl).get_user_by_username("alice").id == 42


def test_get_group_by_path():
    gl = MagicMock()
    gl.groups.get.return_value = MagicMock(id=99, full_path="g/sub")
    assert make_client(gl).get_group_by_path("g/sub").full_path == "g/sub"


def test_get_project_by_path_404_returns_none():
    gl = MagicMock()
    gl.projects.get.side_effect = gitlab.GitlabGetError(response_code=404)
    assert make_client(gl).get_project_by_path("g/sub/x") is None


def test_requires_tokens():
    with pytest.raises(ValueError):
        GitLabClient(host="gitlab.example.com", tokens=[])
```

- [ ] **Step 2: Implement `client.py`** — per spec §3.2. Methods needed: `get_user_by_username`, `get_group_by_path`, `get_project_by_path`, `iter_group_members`, `iter_subgroups`, `iter_group_projects`, `iter_user_projects`, `iter_user_contributed`, `iter_user_starred`, `iter_project_contributors`, `iter_project_forks`, `iter_project_issues`, `iter_project_merge_requests`, `iter_project_starrers` (returns `[]` when missing). Token rotation rotates `self._idx` and rebuilds the underlying `gitlab.Gitlab` instance.

- [ ] **Step 3: Pass + commit**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_gitlab_client.py -v
git add src/open_pulse_crawler/platforms/gitlab/ tests/platforms/test_gitlab_client.py
git commit -m "feat(gitlab): python-gitlab wrapper with token rotation"
```

---

## Task 10: `GitLabAdapter` — classify + fetch + normalize

**Files:**
- Create: `src/open_pulse_crawler/platforms/gitlab/adapter.py`
- Test: `tests/platforms/test_gitlab_adapter.py`

- [ ] **Step 1: Tests** — top-level user vs group; nested project vs subgroup; fetch returns the right GitLab subclass with platform fields set; normalize strips trailing slash.

- [ ] **Step 2: Implement** — see spec §3.2 for the full method bodies. Use an in-memory `_kind_cache: dict[path, "user"|"group"|"project"|"unknown"]` to avoid re-probing.

- [ ] **Step 3: Commit**

```
git commit -m "feat(gitlab): GitLabAdapter classify/fetch/normalize with disambiguation"
```

---

## Task 11: `GitLabAdapter.expand()`

- [ ] **Step 1: Tests** — User: authored / starred / contributed edges. Group: members / subgroups / authored. Project: contributors / forks / starrers (gated by `crawl_stars`). `max_contributors` cap. Missing `/starrers` endpoint returns no edges.

- [ ] **Step 2: Implement** — see spec §3.2.

- [ ] **Step 3: Commit**

```
git commit -m "feat(gitlab): GitLabAdapter.expand() — users, groups, projects edges"
```

---

# Block C — API and CLI

## Task 12: Split `api.py` into `api/v1.py` + `api/deps.py`

- [ ] **Step 1: `git mv` and split**

```
mkdir -p src/open_pulse_crawler/api
git mv src/open_pulse_crawler/api.py src/open_pulse_crawler/api/v1.py
```

Extract shared bits (job store, auth helpers, background-task plumbing) into `api/deps.py`. Convert the top-level `app` inside `v1.py` to a `router = APIRouter()`. Create:

```python
# src/open_pulse_crawler/api/__init__.py
from fastapi import FastAPI
from .v1 import router as v1_router

app = FastAPI(title="Open Pulse Crawler")
app.include_router(v1_router, prefix="/api/v1")
```

- [ ] **Step 2: Existing API tests still pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_api.py -v
```

- [ ] **Step 3: Commit**

```
git add -A src/open_pulse_crawler/api/ tests/
git commit -m "refactor(api): split into api/v1.py + api/deps.py"
```

---

## Task 13: `/api/v2` + v1 non-GitHub seed guard

- [ ] **Step 1: Tests** — `tests/test_api_v2.py` (health, platforms, crawl shape, filtered nodes) and `tests/test_api_v1_compat.py` (400 on non-github seeds; existing v2.0.0 response byte-identical for github-only crawls).

- [ ] **Step 2: Implement `api/v2.py`** — router with `health`, `platforms`, `crawl`, `graph/{job_id}`, `nodes` filtering by `subkind`/`platform`/`instance`. Add seed-host guard at the top of v1's crawl handler that rejects non-`github.com` hosts with 400. Mount v2 in `api/__init__.py`. v1 response continues to **omit** the `subkind` field (filter at serialization).

- [ ] **Step 3: Pass + commit**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_api_v2.py tests/test_api_v1_compat.py -v
git add -A src/open_pulse_crawler/api/ tests/
git commit -m "feat(api): /api/v2 unified shape; v1 rejects non-github seeds"
```

---

## Task 14: CLI — multi-platform seeds + `doctor`

- [ ] **Step 1: Tests** — `doctor` lists enabled hosts and token counts; `crawl --platforms github.com,gitlab.com https://gitlab.com/foo/bar` routes via the GitLab adapter.

- [ ] **Step 2: Implement** — `crawl` builds a `PlatformRegistry` from `CRAWLER_PLATFORMS` (or `--platforms`), constructs the right adapter per host, normalizes seeds via the adapter. Add `--default-host`, `--crawl-stars`. Add `doctor`:

```python
@app.command()
def doctor(as_json: bool = typer.Option(False, "--json")):
    import json
    from .config import enabled_instances, resolve_tokens
    rows = [{"host": h, "tokens": len(resolve_tokens(h)),
             "ok": bool(resolve_tokens(h))} for h in enabled_instances()]
    if as_json:
        typer.echo(json.dumps(rows, indent=2)); return
    for r in rows:
        typer.echo(f"{r['host']}: {r['tokens']} token(s) {'OK' if r['ok'] else 'MISSING'}")
```

- [ ] **Step 3: Pass + commit**

```
git commit -m "feat(cli): multi-platform seeds, --platforms, --crawl-stars, doctor"
```

---

# Block D — Migration, integration, docs, verification

## Task 15: Per-host cache layout + state-schema rejection

- [ ] **Step 1: Test**

```python
# tests/test_migration.py
import json, pytest
from open_pulse_crawler.crawler import GitHubCrawler, IncompatibleStateError


def test_v2_state_file_rejected(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"schema_version": 2, "visited": []}))
    with pytest.raises(IncompatibleStateError):
        GitHubCrawler.resume(state, registry=None)
```

- [ ] **Step 2: Implement `IncompatibleStateError` + version check** — reject `schema_version != 3` with the message *"snapshot schema is from v2.x; please start a fresh crawl"*.

- [ ] **Step 3: Per-host cache path** — cache key becomes `cache_dir / host / sha256(uri).json` in both the GitHub and GitLab clients.

- [ ] **Step 4: Commit**

```
git add src/ tests/test_migration.py
git commit -m "feat: bump state schema to v3; cache layout cache/<host>/<sha>.json"
```

---

## Task 16: Integration test — tiny live `gitlab.com` crawl

- [ ] **Step 1: Write**

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
    from open_pulse_crawler.crawler import GitHubCrawler
    from open_pulse_crawler.config import resolve_tokens

    tokens = resolve_tokens("gitlab.com")
    client = GitLabClient(host="gitlab.com", tokens=tokens)
    adapter = GitLabAdapter(client=client, instance_host="gitlab.com")
    reg = PlatformRegistry(); reg.register(adapter)
    c = GitHubCrawler(registry=reg, max_rounds=1)
    c.add_seeds(["https://gitlab.com/gitlab-org/gitlab"])
    g = c.run()
    assert "https://gitlab.com/gitlab-org/gitlab" in g.repos
```

- [ ] **Step 2: Commit**

```
git add tests/integration/
git commit -m "test(integration): tiny gitlab.com dryrun (skipped without token)"
```

---

## Task 17: Documentation + CHANGELOG

- [ ] **Step 1: `docs/GITLAB.md`** — how to get tokens for each instance (gitlab.com Personal Access Token, gitlab.epfl.ch SSO PAT flow, gitlab.ethz.ch, renkulab.io); manual-test recipes; per-instance quirks (older versions missing `/starrers`).
- [ ] **Step 2: `README.md`** — multi-platform section, env-var examples for `CRAWLER_PLATFORMS` and `CRAWLER_TOKEN_POOL__*`, deprecation note for `CRAWLER_GITHUB_TOKEN_POOL`.
- [ ] **Step 3: `docs/API.md`** — `/api/v2` shape, v1 deprecation.
- [ ] **Step 4: `docs/DEPLOYMENT.md`** — env-var changes.
- [ ] **Step 5: `docs/index.md`** — multi-platform pointer.
- [ ] **Step 6: `CHANGELOG.md`** — under `[Unreleased]` (will become `[3.0.0]` on release):

```
### Added
- GitLab support (multi-instance: gitlab.com, gitlab.epfl.ch, gitlab.ethz.ch, renkulab.io).
- `PlatformAdapter` abstraction + registry; GitHub and GitLab adapters.
- Subkind discriminator on every node (`GitHubUser`, `GitLabProject`, …) with typed GitLab subclasses.
- `/api/v2` — unified shape including `subkind`.
- `--platforms`, `--default-host`, `--crawl-stars` CLI flags.
- `crawler doctor` subcommand.
- Host-keyed env vars: `CRAWLER_TOKEN_POOL__<HOST>`, `CRAWLER_TOKEN__<HOST>`; `CRAWLER_PLATFORMS` to enable instances.

### Changed
- Snapshot schema bumped to 3; v2 snapshots are refused on load.
- Cache layout: `cache/<host>/<sha256(uri)>.json`.

### Deprecated
- `CRAWLER_GITHUB_TOKEN_POOL`, `CRAWLER_GITHUB_TOKEN`, `GITHUB_TOKEN`: still work for `github.com` in v3 with a one-shot deprecation warning; removed in v4.
- `/api/v1`: github-only crawls keep working; non-github seeds rejected with 400; removed in v4.
```

- [ ] **Step 7: Commit**

```
git add docs/ README.md CHANGELOG.md
git commit -m "docs: multi-platform crawler, GitLab guide, v2→v3 migration notes"
```

---

## Task 18: Final pre-PR verification

- [ ] **Step 1: Lint**

```
VIRTUAL_ENV= uv run ruff check src/
```

- [ ] **Step 2: Full unit suite (serial)**

```
VIRTUAL_ENV= uv run pytest -n 0 -m "not integration" -q
```

- [ ] **Step 3: Diff stat**

```
git diff --stat origin/develop...HEAD
```

- [ ] **Step 4: Report ready for review.** Do NOT push without explicit user permission.

---

## Self-review

**Spec coverage:**

| Spec section | Tasks |
|---|---|
| Starting point | n/a |
| Architecture & module layout | 1, 3, 4, 5, 9, 10, 12, 13 |
| Data model (subkind subclasses) | 2 |
| PlatformAdapter ABC | 3 |
| GitLab adapter + quirks | 9, 10, 11 |
| Crawler dispatch | 6 |
| Host-keyed config (with legacy fallback) | 1 |
| `/api/v2` | 13 |
| `/api/v1` shim | 13 |
| CLI | 14 |
| Cache + state migration | 15 |
| Testing | every task; integration in 16 |
| Effort estimate | n/a |
| Open questions | resolved (defaults stated in spec) |

**Placeholder scan:** Tasks 10 and 11 reference "per spec §3.2" for full method bodies rather than inlining the whole adapter. That's because §3.2 of the (committed) spec doc has the full implementation; the executor reads both. No "TBD" / "TODO" / "implement later" anywhere.

**Type consistency:** Class name `GitHubCrawler` retained for back-compat. `Edge`, `ExpandOpts`, `RateLimitInfo`, `PlatformAdapter`, `PlatformRegistry`, GitLab subkind names — all spelled identically across tasks.
