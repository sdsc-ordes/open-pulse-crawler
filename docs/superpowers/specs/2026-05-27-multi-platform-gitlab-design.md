# Multi-platform crawler — abstraction + GitLab (Spec 1, targets v3.0.0)

**Status:** Draft for review · revised 2026-05-27 after rebase on v2.0.0
**Branch:** `feat/multi-platform-gitlab` (from `origin/develop` post-v2.0.0)
**Worktree:** `.worktrees/feat-multi-platform-gitlab/`
**Sequence:** Spec 1 of 3 — this → Zenodo (Spec 2) → HuggingFace (Spec 3)

## Starting point: what v2.0.0 already shipped

The URL-keyed refactor is already in develop:

- `src/open_pulse_crawler/node_id.py` — canonical URL helpers (`canonical_url`, `extract_login`, `extract_full_name`, `extract_team_parts`, seed parsing) and a `NodeKind` enum (`USER_OR_ORG`, `REPO`, `TEAM`).
- `models.py` — `UserModel`/`OrgModel`/`RepoModel`/`TeamModel`, each keyed by a canonical `url` and carrying a `platform: str = "github"` field on `BaseEntityModel`. `GRAPH_SCHEMA_VERSION = 2`.
- URL-keyed `GraphData.users/orgs/repos/teams` dicts; URL-keyed BFS queue + visited set.
- `token_env.py` resolves `CRAWLER_GITHUB_TOKEN_POOL` → `CRAWLER_GITHUB_TOKEN` → `GITHUB_TOKEN` (the last is legacy with a deprecation warning).
- URL-keyed CSV/JSON output; v1 API contract aligned to the URL keys.

What this spec adds on top is the platform abstraction, GitLab support, multi-instance config, typed subkind subclasses, and `/api/v2`.

## Goal

1. A `PlatformAdapter` abstraction with a platform-agnostic BFS engine.
2. The existing GitHub code, ported behind the abstraction with no behaviour change.
3. A `GitLabAdapter` for `gitlab.com`, `gitlab.epfl.ch`, `gitlab.ethz.ch`, `renkulab.io`: users, groups (incl. nested subgroups via `parent`), projects, ownership/membership/contributors, issue/MR activity, fork parent/child, project stars.
4. Typed subkind subclasses on top of the v2 flat models so platform-specific fields land naturally.
5. `/api/v2` (unified shape with `kind`+`subkind`) alongside a v1 host-guard shim.

## Non-goals (deferred)

- **Zenodo adapter** — Spec 2.
- **HuggingFace adapter** — Spec 3.
- **Cross-platform identity resolution.** Models carry an `external_identifiers` slot; populating it (ORCID, email, manual) is its own design.
- **GitLab SBOM / "used by".** No public equivalent of GitHub's dependents view.
- **Renku-specific concepts** (Renku datasets, project lineage). `renkulab.io` is treated as a vanilla GitLab instance in v1.

## Locked-in design choices

| Topic | Decision |
|---|---|
| Use case | Cross-platform open-science graph. |
| Sequencing | GitLab in this spec; Zenodo next; HF last. |
| Node identity | Canonical HTTPS URL (already shipped). |
| Subkind hierarchy | **Add concrete subclasses on top of v2 flat models.** `GitLabUserModel(UserModel)`, `GitLabGroupModel(OrgModel)`, `GitLabProjectModel(RepoModel)`. `BaseEntityModel` gains a `subkind: str` Literal discriminator and an `extras: dict[str, Any]` escape hatch. |
| Token config | **Migrate all platforms to host-keyed env vars** (`CRAWLER_TOKEN_POOL__<HOST>` / `CRAWLER_TOKEN__<HOST>`). `CRAWLER_GITHUB_TOKEN_POOL` / `CRAWLER_GITHUB_TOKEN` keep working in v3 with a one-shot deprecation warning; remove in v4. |
| Architecture | Approach A — strangler-fig refactor with `PlatformAdapter` ABC. |
| API compatibility | Ship `/api/v2` (unified `kind`+`subkind` shape); `/api/v1` becomes a thin shim that 400s on non-GitHub seeds and emits the existing `users/orgs/repos/teams` shape for github-only crawls. Two-release deprecation window. |
| Migration | Hard break across the v2→v3 boundary. Snapshot schema bumps to 3. Operators re-crawl. |

## 1. Architecture & module layout

```
src/open_pulse_crawler/
  node_id.py                    # EXISTING — canonical URL helpers (reuse)
  platforms/                    # NEW
    __init__.py                 # PlatformRegistry (host → adapter)
    base.py                     # PlatformAdapter ABC + Edge + ExpandOpts + RateLimitInfo
    github/
      __init__.py               # re-exports
      adapter.py                # implements PlatformAdapter
      client.py                 # = current github_client.py, moved
      graphql.py                # = current graphql_client.py, moved
    gitlab/
      __init__.py
      adapter.py
      client.py                 # python-gitlab wrapper with token rotation
  models.py                     # EXTENDED — add subkind, extras, GitLab subclasses
  crawler.py                    # EXTENDED — dispatch via PlatformRegistry
  config.py                     # NEW — host-keyed token + instance resolution (legacy GITHUB env compat)
  token_env.py                  # KEPT — delegates to config.py for github.com
  api/
    __init__.py                 # mounts both routers
    v1.py                       # ex-api.py, kept as compat shim
    v2.py                       # NEW — unified shape
    deps.py                     # NEW — shared FastAPI deps (auth, job store)
```

The `PlatformAdapter` ABC is the single seam. `dependency_utils.py`, `gimie_*`, `io_utils.py`, `visualization.py`, `gui.py` are unchanged structurally — they already speak URL keys.

```python
class PlatformAdapter(ABC):
    platform: ClassVar[str]              # "github" | "gitlab"
    instance_host: str                   # "github.com" | "gitlab.epfl.ch"
    def classify(self, uri: str) -> NodeKind | None: ...
    def fetch(self, uri: str) -> Node: ...
    def expand(self, node: Node, opts: ExpandOpts) -> Iterable[Edge]: ...
    def normalize_uri(self, raw: str) -> str: ...
    def rate_limit_state(self) -> RateLimitInfo: ...
```

The BFS crawler resolves the adapter from each URL's host.

## 2. Data model

### 2.1 Subkind subclasses on top of v2 flat models

`BaseEntityModel` (already in v2) is extended:

```python
class BaseEntityModel(BaseModel):
    is_explored: bool = False
    exploration_timestamp: Optional[str] = None
    platform: str = "github"
    subkind: str                              # discriminator — set on each concrete subclass
    extras: dict[str, Any] = Field(default_factory=dict)
    external_identifiers: list[ExternalIdentifier] = Field(default_factory=list)
```

Existing concrete classes (`UserModel`, `OrgModel`, `RepoModel`, `TeamModel`) become the GitHub-shaped concrete classes. Their `subkind` field is set:

```python
class UserModel(BaseEntityModel):
    subkind: Literal["GitHubUser"] = "GitHubUser"
    # ... existing fields unchanged

class OrgModel(BaseEntityModel):
    subkind: Literal["GitHubOrganization"] = "GitHubOrganization"
    # ... existing fields unchanged

class RepoModel(BaseEntityModel):
    subkind: Literal["GitHubRepository"] = "GitHubRepository"
    # ... existing fields unchanged

class TeamModel(BaseEntityModel):
    subkind: Literal["GitHubTeam"] = "GitHubTeam"
    # ... existing fields unchanged
```

GitLab-specific subclasses:

```python
class GitLabUserModel(UserModel):
    subkind: Literal["GitLabUser"] = "GitLabUser"
    state: Literal["active", "blocked", "deactivated"] = "active"
    public_email: str = ""

class GitLabGroupModel(OrgModel):
    subkind: Literal["GitLabGroup"] = "GitLabGroup"
    parent: Optional[str] = None              # URL of parent group when subgroup
    visibility: Literal["private", "internal", "public"] = "public"

class GitLabProjectModel(RepoModel):
    subkind: Literal["GitLabProject"] = "GitLabProject"
    visibility: Literal["private", "internal", "public"] = "public"
    namespace: Optional[str] = None           # owner namespace URL
```

`GraphData.users`/`orgs`/`repos` remain `dict[str, UserModel]`/`dict[str, OrgModel]`/`dict[str, RepoModel]`. With Pydantic v2 discriminated unions on `subkind`, each dict holds the GitHub base class **or** the GitLab subclass interchangeably; JSON serialization round-trips through the right concrete class.

Future Spec 2/3 sketches (slot reserved, not implemented now):

```python
class ZenodoUserModel(UserModel):       subkind: Literal["ZenodoUser"]
class ZenodoCommunityModel(OrgModel):   subkind: Literal["ZenodoCommunity"]
class ZenodoRecordModel(RepoModel):     subkind: Literal["ZenodoRecord"]
class HuggingFaceUserModel(UserModel):  subkind: Literal["HuggingFaceUser"]
class HuggingFaceOrgModel(OrgModel):    subkind: Literal["HuggingFaceOrganization"]
class HuggingFaceModelModel(RepoModel): subkind: Literal["HuggingFaceModel"]
class HuggingFaceDatasetModel(RepoModel): subkind: Literal["HuggingFaceDataset"]
class HuggingFaceSpaceModel(RepoModel): subkind: Literal["HuggingFaceSpace"]
```

The user's earlier ask — "general but more specific" — maps directly: `OrgModel` is the abstract group bucket; `ZenodoCommunityModel`/`HuggingFaceOrgModel`/`GitLabGroupModel` are the concrete subkinds.

### 2.2 GraphData additions (additive)

```python
class GraphData(BaseModel):
    schema_version: int = GRAPH_SCHEMA_VERSION   # bumps to 3
    users: dict[str, UserModel] = {}             # accepts GitHubUser or GitLabUser
    orgs:  dict[str, OrgModel] = {}              # accepts GitHubOrganization or GitLabGroup
    repos: dict[str, RepoModel] = {}             # accepts GitHubRepository or GitLabProject
    teams: dict[str, TeamModel] = {}             # GitHub-only in v1

    # new convenience accessors (additive):
    def of_subkind(self, subkind: str) -> Iterator[BaseEntityModel]: ...
    def by_platform(self, platform: str) -> Iterator[BaseEntityModel]: ...
    def by_instance(self, instance_host: str) -> Iterator[BaseEntityModel]: ...
```

`schema_version` bumps to **3**. v2 snapshots are refused on load with: *"snapshot schema is from v2.x; please start a fresh crawl"*.

### 2.3 URL normalization

`node_id.canonical_url` already handles scheme/host lowercasing and trailing-slash stripping. The new per-adapter `normalize_uri()` adds:

- Per-platform login casing canonicalization (resolve via API call, cache the mapping). Without this, `https://github.com/Torvalds` and `.../torvalds` would be distinct keys.
- Per-adapter `classify(uri) → NodeKind | None` walks the path.

## 3. Platform adapters

### 3.1 `PlatformAdapter` ABC (`platforms/base.py`)

```python
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
```

Adapters silently ignore options that don't apply (GitLab is a no-op for `crawl_dependents`).

### 3.2 GitLab adapter

Uses [python-gitlab](https://python-gitlab.readthedocs.io). One adapter per configured host with its own token pool.

| Concept | Endpoint(s) | Stored / edges emitted |
|---|---|---|
| User fetch | `GET /users/:id_or_username` | `GitLabUserModel` (state, public_email, native id) |
| User expand | `users/:id/projects`, `users/:id/contributed_projects`, `users/:id/starred_projects` | `authored_repositories`, `starred_repositories`; contributed projects are *enqueued* (contribution edge is recorded on `RepoModel.contributors`, matching the GitHub model). |
| Group fetch | `GET /groups/:full_path` | `GitLabGroupModel` (parent if subgroup, visibility) |
| Group expand | `groups/:id/members/all` (inherited), `groups/:id/subgroups`, `groups/:id/projects?include_subgroups=false` | `members`, child→parent subgroup edges, `authored_repositories` |
| Project fetch | `GET /projects/:url_encoded_path` | `GitLabProjectModel` (visibility, namespace, forked_from, stargazer_count) |
| Project expand | `projects/:id/repository/contributors`, `projects/:id/forks`, `projects/:id/issues` (`crawl_issues`), `projects/:id/merge_requests` (`crawl_prs`), `projects/:id/starrers` (`crawl_stars`) | `contributors`, fork children, `issue_authors`, `pr_authors`, `pr_reviewers`, `commenters`, stargazer users |

Quirks and resolutions:

1. **User-vs-group at top-level path.** Probe `/users?username=foo` then `/groups/foo`. Cache the result. Yields `GitLabUser` or `GitLabGroup`.
2. **Nested subgroup vs project.** `https://gitlab.epfl.ch/a/b/c` is either subgroup `a/b/c` or project `a/b/c`. Try `/projects/{url-encoded path}` first; on 404, treat as group path. Cache.
3. **Multi-instance auth.** Each adapter holds its own token pool. Rate-limit state from python-gitlab's response headers.
4. **No "follow user" concept.** `UserModel.followers`/`following` stay empty for `GitLabUserModel`.
5. **Star semantics.** `stargazer_count` always captured; per-user star enumeration gated behind `--crawl-stars` (off by default).
6. **Self-hosted older versions** (`gitlab.epfl.ch`, `gitlab.ethz.ch`, `renkulab.io`). Optional endpoints (e.g., `starrers` from GitLab 13.5+) return no edges with a debug log on 404 rather than failing.

## 4. Crawler

The current `GitHubCrawler` becomes `Crawler` (or stays named for compat — the implementation plan picks the safer path: keep the class name, add a `registry: PlatformRegistry` constructor arg, dispatch by host). The existing concurrency model (ThreadPoolExecutor + semaphore + pause/cancel) is preserved.

Per-batch loop:

```python
adapter = registry.adapter_for(uri)
node = adapter.fetch(uri)
graph.add(node)
for edge in adapter.expand(node, opts):
    enqueue_if_new(edge.dst if edge.src == node.url else edge.src)
    record_edge(edge)
```

## 5. Configuration (`config.py`)

```bash
# Enable instances (comma-separated). Hosts not listed are not crawled.
CRAWLER_PLATFORMS="github.com,gitlab.epfl.ch,gitlab.ethz.ch,renkulab.io"

# Per-host token pool. Env-var transform: lowercase host → replace dots/hyphens with underscores → uppercase.
CRAWLER_TOKEN_POOL__GITHUB_COM="ghp_a,ghp_b"
CRAWLER_TOKEN_POOL__GITLAB_EPFL_CH="glpat-x,glpat-y"
CRAWLER_TOKEN_POOL__GITLAB_ETHZ_CH="glpat-z"
CRAWLER_TOKEN_POOL__RENKULAB_IO="glpat-r"

# Single-token shorthand (pool wins if both set):
CRAWLER_TOKEN__GITHUB_COM="ghp_x"
```

**Legacy compatibility (v3 only):**

- `CRAWLER_GITHUB_TOKEN_POOL` → mapped to `host=github.com`, emits one-shot DeprecationWarning.
- `CRAWLER_GITHUB_TOKEN` → same.
- `GITHUB_TOKEN` → same (continues from its v2 state).
- All three drop in v4.

`token_env.py` is preserved as a thin wrapper that calls into `config.py` for `github.com`. The env-var-name transform is a single helper in `config.py` so it can't drift across adapters.

## 6. REST API

### 6.1 `/api/v2` (new)

- `POST /api/v2/crawl` — body accepts `seeds: list[str]` (URLs, `owner/repo`, or bare logins resolved against `--default-host`). Returns URL-keyed graph in the unified shape.
- `GET /api/v2/graph/{job_id}` — unified shape.
- `GET /api/v2/nodes?subkind=GitLabProject&instance=gitlab.epfl.ch` — filtered listing.
- `GET /api/v2/health`.
- `GET /api/v2/platforms` — lists enabled instances + per-host token health.

### 6.2 `/api/v1` (compat shim)

1. Reject non-GitHub seeds early with `400 {"error": "v1 supports github.com seeds only; use /api/v2"}`.
2. Run crawl on the unified internals.
3. Return existing v2.0.0 response shape (`users/orgs/repos/teams` dicts of URL-keyed nodes). `subkind` field is **omitted** from v1 response for byte-identity with the v2.0.0 contract.

## 7. CLI

The existing `crawl` command keeps its surface:

- Seeds accept the same forms; URLs route by host. Bare logins and `owner/repo` default to `github.com` unless `--default-host gitlab.epfl.ch` is passed.
- `--platforms github.com,gitlab.epfl.ch` overrides `CRAWLER_PLATFORMS` for ad-hoc runs.
- New `--crawl-stars` flag (off by default).
- New `crawler doctor` subcommand: prints enabled hosts, configured token counts (never values), and a one-call health check per pool.

## 8. Caching, state, migration

- Cache directory layout: `cache/<host>/<sha256(uri)>.json`. Per-host isolation.
- `OPC_CACHE_TTL_DAYS` (default 30) unchanged.
- v2 cache and state files are not migrated. Operators re-crawl.
- `GRAPH_SCHEMA_VERSION` bumps to **3**; v2 snapshots refused with a clear message.

## 9. Testing

| Suite | Coverage |
|---|---|
| `tests/platforms/test_github_adapter.py` | Mock the existing GitHub client; assert `fetch()` returns the right model and `expand()` emits the right edges. |
| `tests/platforms/test_gitlab_adapter.py` | Mock python-gitlab; cover user-vs-group, subgroup-vs-project, missing `/starrers` endpoint, stars-gating. |
| `tests/test_config.py` | env-var transform + legacy fallback + deprecation warning. |
| `tests/test_crawler.py` | Migrated to drive BFS via `FakePlatformAdapter`. Same behaviour assertions. |
| `tests/test_api_v1_compat.py` | Byte-identical v2.0.0 shape for github-only crawls; 400 for non-github seeds. |
| `tests/test_api_v2.py` | New shape, filtered listings, `platforms` endpoint. |
| `tests/integration/test_gitlab_dryrun.py` | Tiny real crawl against `gitlab.com` with a public token; skipped when no token in env. |
| Migration sanity | v2 snapshot is rejected with the expected message. |

Self-hosted GitLab instances are **not** crawled in CI. Manual-test recipes go in `docs/GITLAB.md`.

## 10. Effort estimate

| Block | Effort |
|---|---|
| (a) `PlatformAdapter` ABC + subkind subclasses + config + GitHub port | ~0.5-1 day |
| (b) GitLab adapter + python-gitlab + disambiguation + multi-instance auth | ~1-2 days |
| (c) `/api/v2` + v1 host-guard + CLI changes + `crawler doctor` | ~0.5 day |
| (d) Tests + `docs/GITLAB.md` + CHANGELOG | ~0.5 day |
| **Total** | **~2.5-4 days** |

## 11. Open questions for the implementation plan

- python-gitlab vs current thread-pool: use sync API + thread pool for parity with REST client (matches the existing concurrency model).
- Disambiguation-probe cache: in-memory per adapter instance for now (`_kind_cache`). If repeated runs make probes expensive, persist under `cache/<host>/_disambig/<sha256(path)>.json` later.
- `crawler doctor` output: plain text by default; `--json` flag for machine-readable.
