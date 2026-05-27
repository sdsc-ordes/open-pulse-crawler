# Multi-platform crawler — abstraction + GitLab (Spec 1)

**Status:** Draft for review
**Date:** 2026-05-27
**Branch:** `feat/multi-platform-gitlab` (from `origin/develop`)
**Worktree:** `.worktrees/feat-multi-platform-gitlab/`
**Sequence:** Spec 1 of 3 (this) → Spec 2 (Zenodo) → Spec 3 (HuggingFace)

## Goal

Turn Open Pulse Crawler from a GitHub-only BFS crawler into a multi-platform
crawler whose primary downstream use is a **cross-platform open-science graph**.
Spec 1 ships:

1. A `PlatformAdapter` abstraction with a generic, platform-agnostic BFS engine.
2. The existing GitHub code, ported to the new abstraction with no behaviour
   change (modulo the data-model rename).
3. A `GitLabAdapter` supporting users, groups (incl. nested subgroups),
   projects, ownership/membership/contributors, issue/MR activity, fork
   parent/child, and project stars — across `gitlab.com`, `gitlab.epfl.ch`,
   `gitlab.ethz.ch`, and `renkulab.io`.

## Non-goals (deferred)

- **Zenodo adapter** — Spec 2 (separate brainstorming session).
- **HuggingFace adapter** — Spec 3.
- **Cross-platform identity resolution.** The data model carries a slot
  (`external_identifiers` on every node), but no resolver ships here.
- **GitLab SBOM / "used by".** No public GitLab equivalent of GitHub's
  dependents view. Skipped rather than faked.
- **Renku-specific concepts** (Renku datasets, project lineage). `renkulab.io`
  is treated as a vanilla GitLab instance in v1.
- **A real-time graph store / DB.** Output stays JSON + CSV + matplotlib.

## Locked-in design choices

| Topic | Decision |
|---|---|
| Use case | Cross-platform open-science graph (identities will eventually link). |
| Sequencing | Abstraction + GitLab first; Zenodo next; HuggingFace last. |
| Node identity | Canonical HTTPS URL is the primary key and JSON-LD `@id`. |
| Configuration | Env vars keyed by instance host. |
| Architecture | Approach A — strangler-fig refactor with `PlatformAdapter` ABC. |
| API compatibility | Ship `/api/v2` (unified shape); keep `/api/v1` as a compat shim for GitHub-only crawls; two-release deprecation window. |
| Migration | Hard break — snapshot/cache schema version bumps; operators re-crawl. No migration script. |

## 1. Architecture & module layout

```
src/open_pulse_crawler/
  platforms/
    __init__.py          # PlatformAdapter registry (host → adapter)
    base.py              # PlatformAdapter ABC + supporting types
    github/
      adapter.py         # implements PlatformAdapter using existing PyGithub client
      client.py          # = current github_client.py, narrowed
      graphql.py         # = current graphql_client.py
    gitlab/
      adapter.py         # implements PlatformAdapter using python-gitlab
      client.py          # rate-limit-aware wrapper around python-gitlab
  models.py              # unified Pydantic models (URL-keyed, kind+subkind hierarchy)
  crawler.py             # platform-agnostic BFS driver; talks only to adapters
  uris.py                # URL normalization + classification helpers
  config.py              # NEW: instance & token resolution per host
  api/
    v1.py                # legacy shape compat shim (renamed from current api.py)
    v2.py                # new unified shape
    deps.py              # shared dependencies (auth, job store)
```

`dependency_utils.py`, `gimie_*`, `io_utils.py`, `visualization.py`, `gui.py`
stay where they are but consume URL-keyed nodes.

`PlatformAdapter` sketch (full method shapes are in §3):

```python
class PlatformAdapter(ABC):
    platform: ClassVar[str]              # "github" | "gitlab"
    instance_host: str                   # "github.com" | "gitlab.epfl.ch"
    def classify(self, uri: str) -> NodeKind: ...
    def fetch(self, uri: str) -> Node: ...
    def expand(self, node: Node, opts: ExpandOpts) -> Iterable[Edge]: ...
    def normalize_uri(self, raw: str) -> str: ...
    def rate_limit_state(self) -> RateLimitInfo: ...
```

A `PlatformRegistry` maps `instance_host → adapter`. The BFS crawler resolves
the adapter from each node's URL host.

## 2. Data model

Every node carries two type fields:

- `kind` — the **abstract bucket** the BFS engine reasons over: `Person`,
  `Group`, `Repository`.
- `subkind` — the **concrete platform-specific type**: `GitHubUser`,
  `GitHubOrganization`, `GitHubRepository`, `GitLabUser`, `GitLabGroup`,
  `GitLabProject`. Future: `ZenodoUser`/`Community`/`Record`,
  `HuggingFaceUser`/`Organization`/`Model`/`Dataset`/`Space`.

The graph stores subclass instances; Pydantic v2 discriminated unions (on
`subkind`) reconstruct the right subclass from JSON. Downstream code can
filter abstractly (`graph.of_kind(NodeKind.REPOSITORY)`) or concretely
(`graph.of_subkind("GitLabProject")`).

**No `Team` type.** GitHub Teams are not consumed downstream; the
team-crawling code paths are removed during the refactor.

### 2.1 Class hierarchy (Spec 1: GitHub + GitLab)

```python
class NodeKind(str, Enum):
    PERSON     = "Person"
    GROUP      = "Group"
    REPOSITORY = "Repository"

class Node(BaseModel):
    id: HttpUrl                       # canonical URL — primary key
    kind: NodeKind                    # ClassVar on each subclass
    subkind: str                      # ClassVar (Literal) on each subclass — Pydantic discriminator
    platform: str                     # "github" | "gitlab"
    instance: str                     # "github.com" | "gitlab.epfl.ch" | ...
    name: str = ""
    native_id: str = ""               # numeric id or slug from the source platform
    is_explored: bool = False
    exploration_timestamp: str | None = None
    external_identifiers: list[ExternalIdentifier] = []   # slot for future cross-platform linking
    extras: dict[str, Any] = {}       # escape hatch for fields we deliberately don't model

# --- Person ---
class Person(Node):
    kind: ClassVar[NodeKind] = NodeKind.PERSON
    followers: list[HttpUrl] = []
    following: list[HttpUrl] = []
    starred_repositories: list[HttpUrl] = []
    authored_repositories: list[HttpUrl] = []
    forked_repositories: list[HttpUrl] = []

class GitHubUser(Person):
    subkind: Literal["GitHubUser"] = "GitHubUser"
    type: Literal["User", "Bot"] = "User"
    watched_repositories: list[HttpUrl] = []      # GitHub-only

class GitLabUser(Person):
    subkind: Literal["GitLabUser"] = "GitLabUser"
    state: Literal["active", "blocked", "deactivated"] = "active"
    public_email: str = ""

# --- Group (GitHub Org, GitLab Group, future Zenodo Community / HF Org) ---
class Group(Node):
    kind: ClassVar[NodeKind] = NodeKind.GROUP
    members: list[HttpUrl] = []
    authored_repositories: list[HttpUrl] = []

class GitHubOrganization(Group):
    subkind: Literal["GitHubOrganization"] = "GitHubOrganization"
    forked_repositories: list[HttpUrl] = []

class GitLabGroup(Group):
    subkind: Literal["GitLabGroup"] = "GitLabGroup"
    parent: HttpUrl | None = None         # set for subgroups (URL of parent group)
    visibility: Literal["private", "internal", "public"] = "public"

# --- Repository ---
class Repository(Node):
    kind: ClassVar[NodeKind] = NodeKind.REPOSITORY
    owner: HttpUrl | None = None          # Person or Group URL
    contributors: list[HttpUrl] = []
    contributor_count: int | None = None
    is_fork: bool = False
    forked_from: HttpUrl | None = None
    issue_authors: list[HttpUrl] = []
    pr_authors: list[HttpUrl] = []        # GitLab MR authors land here too
    commenters: list[HttpUrl] = []
    pr_reviewers: list[HttpUrl] = []      # GitLab MR reviewers land here too
    stargazer_count: int | None = None

class GitHubRepository(Repository):
    subkind: Literal["GitHubRepository"] = "GitHubRepository"
    dependents: list[HttpUrl] = []
    dependencies: list[HttpUrl] = []

class GitLabProject(Repository):
    subkind: Literal["GitLabProject"] = "GitLabProject"
    visibility: Literal["private", "internal", "public"] = "public"
    namespace: HttpUrl | None = None      # owner namespace (a user or a group)
```

### 2.2 GraphData

```python
class GraphData(BaseModel):
    nodes: dict[HttpUrl, Node] = {}       # discriminated by subkind on (de)serialize

    def of_kind(self, kind: NodeKind) -> Iterator[Node]: ...
    def of_subkind(self, subkind: str) -> Iterator[Node]: ...
    def by_platform(self, platform: str) -> Iterator[Node]: ...
    def by_instance(self, instance_host: str) -> Iterator[Node]: ...

    # Deprecated legacy accessors (filtered iterators, DeprecationWarning):
    def users(self):  ...   # of_kind(PERSON)
    def orgs(self):   ...   # of_kind(GROUP), platform="github"
    def repos(self):  ...   # of_kind(REPOSITORY)
```

`teams` is not provided — `Team` is gone from the model.

### 2.3 Edges

The BFS engine emits explicit `Edge(src_uri, kind, dst_uri)` tuples via
`adapter.expand()`. Per-node lists (`Repository.contributors`,
`Group.members`, etc.) are also populated for back-compat with the current
JSON shape. CSV export gains `source_id` / `target_id` URL columns and an
`edge_kind` column.

### 2.4 URL normalization (`uris.py`)

1. Lowercase scheme + host. Reject non-`https`.
2. Strip trailing slash, fragments, query strings.
3. Path case is normalized **per adapter** (handled by `normalize_uri()`),
   never by the generic normalizer. Both GitHub and GitLab logins are
   case-insensitive in lookup but case-preserving in display; the adapter
   resolves to the canonical case by API response, then caches that
   mapping so subsequent inputs round-trip without an extra call.
   Without this, `https://github.com/Torvalds` and `.../torvalds` would
   become two distinct graph nodes for the same user.
4. Per-adapter `classify(uri) → NodeKind`. The registry picks the adapter by host;
   the adapter inspects the path to decide kind.
5. GitHub forms: `https://github.com/<owner>` (User/Org), `https://github.com/<owner>/<repo>`.
6. GitLab forms: `https://<host>/<full_path>` covers users, groups, subgroups,
   and projects. Disambiguation is via API probing (see §3.2).

## 3. Platform adapters

### 3.1 PlatformAdapter ABC (`platforms/base.py`)

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
    src: HttpUrl
    kind: str            # "contributor_of", "member_of", "forked_from", ...
    dst: HttpUrl

class PlatformAdapter(ABC):
    platform: ClassVar[str]
    instance_host: str

    @abstractmethod
    def classify(self, uri: str) -> NodeKind: ...
    @abstractmethod
    def fetch(self, uri: str) -> Node: ...
    @abstractmethod
    def expand(self, node: Node, opts: ExpandOpts) -> Iterable[Edge]: ...
    @abstractmethod
    def normalize_uri(self, raw: str) -> str: ...
    @abstractmethod
    def rate_limit_state(self) -> RateLimitInfo: ...
```

Adapters silently ignore options that don't apply to their platform (the GitLab
adapter is a no-op for `crawl_dependents`).

### 3.2 GitLab adapter

Uses [python-gitlab](https://python-gitlab.readthedocs.io). One adapter
instance per configured instance host, each holding its own multi-token pool.

| Concept | Endpoint(s) | Stored on node / edges emitted |
|---|---|---|
| User fetch | `GET /users/:id_or_username` | `GitLabUser` (state, public_email, native_id) |
| User expand | `users/:id/projects`, `users/:id/contributed_projects`, `users/:id/starred_projects` | `Person.authored_repositories`, `Person.starred_repositories`; contributed projects are *enqueued* (no per-Person field — the contribution edge is recorded on `Repository.contributors`, matching the GitHub model). |
| Group fetch | `GET /groups/:full_path` | `GitLabGroup` (parent if subgroup, visibility, native_id) |
| Group expand | `groups/:id/members/all` (inherited), `groups/:id/subgroups`, `groups/:id/projects?include_subgroups=false` | `members`, child→parent edges, `authored_repositories` |
| Project fetch | `GET /projects/:url_encoded_path` | `GitLabProject` (visibility, namespace, forked_from, stargazer_count, native_id) |
| Project expand | `projects/:id/repository/contributors`, `projects/:id/forks`, `projects/:id/issues` (`crawl_issues`), `projects/:id/merge_requests` (`crawl_prs`), `projects/:id/starrers` (`crawl_stars`) | `contributors`, fork children, `issue_authors`, `pr_authors`, `pr_reviewers`, `commenters`, stargazer users |

#### GitLab-specific quirks and resolutions

1. **User-vs-group disambiguation at top-level path.** `https://gitlab.epfl.ch/foo`
   could be either. The adapter probes `/users?username=foo` first, then
   `/groups/foo`. Result is cached so a second visit is free. Yields
   `GitLabUser` or `GitLabGroup`.
2. **Nested-subgroup vs project ambiguity.** `https://gitlab.epfl.ch/a/b/c`
   could be subgroup `a/b/c` *or* project `a/b/c`. Try
   `/projects/{url-encoded path}` first; on 404, treat as group path. Cache.
3. **Multi-instance auth.** Each instance has its own token pool. Rate limit
   state is per-instance (GitLab's rate limit headers; same rotation model as
   the GitHub side).
4. **No "follow user" concept.** `Person.followers`/`following` stay empty for
   `GitLabUser`. Already typed as empty default.
5. **Star semantics.** `stargazer_count` is always captured. Enumerating
   *which users* starred a project is gated behind `--crawl-stars` (off by
   default) — mirrors the GitHub trade-off.
6. **Self-hosted instances** (`gitlab.epfl.ch`, `gitlab.ethz.ch`, `renkulab.io`)
   may run older GitLab versions with subtly different endpoints. The adapter
   handles `404`s on optional endpoints (e.g., `starrers` exists from GitLab
   13.5+) by emitting no edges and logging at debug.

## 4. Crawler (`crawler.py`)

The BFS driver stops knowing GitHub. It owns:

- URL-keyed queue and visited set
- Round counters and per-round summaries
- Per-batch concurrency (the existing `ThreadPoolExecutor` / semaphore model)
- The pause/cancel flags
- Progress reporting (tqdm + structured stats)

Per-batch loop becomes:

```python
adapter = registry.adapter_for(uri)
node = adapter.fetch(uri)
with graph_lock:
    graph.nodes[node.id] = node
for edge in adapter.expand(node, opts):
    with visited_lock:
        if edge.dst not in visited:
            enqueue(edge.dst)
    record_edge(edge)
```

Rate-limit handling is adapter-local. The crawler only reads
`adapter.rate_limit_state()` for progress reporting.

## 5. Configuration (`config.py`)

```bash
# Enable instances (comma-separated). Hosts not listed here are not crawled.
CRAWLER_PLATFORMS="github.com,gitlab.epfl.ch,gitlab.ethz.ch,renkulab.io"

# Per-host token pool (multi-token rotation). Env-var transform:
# lowercase the host, replace dots/hyphens with underscores, uppercase.
CRAWLER_TOKEN_POOL__GITHUB_COM="ghp_a,ghp_b"
CRAWLER_TOKEN_POOL__GITLAB_EPFL_CH="glpat-x,glpat-y"
CRAWLER_TOKEN_POOL__GITLAB_ETHZ_CH="glpat-z"
CRAWLER_TOKEN_POOL__RENKULAB_IO="glpat-r"

# Single-token shorthand (pool wins if both set):
CRAWLER_TOKEN__GITHUB_COM="ghp_x"
```

**Legacy compatibility.** `CRAWLER_GITHUB_TOKEN_POOL`, `CRAWLER_GITHUB_TOKEN`,
and `GITHUB_TOKEN` remain readable for two minor releases — mapped to
`host=github.com` with a deprecation warning.

The host→env-var transform is a single helper in `config.py` so it can't drift
across adapters.

## 6. REST API

### 6.1 `/api/v2` (new — unified shape)

- `POST /api/v2/crawl` — body accepts `seeds: list[str]` (URLs, `owner/repo`,
  or bare logins resolved against `--default-host`). Returns the URL-keyed
  graph in the unified shape.
- `GET /api/v2/graph/{job_id}` — unified shape.
- `GET /api/v2/nodes?kind=Repository&platform=gitlab&instance=gitlab.epfl.ch` —
  filtered listing.
- `GET /api/v2/health` — health check.
- `GET /api/v2/platforms` — lists enabled instances and per-host token health.

### 6.2 `/api/v1` (compat shim)

1. Detects non-GitHub seeds early →
   `400 {"error": "v1 supports github.com seeds only; use /api/v2"}`.
2. Runs the crawl on the unified internals.
3. Adapts the response to the legacy `{users, orgs, repos, teams}` shape by
   filtering on `kind`. `teams` is returned as `{}` for two releases, then the
   field is removed.

## 7. CLI

The existing `crawl` command keeps its surface area. Changes:

- Seeds accept the same forms as before; URLs are routed by host. Bare logins
  and `owner/repo` default to `github.com` unless `--default-host
  gitlab.epfl.ch` is passed.
- `--platforms github.com,gitlab.epfl.ch` overrides `CRAWLER_PLATFORMS` for
  ad-hoc runs.
- New `--crawl-stars` flag (off by default) for stargazer-user crawling on
  both platforms.
- New `crawler doctor` subcommand: prints enabled hosts, configured token
  counts (never values), and a per-pool token health check (one cheap API
  call each).

## 8. Caching, state, and migration

- Cache directory layout: `cache/<host>/<sha256(uri)>.json`. Per-host
  isolation; same URI on two instances is distinct.
- Cache TTL machinery (`OPC_CACHE_TTL_DAYS`, default 30 days) is unchanged.
- Old GitHub-shaped cache is **not** migrated. Operators re-crawl.
- State file (`--state-file`) bumps its schema version. Resuming an old state
  file errors with: *"snapshot schema is from an earlier release; please
  start a fresh crawl"*.

## 9. Testing

| Suite | What it covers |
|---|---|
| `tests/platforms/test_github_adapter.py` | Mock PyGithub; assert `fetch()` returns the right subclass; `expand()` emits the right edges for canned API responses. |
| `tests/platforms/test_gitlab_adapter.py` | Mock python-gitlab; cover normal cases + user-vs-group disambiguation + subgroup-vs-project disambiguation + missing `starrers` endpoint. |
| `tests/test_uris.py` | Table-driven URI normalization + classification across both adapters. |
| `tests/test_crawler.py` | Migrated to drive BFS via a `FakePlatformAdapter`. Same behaviour assertions as today. |
| `tests/test_api_v1_compat.py` | Snapshot-tests v1 byte-identical against a golden file for GitHub-only crawls; asserts 400 on non-GitHub seeds. |
| `tests/test_api_v2.py` | Covers the new shape, filtered listings, and `platforms` endpoint. |
| `tests/integration/test_gitlab_dryrun.py` | Tiny real crawl against `gitlab.com` with a public throwaway token; skipped when no token in env. |
| Migration sanity | Test that a v0 cache/state file is rejected with the expected message rather than silently loaded. |

Self-hosted GitLab instances (`gitlab.epfl.ch`, `gitlab.ethz.ch`,
`renkulab.io`) are **not** crawled in CI — they need institutional tokens.
Manual-test recipes go in `docs/GITLAB.md`.

## 10. Effort estimate

Rough sizing for the implementation plan that follows:

| Block | Effort |
|---|---|
| (a) `PlatformAdapter` ABC + GitHub port (mechanical: rename + dict→subclass) | ~1 day |
| (b) GitLab adapter + python-gitlab integration + disambiguation cases + multi-instance auth | ~1-2 days |
| (c) `/api/v2` + v1 compat shim + CLI changes + `crawler doctor` | ~0.5 day |
| (d) Tests + `docs/GITLAB.md` + CHANGELOG | ~0.5-1 day |
| **Total** | **~3-4 days** of focused work |

## 11. Open questions for the implementation plan

These are non-blocking for the spec but worth flagging for `writing-plans`:

- Does `python-gitlab` async support match our existing thread-pool model, or
  do we need a thin sync wrapper? (Affects the rate-limit semaphore design.)
- Cache key for the user-vs-group disambiguation probe — store as a separate
  `kind` cache file per host (e.g., `cache/<host>/_disambig/<sha256(path)>.json`)
  or fold into the normal cache?
- `crawler doctor`'s output format: plain text vs. JSON-when-piped?
