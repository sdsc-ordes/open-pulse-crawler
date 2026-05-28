# Zenodo adapter (Spec 2, targets v3.1.0)

**Status:** Draft for review
**Date:** 2026-05-28
**Branch:** `feat/multi-platform-gitlab` (same branch v3 multi-platform work lives on)
**Worktree:** `.worktrees/feat-multi-platform-gitlab/`
**Sequence:** Spec 2 of 3 — GitLab (Spec 1, shipped) → **Zenodo (this)** → HuggingFace (Spec 3)

## Starting point: what Spec 1 already shipped

The platform abstraction is in place: `PlatformAdapter` ABC, `PlatformRegistry` (host-keyed dispatch), URL-keyed nodes, `subkind` discriminator on `models.py`, host-keyed env-var config (`CRAWLER_TOKEN__<HOST>`), per-host cache layout (`cache/<host>/<sha>.json`), dual-path crawler dispatch (github.com stays on legacy `_process_*`; everything else uses the adapter path), `/api/v2` REST surface, CLI `--platforms` / `--default-host`, anonymous-friendly clients with graceful 401/403 degradation.

Spec 2 slots in as a third platform without changing any of that — the seam was designed for exactly this.

## Goal

Crawl Zenodo records, communities, and uploader accounts as part of the unified open-science graph. Each Zenodo record's `metadata.related_identifiers` (pointing at GitHub repos, papers, etc.) becomes a `related_to.<RelationType>` edge so the BFS engine discovers cross-platform links automatically.

## Non-goals (explicitly deferred)

- **Cross-platform identity resolution.** Locked: a separate tool owns that layer. The Zenodo adapter populates `extras.creators` on records as raw `{name, orcid, affiliation}` dicts — not Person nodes.
- **Community members crawling.** `/api/communities/<slug>/members` is auth-gated on production Zenodo and adds complexity for negligible return; skip entirely. No `member_of` (user → community) edge.
- **Per-version record nodes.** The concept DOI is the primary identity; the version chain is intra-node metadata (`versions: list[dict]`). No `version_of` edge.
- **Deposit / upload / draft flows.** Read-only adapter.
- **ORCID resolution.** Stored verbatim in `extras` / `creators`; no API lookups.
- **HuggingFace adapter** — Spec 3.
- **Dual-path crawler unification** (collapsing the GitHub legacy `_process_*` into `GitHubAdapter`). Not blocking Spec 2.

## Locked-in design choices

| Topic | Decision |
|---|---|
| Entity scope | Three subkinds: `ZenodoUser`, `ZenodoCommunity`, `ZenodoRecord`. |
| Identity unit | One node per concept DOI. Specific versions collapse into the concept node's `versions: list[dict]` metadata. |
| Seed shapes | Canonical Zenodo URLs (`https://zenodo.org/records/<id>`, `/communities/<slug>`, `/users/<id>`); DOI URLs (`https://doi.org/10.5281/zenodo.<id>` prod, `10.5072/zenodo.<id>` sandbox) rewritten to canonical form via regex (no HTTP). |
| Multi-instance | `zenodo.org` + `sandbox.zenodo.org` via host-keyed `CRAWLER_PLATFORMS` and `CRAWLER_TOKEN__<HOST>` / `CRAWLER_TOKEN_POOL__<HOST>`. |
| Architecture | Approach A — plain `httpx` `ZenodoClient` + `ZenodoAdapter`, mirroring the `GitLabClient`/`GitLabAdapter` pattern. No third-party Zenodo SDK. |
| Token auth | `Authorization: Bearer <tok>` header. Anonymous when no token configured for a host. |
| Members | Skipped (no edges, no client method). |
| Related identifiers | Emit `related_to.<RelationType>` edge for every entry resolvable to an `https://` URL. BFS routes by host; unregistered hosts skip with a debug log. |

## 1. Architecture & module layout

```
src/open_pulse_crawler/
  platforms/
    zenodo/                          # NEW
      __init__.py                    # re-exports ZenodoClient
      client.py                      # httpx wrapper (~150 LoC, 6 endpoints + cache hookup)
      adapter.py                     # ZenodoAdapter(PlatformAdapter) (~250 LoC)
  models.py                          # EXTENDED — three Zenodo subkinds
  node_id.py                         # EXTENDED — DOI URL → canonical Zenodo URL rewriter
  cli.py                             # EXTENDED — _build_registry routes zenodo.org / sandbox.zenodo.org
tests/
  platforms/
    test_zenodo_client.py            # NEW — mocked httpx
    test_zenodo_adapter.py           # NEW — classify/fetch/normalize/expand
  integration/
    test_zenodo_dryrun.py            # NEW — tiny live zenodo.org crawl, anonymous, optional skip
tools/scripts/
  fetch_public_projects.py           # MINOR EDIT — host-dispatch GitLab vs Zenodo listing endpoints
docs/
  ZENODO.md                          # NEW
src/open_pulse_crawler/api/
  v2.py                              # EXTENDED — OpenAPI examples for Zenodo seeds
CHANGELOG.md / README.md             # EXTENDED
```

`crawler.py` is unchanged — the host-check dispatch from Spec 1 routes all non-github.com URIs through `_process_one_via_adapter` already.

## 2. Data model

Three new Pydantic subclasses on top of v2 flat models. All inherit the discriminated-union machinery so they slot into existing `GraphData.users` / `orgs` / `repos` dicts without schema changes to `GraphData`.

### 2.1 `ZenodoUserModel(UserModel)`

Represents a Zenodo platform account (uploader). Distinct from "creator" — creators are name strings on records, not Zenodo accounts.

```python
class ZenodoUserModel(UserModel):
    subkind: Literal["ZenodoUser"] = "ZenodoUser"
    orcid: str | None = None
    affiliation: str = ""
```

- `UserModel.login` = Zenodo username (display handle when present, else stringified numeric ID).
- `UserModel.id` = numeric Zenodo user ID.
- URL form: `https://zenodo.org/users/<id>`.
- Social fields (`followers`/`following`/`starred_repositories`/`watched_repositories`) stay empty by construction — **Zenodo has no social graph**; the adapter emits no edges that populate them. Documented in the class docstring so the empty state isn't read as a bug.

### 2.2 `ZenodoCommunityModel(OrgModel)`

```python
class ZenodoCommunityModel(OrgModel):
    subkind: Literal["ZenodoCommunity"] = "ZenodoCommunity"
    doi: str | None = None
    description: str = ""
    community_type: str = ""    # "project" | "organization" | "event" | "topic"
```

- `OrgModel.login` = community slug (e.g., `"sdsc-ordes"`).
- URL form: `https://zenodo.org/communities/<slug>`.
- `OrgModel.members` stays empty — members are NOT crawled (gated endpoint, out of scope).

### 2.3 `ZenodoRecordModel(RepoModel)`

Represents a **concept** record (the canonical, version-agnostic identity). Versions are flattened into `versions: list[dict]`.

```python
class ZenodoRecordModel(RepoModel):
    subkind: Literal["ZenodoRecord"] = "ZenodoRecord"
    doi: str                           # the concept DOI (or the record's own DOI when no versioning)
    concept_doi: str                   # always populated; same as `doi` for records without versioning
    latest_version: str = ""           # latest version identifier ("1.0.0", "v2", or "")
    latest_version_doi: str = ""       # latest version DOI
    latest_version_url: str = ""       # canonical Zenodo URL of the latest version's record id
    versions: list[dict[str, Any]] = []  # [{doi, url, version, publication_date, record_id}, …]
    resource_type: str = ""            # "software" | "dataset" | "publication-article" | …
    publication_date: str = ""         # ISO YYYY-MM-DD of the latest version
    title: str = ""                    # record title (independent of RepoModel.name)
    creators: list[dict[str, Any]] = []  # [{name, orcid, affiliation, type}, …]
    keywords: list[str] = []
    license: str = ""                  # SPDX-like identifier, e.g. "CC-BY-4.0"
    access_right: Literal["open", "embargoed", "restricted", "closed"] = "open"
```

- `RepoModel.full_name` = the concept DOI (e.g., `"10.5281/zenodo.12345"`). Natural citation identifier; parallel to GitHub's `owner/repo` and GitLab's `path_with_namespace`.
- URL form: `https://zenodo.org/records/<concept_record_id>`.
- `RepoModel.forked_from` / `is_fork` / `dependents` / `dependencies` stay at defaults — Zenodo has no fork or dependency concept.
- **Why `creators` is `list[dict]` not crawled to User nodes:** identity resolution is out of scope. Creators are author names with optional ORCIDs, not Zenodo accounts. Embedding matches what callers need for citation/display without inflating the graph with un-resolveable shell nodes.

### 2.4 Edge kinds

| Source | Kind | Target |
|---|---|---|
| `ZenodoRecord` | `in_community` | `ZenodoCommunity` |
| `ZenodoRecord` | `uploaded_by` | `ZenodoUser` |
| `ZenodoUser` | `uploaded` | `ZenodoRecord` |
| `ZenodoCommunity` | `contains` | `ZenodoRecord` |
| `ZenodoRecord` | `related_to.<RelationType>` | URL on any platform |

Five edge kinds. `related_to.<RelationType>` uses a compound string where `<RelationType>` is the DataCite RelationType vocabulary from Zenodo's `metadata.related_identifiers[].relation_type` (e.g., `related_to.isSupplementTo`, `related_to.cites`, `related_to.isCitedBy`). Downstream consumers can split on `.` if they care about relation semantics; the BFS engine treats it as opaque.

### 2.5 `GraphData` widening

The discriminated unions in `models.py` widen by one entry each:

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

`GraphData.users` / `orgs` / `repos` continue to be `dict[str, UserNode]` / `dict[str, OrgNode]` / `dict[str, RepoNode]`. No schema-version bump (Spec 2 is additive within v3.x).

## 3. Adapter behavior

### 3.1 `normalize_uri`

Accepts three input forms; returns the canonical platform URL:

1. **Canonical Zenodo URL** — `https://zenodo.org/records/<id>` (and `/communities/<slug>`, `/users/<id>`). Pass through `node_id.canonical_url` (lowercase host, strip trailing slash, drop fragment/query).
2. **Legacy `/record/<id>`** (singular) — rewrite `/record/<id>` → `/records/<id>` before canonicalizing.
3. **DOI URL** — `https://doi.org/10.5281/zenodo.<id>` → `https://zenodo.org/records/<id>`; `https://doi.org/10.5072/zenodo.<id>` → `https://sandbox.zenodo.org/records/<id>`. Pure regex; no HTTP lookup. Non-Zenodo DOIs raise `InvalidURIError` so the seed parser rejects them before they reach the queue.

`normalize_uri` itself does NOT resolve version-to-concept (that requires an API call). The version-to-concept canonicalization happens inside `fetch`.

### 3.2 `classify`

```python
def classify(self, uri: str) -> NodeKind | None:
    path = urlparse(self.normalize_uri(uri)).path.strip("/")
    if path.startswith("records/"):    return NodeKind.REPO
    if path.startswith("communities/"): return NodeKind.USER_OR_ORG
    if path.startswith("users/"):      return NodeKind.USER_OR_ORG
    return None
```

Reuses the existing `NodeKind` enum. The user-vs-community split is decided inside `fetch` based on the `/api/{records,communities,users}` endpoint chosen.

### 3.3 `fetch`

Routes by path prefix:

| URI shape | Client call | Returns |
|---|---|---|
| `/records/<id>` | `client.get_record(id)` → conditionally `client.get_record(concept_id)` | `ZenodoRecordModel` (always concept-rooted) |
| `/communities/<slug>` | `client.get_community(slug)` | `ZenodoCommunityModel` |
| `/users/<id>` | `client.get_user(id)` | `ZenodoUserModel` |

404 → `None`. Other HTTP errors degrade per the client layer.

**Concept-DOI resolution in `_build_record`:**

```python
def _build_record(self, uri: str) -> Optional[ZenodoRecordModel]:
    record_id = self._record_id_from_uri(uri)
    raw = self._client.get_record(record_id)
    if raw is None:
        return None
    concept_recid = raw.get("conceptrecid") or raw.get("metadata", {}).get("relations", {}) \
        .get("version", [{}])[0].get("parent", {}).get("pid_value")
    if concept_recid and str(concept_recid) != str(record_id):
        # This URI pointed at a specific version. Re-fetch the concept record so
        # the returned node's url IS the concept URL.
        self._concept_cache[uri] = self._url_for_record(concept_recid)
        concept_raw = self._client.get_record(concept_recid)
        if concept_raw is None:
            # Concept fetch failed — fall back to using this record as its own concept.
            return self._record_model_from(raw, concept_recid_fallback=record_id)
        return self._record_model_from(concept_raw, version_payload=raw)
    return self._record_model_from(raw)
```

The returned model always has `url` equal to the concept URL. `versions` is populated from the `metadata.relations.version` array on the concept record (Zenodo includes all version entries in the concept-record response). Subsequent visits via `_concept_cache` skip the extra API call.

### 3.4 `expand` — per-kind edge emission

```python
def expand(self, node: Node, opts: ExpandOpts) -> Iterable[Edge]:
    if isinstance(node, ZenodoRecordModel):
        yield from self._expand_record(node, opts)
    elif isinstance(node, ZenodoCommunityModel):
        yield from self._expand_community(node, opts)
    elif isinstance(node, ZenodoUserModel):
        yield from self._expand_user(node, opts)
```

**`_expand_record(record, opts)`** emits:
- For each community in the record's `metadata.communities[].identifier`: `Edge(src=record.url, kind="in_community", dst=self._url_for_community(slug))`.
- For the uploader (when the public response includes `owners[].user` or `submitter`): `Edge(src=record.url, kind="uploaded_by", dst=self._url_for_user(user_id))`. The public records API often omits this; the edge fires only when present.
- For each `metadata.related_identifiers[]` entry whose `identifier` is resolvable to an `https://` URL (direct URL or rewriteable DOI): `Edge(src=record.url, kind=f"related_to.{relation_type}", dst=target_url)`. Unresolvable schemes (e.g., bare `arxiv:` without a URL) are skipped silently.
- **No `version_of` edges.** Versions are intra-node metadata.

**`_expand_community(community, opts)`** emits:
- For each record in `client.iter_community_records(slug)`: `Edge(src=community.url, kind="contains", dst=record_url)`. The iterator paginates via `links.next` (Zenodo's keyset pagination).
- **No `member_of` edges.** Members are out of scope (auth-gated).

**`_expand_user(user, opts)`** emits:
- For each record in `client.iter_user_records(user_id)` (degrades to `[]` when 401/403 — common anonymously): `Edge(src=user.url, kind="uploaded", dst=record_url)`.
- No follows/stars (Zenodo has neither).

### 3.5 `ZenodoClient` API

Thin `httpx.Client` wrapper, six public methods:

```python
class ZenodoClient:
    def __init__(self, host: str, tokens: list[str], _cache_dir: Path | None = None): ...

    # Single fetches (404 → None; other errors degrade per `_degrade_on_forbidden`)
    def get_record(self, record_id: str | int) -> dict | None: ...
    def get_community(self, slug: str) -> dict | None: ...
    def get_user(self, user_id: str | int) -> dict | None: ...

    # Iterators (keyset pagination via `links.next`; default page size 100)
    def iter_community_records(self, slug: str) -> Iterable[dict]: ...
    def iter_user_records(self, user_id: str | int) -> Iterable[dict]: ...  # 401/403 → []

    def rate_limit_state(self) -> RateLimitInfo: ...
```

Returns plain `dict` payloads (Zenodo's API is JSON; an object layer like python-gitlab's would add complexity without benefit). Authentication via `Authorization: Bearer <tok>` header from the rotation pool's current token; no header in anonymous mode. Per-host disk cache wiring matches `GitLabClient`'s pattern (pre-allocated when `_cache_dir` set, single-entity lookups are the future wiring target).

### 3.6 Endpoint reference

| Operation | Endpoint | Auth |
|---|---|---|
| Get record | `GET /api/records/<id>` | Public anonymous-OK |
| Get community | `GET /api/communities/<slug>` | Public anonymous-OK |
| Get user | `GET /api/users/<id>` | Often 401 anonymously; degrade |
| List community records | `GET /api/records?communities=<slug>&size=100` | Public anonymous-OK; keyset paginate via `links.next` |
| List user records | `GET /api/records?q=owners.user:<id>&size=100` | Often auth-required; degrade |

Pagination uses `links.next` — we follow the URL Zenodo returns rather than incrementing `page=`, matching Invenio's keyset semantics.

## 4. Configuration, caching, testing

### 4.1 Configuration

No new mechanism. Host-keyed env vars from Spec 1 already cover Zenodo:

```bash
# Enable instances
CRAWLER_PLATFORMS="zenodo.org,sandbox.zenodo.org"

# Token rotation pool (preferred)
CRAWLER_TOKEN_POOL__ZENODO_ORG="zen-pat-a,zen-pat-b"

# Or single token
CRAWLER_TOKEN__ZENODO_ORG="zen-pat-here"
CRAWLER_TOKEN__SANDBOX_ZENODO_ORG="zen-sandbox-pat"
```

`cli.py:_build_registry` is extended with one branch:

```python
elif host in ("zenodo.org", "sandbox.zenodo.org") or host.endswith(".zenodo.org"):
    zen_client = ZenodoClient(host=host, tokens=tokens)  # tokens=[] OK
    reg.register(ZenodoAdapter(zen_client, instance_host=host))
```

`crawler doctor` already enumerates configured hosts and token counts — no doctor changes.

### 4.2 Caching

Per-host disk cache layout (`<cache_dir>/<host>/<sha256(uri)>.json`) from Spec 1 Task 15 already covers Zenodo URLs. `ZenodoClient` accepts the same optional `_cache_dir` parameter as `GitLabClient` with identical TODO-for-later wiring: pre-allocated cache directory; single-entity lookups still hit the API. Zenodo's JSON responses are easier to serialize to disk than python-gitlab's typed objects, so the cache wiring is straightforward to enable in a follow-up if needed.

### 4.3 Testing

| Suite | Coverage |
|---|---|
| `tests/platforms/test_zenodo_client.py` | Mock `httpx.Client`; assert each `get_*` and `iter_*` produces the right path + query and parses the response shape. Includes 404 → `None`, 401/403 → `[]`, keyset pagination via `links.next`. |
| `tests/platforms/test_zenodo_adapter.py` | Mock `ZenodoClient`; assert `classify`/`fetch`/`normalize_uri`/`expand` for each subkind. DOI URL rewriting (prod + sandbox). Compound `related_to.<RelationType>` edge encoding. Version-URL → concept-URL canonicalization (verifying the returned node's `url` is the concept URL even when seeded with a version URL). |
| `tests/integration/test_zenodo_dryrun.py` | Tiny live crawl against `zenodo.org`, anonymous mode. Seeds a small public community (`renku-python` or similar), runs one round, asserts the community node lands in the graph plus at least one `contains` edge. Skipped only when `CRAWLER_SKIP_INTEGRATION=1` is set. |

The existing `_fake_adapter.py` machinery covers crawler-dispatch tests for the new edge kinds (especially `related_to.<RelationType>` routing across platforms).

### 4.4 Explore script extension

`tools/scripts/fetch_public_projects.py` gains conditional behaviour by host:

- For GitLab hosts (existing): `GET /api/v4/projects?visibility=public`.
- For `zenodo.org` / `sandbox.zenodo.org`: `GET /api/records?size=100` (paginate via `links.next`). Writes record URLs to `data/explore/<host>.txt`.

One new helper `_fetch_zenodo_urls(host, limit)` lives in the same file. Host dispatch is one `if` at the top of `fetch_public_project_urls`. ~30 lines added.

For reference (probed 2026-05-28): `zenodo.org` reports ~5M total published records. Reasonable defaults: `--per-host-limit 10000` for a discovery sample, `--per-host-limit 100000` for a richer corpus. Full-instance dump is not the intended use case for this script.

A separate `tools/scripts/fetch_zenodo_communities.py` (mirror of the projects script but for communities) is an OPTIONAL follow-up — not required for Spec 2 to ship. ~80 LoC if added.

### 4.5 OpenAPI examples

`POST /api/v2/crawl` gains four new dropdown examples in `api/v2.py`:

```python
"zenodo_community_renku": {
    "summary": "Zenodo community (Renku)",
    "value": {"seeds": ["https://zenodo.org/communities/renku-python"], "max_rounds": 2},
},
"zenodo_record_doi_url": {
    "summary": "Zenodo record via DOI URL",
    "value": {"seeds": ["https://doi.org/10.5281/zenodo.7234562"], "max_rounds": 2},
},
"zenodo_record_canonical": {
    "summary": "Zenodo record via canonical URL",
    "value": {"seeds": ["https://zenodo.org/records/7234562"], "max_rounds": 2},
},
"cross_platform_zenodo_github": {
    "summary": "Zenodo record → discover related GitHub repo",
    "description": "Zenodo records with related_identifiers pointing at GitHub spawn a cross-platform crawl when the GitHub adapter is also registered.",
    "value": {
        "seeds": ["https://zenodo.org/records/7234562"],
        "max_rounds": 2,
    },
},
```

## 5. Effort estimate

| Block | Effort |
|---|---|
| Models (3 subclasses) + `node_id` DOI URL rewriter | ~0.25 day |
| `ZenodoClient` (`httpx`, 6 endpoints, cache hookup, 401/403 degrade) | ~0.5 day |
| `ZenodoAdapter` (classify/fetch/expand/normalize_uri, version → concept resolution, `related_to.<RelationType>` emission) | ~0.75 day |
| Tests (client unit, adapter unit, integration, fake-driven crawler dispatch) | ~0.5 day |
| Explore script extension | ~0.25 day |
| Docs (`docs/ZENODO.md`, README + CHANGELOG, OpenAPI examples) | ~0.25 day |
| **Total** | **~2.5 days** of focused work — similar profile to Spec 1's GitLab block (~2 days). |

## 6. Open questions for the implementation plan

Non-blocking; recorded so the executor doesn't have to invent answers.

- **Concept-record self-reference fields:** when a record IS the concept (no versioning), should `concept_doi == doi` and `latest_version_url == url` (self-reference), or `concept_doi = None` and `latest_version_*` left blank? Spec recommendation: self-reference. Consistent invariant ("every record has a concept_doi"); zero special cases in downstream consumers.
- **Resource-type filtering:** `--zenodo-resource-types software,dataset` CLI flag to skip publications? Defer to v3.1.1 if a user asks; not in Spec 2's MVP.
- **Token auth header precedence:** Zenodo accepts both `Authorization: Bearer <tok>` and `?access_token=<tok>` query string. The spec uses the header (keeps tokens out of URL logs). No precedence concern since we never send both.
- **`/api/users/<id>` auth requirement:** appears to be 401 anonymously on production Zenodo. Verify during implementation; if confirmed, document in `docs/ZENODO.md` that user-seeded crawls require a token.

## 7. Deferred to later specs

- **Spec 3 — HuggingFace adapter.** Subkinds: HuggingFaceUser, HuggingFaceOrganization, HuggingFaceModel, HuggingFaceDataset, HuggingFaceSpace.
- **Dual-path crawler unification.** Move GitHub legacy `_process_*` (≈800 LoC in `crawler.py`) into `GitHubAdapter`. Shrinks `crawler.py` ~50%; ships no new user-visible capability. Worth doing before Spec 3 lands so platform adapters all sit on the same path.
- **GitLab disk cache wiring.** Pre-allocated in Task 15 but not yet used; Zenodo client follows the same pattern.
- **Real rate-limit headers** from python-gitlab v5 (currently returns conservative defaults).
- **`crawler doctor` health probe** — actually call an API endpoint per host, not just count tokens. Marked TODO(post-task-18).
- **Cross-platform identity resolution** — explicitly assigned to another tool.
