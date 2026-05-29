# Infoscience adapter (Spec 3, targets v3.2.0)

**Status:** Draft for review
**Date:** 2026-05-28
**Branch:** `feat/multi-platform-gitlab` (same branch the multi-platform work lives on)
**Worktree:** `.worktrees/feat-multi-platform-gitlab/`
**Sequence:** Spec 3 of N — GitLab (Spec 1, shipped) → Zenodo (Spec 2, shipped) → **Infoscience (this)** → HuggingFace (Spec 4)

## Starting point: what Specs 1+2 already shipped

The platform abstraction + Zenodo adapter are in place: `PlatformAdapter` ABC, `PlatformRegistry`, URL-keyed nodes, `subkind` discriminator on `models.py`, host-keyed env-var config (`CRAWLER_TOKEN__<HOST>`), per-host cache layout, dual-path crawler dispatch, `/api/v2` REST surface with OpenAPI examples, CLI `--platforms`, anonymous-friendly clients with graceful 401/403/429 degradation. Zenodo's adapter additionally introduced DataCite-flavored `related_to.<RelationType>` edges and a URL synthesizer for arxiv / orcid / pmid / pmcid / swh / doi schemes.

Spec 3 slots in as a fourth platform, **lifts the DataCite URL synthesizer** out of `ZenodoAdapter` into a shared `platforms/datacite.py` module so both Zenodo and Infoscience use the same code, and reuses everything else from the abstraction.

## Goal

Crawl EPFL's Infoscience publications + researchers + departmental units as part of the unified open-science graph. Items' DataCite-flavored `dc.relation.*` fields produce cross-platform `related_to.<RelationType>` edges identical to Zenodo's. EPFL researchers (CRIS Person entities) carry ORCID + SciPer + Scopus IDs as embedded metadata, not as cross-platform identity anchors.

## Non-goals (explicitly deferred)

- **Cross-platform identity resolution.** Still locked: another tool owns that layer. `InfosciencePerson` is purely a platform-internal account.
- **DSpace Communities + Collections.** OrgUnit captures the canonical EPFL departmental hierarchy; the DSpace community/collection structural layer is redundant and skipped.
- **DSpace-CRIS Project entities** (grants / funding). Would require a new top-level `ProjectModel` base class; out of scope for Spec 3.
- **Tequila SSO authentication.** Institutional browser-flow auth, not appropriate for server-side crawling.
- **DSpace deposit / submit / workflow APIs.** Read-only adapter.
- **Bitstream / binary download.** Just metadata + structural edges.
- **Generalizing to other DSpace instances** (UZH ZORA, UNIBE BORIS). Possible follow-up — refactor `InfoscienceAdapter` into a parameterized `DSpaceAdapter` once a second instance is needed.
- **HuggingFace adapter** — Spec 4.

## Locked-in design choices

| Topic | Decision |
|---|---|
| Entity scope | Three subkinds: `InfoscienceItem`, `InfosciencePerson`, `InfoscienceOrgUnit`. |
| Items split | One `InfoscienceItem(RepoModel)` subkind with a `resource_type: str` field (publications + datasets + software all share the same DSpace `item` shape; same precedent as `ZenodoRecord.resource_type`). |
| Identity scheme | Canonical URL = `https://infoscience.epfl.ch/handle/<prefix>/<id>` for all three subkinds. |
| Entity dispatch | DSpace returns `entityType: "Person" \| "OrgUnit" \| "Publication" \| ...` on every item response. The adapter's `fetch` reads this field and routes to one of three `_build_*` builders. |
| Architecture | Approach A — plain `httpx` `InfoscienceClient` + `InfoscienceAdapter`, mirroring the Zenodo pattern. No third-party DSpace SDK. |
| Shared helper | `_synthesize_target_url` lifts from `ZenodoAdapter` into a new `platforms/datacite.py`. Both adapters import it. Zenodo behavior is unchanged. |
| Token auth | `Authorization: Bearer <tok>` header. Anonymous when no token configured. Optional `CRAWLER_TOKEN__INFOSCIENCE_EPFL_CH`. |
| Rate-limit handling | Honor `Retry-After` header on HTTP 429 with one automatic retry; second 429 raises `httpx.HTTPStatusError`. Aggressive backoff because EPFL rate-limits hard. |
| Members crawl | `_expand_orgunit` gates `has_member` edge emission behind `opts.crawl_members` (default `False`) — EPFL departments can have hundreds of researchers. |
| DOI URL handling | EPFL's `10.5075/epfl-*` DOIs are **not** rewritten in `normalize_uri` (the prefix covers multiple EPFL services, not just Infoscience). DOI seeds flow through unchanged. |

## 1. Architecture & module layout

```
src/open_pulse_crawler/
  platforms/
    datacite.py                      # NEW — shared DataCite URL synthesizer
    infoscience/                     # NEW
      __init__.py                    # re-exports InfoscienceClient + InfoscienceAdapter
      client.py                      # httpx wrapper (~150 LoC: 5 endpoints + 429 retry + HAL+JSON paging)
      adapter.py                     # InfoscienceAdapter(PlatformAdapter) (~280 LoC)
    zenodo/
      adapter.py                     # MODIFIED — imports synthesize_target_url from datacite
  models.py                          # EXTENDED — three Infoscience subkinds
  cli.py                             # EXTENDED — _build_registry routes infoscience.epfl.ch
tests/
  platforms/
    test_datacite.py                 # NEW — relocated tests for the URL synthesizer
    test_infoscience_client.py       # NEW — mocked httpx, includes 429 retry
    test_infoscience_adapter.py      # NEW — classify/fetch/normalize/expand
  integration/
    test_infoscience_dryrun.py       # NEW — tiny live infoscience crawl, anonymous, opt-out
tools/scripts/
  fetch_public_projects.py           # MINOR EDIT — host-dispatch Infoscience listing endpoint
docs/
  INFOSCIENCE.md                     # NEW
src/open_pulse_crawler/api/
  v2.py                              # EXTENDED — OpenAPI examples
CHANGELOG.md / README.md             # EXTENDED — v3.2 section
```

`crawler.py` unchanged — the dual-path dispatch from Spec 1 routes any non-github host to `_process_one_via_adapter`. `node_id.py` doesn't need new helpers.

**Pre-flight refactor inside this spec** (not a deferred follow-up): lift `ZenodoAdapter._synthesize_target_url` (~80 LoC + ~11 tests) into `platforms/datacite.py`. Both `ZenodoAdapter` and `InfoscienceAdapter` import it. Zenodo's existing tests move to `test_datacite.py` and stay green. Net: same code surface, one new file, zero behavior change for Zenodo.

## 2. Data model

Three new Pydantic subclasses on top of v2 flat models. Same discriminated-union machinery — `GraphData` shape doesn't change.

### 2.1 `InfoscienceItem(RepoModel)`

Represents a DSpace item: publication OR resource, discriminated by `resource_type`.

```python
class InfoscienceItem(RepoModel):
    """A DSpace item on Infoscience — publication or resource.

    All artifacts (papers, theses, datasets, software, presentations, …)
    are DSpace ``item`` entities with the same shape. The ``resource_type``
    field carries Dublin Core ``dc.type`` so downstream code can filter
    publications vs datasets without an isinstance switch.
    """
    subkind: Literal["InfoscienceItem"] = "InfoscienceItem"
    handle: str                          # "20.500.14299/182247"
    uuid: str                            # DSpace internal UUID
    doi: Optional[str] = None            # dc.identifier.doi (when assigned)
    resource_type: str = ""              # dc.type — "journal article" | "dataset" | "software" | "master thesis" | …
    publication_date: str = ""           # dc.date.issued (ISO YYYY-MM-DD)
    title: str = ""                      # dc.title
    abstract: str = ""                   # dc.description.abstract
    authors: List[Dict[str, Any]] = []   # [{name, orcid, authority_uuid}, …] — embedded, not crawled
    keywords: List[str] = []             # dc.subject
    language: str = ""                   # dc.language.iso
    license: str = ""                    # dc.rights / datacite.rights
    journal: str = ""                    # dc.relation.ispartof
    issn: str = ""                       # dc.identifier.issn
    isbn: str = ""                       # dc.identifier.isbn
```

- `RepoModel.full_name` = the handle (e.g., `"20.500.14299/182247"`) — natural citation identifier.
- URL form: `https://infoscience.epfl.ch/handle/<handle>`.
- Inherited `RepoModel.contributors` / `forked_from` / `is_fork` / `dependents` / `dependencies` stay at defaults — DSpace has no fork/dependency concept.
- **`authors` is `list[dict]`, not separate Person nodes** (mirrors Zenodo's `ZenodoRecordModel.creators`). The `authority_uuid` field on each author dict is the Infoscience Person UUID; `_expand_item` emits an `authored_by` edge per author UUID without forcing eager Person fetches.

### 2.2 `InfosciencePerson(UserModel)`

```python
class InfosciencePerson(UserModel):
    """A DSpace-CRIS Person entity — an EPFL researcher profile.

    Distinct from cross-platform identity resolution (still out of scope).
    A Person here is purely an Infoscience platform entity, identified by
    its DSpace UUID and Handle. ORCID / SciPer / Scopus IDs are kept as
    embedded metadata, NOT as cross-platform identity anchors.
    """
    subkind: Literal["InfosciencePerson"] = "InfosciencePerson"
    handle: str                          # "20.500.14299/99923"
    uuid: str
    given_name: str = ""                 # person.givenname
    family_name: str = ""                # person.familyname
    orcid: Optional[str] = None          # person.identifier.orcid
    sciper_id: Optional[str] = None      # epfl.sciperId
    email: str = ""                      # person.email (often hidden anonymously)
    scopus_id: Optional[str] = None      # person.identifier.scopus-author-id
    affiliation_name: str = ""           # person.affiliation.name (text)
    affiliation_uuid: Optional[str] = None  # OrgUnit UUID when CRIS-linked
```

- `UserModel.login` = the SciPer ID when present, else the handle's last segment.
- `UserModel.name` = `"<given_name> <family_name>"`.
- URL form: `https://infoscience.epfl.ch/handle/<handle>`.
- Social fields (`followers`/`following`/`starred_repositories`/`watched_repositories`) inherited from `UserModel` stay empty — Infoscience has no social graph.

### 2.3 `InfoscienceOrgUnit(OrgModel)`

```python
class InfoscienceOrgUnit(OrgModel):
    """A DSpace-CRIS OrgUnit entity — an EPFL department, school, or lab.

    Forms the canonical EPFL hierarchy: school → faculty → department →
    laboratory. Parent pointer is set when CRIS exposes a parent relation.
    """
    subkind: Literal["InfoscienceOrgUnit"] = "InfoscienceOrgUnit"
    handle: str
    uuid: str
    unit_id: Optional[str] = None        # EPFL unit code (e.g., "TRANSP-OR")
    parent_uuid: Optional[str] = None    # parent OrgUnit's UUID
    parent_url: Optional[str] = None     # parent OrgUnit's canonical URL
    unit_type: str = ""                  # organization.type (school / institute / laboratory)
```

- `OrgModel.login` = the unit code (e.g., `"TRANSP-OR"`) when present, else the handle's last segment.
- URL form: `https://infoscience.epfl.ch/handle/<handle>`.
- `OrgModel.members` stays empty by default; population is opt-in via `opts.crawl_members=True`.

### 2.4 Edge kinds emitted by the Infoscience adapter

| Source | Kind | Target |
|---|---|---|
| `InfoscienceItem` | `authored_by` | `InfosciencePerson` |
| `InfoscienceItem` | `affiliated_with` | `InfoscienceOrgUnit` |
| `InfoscienceItem` | `related_to.<RelationType>` | URL on any platform |
| `InfosciencePerson` | `authored` | `InfoscienceItem` |
| `InfosciencePerson` | `member_of` | `InfoscienceOrgUnit` |
| `InfoscienceOrgUnit` | `has_publication` | `InfoscienceItem` |
| `InfoscienceOrgUnit` | `has_member` | `InfosciencePerson` (gated by `opts.crawl_members`) |
| `InfoscienceOrgUnit` | `parent_of` | `InfoscienceOrgUnit` (parent → child) |

Eight edge kinds. `related_to.<RelationType>` reuses the DataCite RelationType vocabulary and the shared `platforms/datacite.synthesize_target_url(scheme, identifier)` helper — same compound-kind encoding as Zenodo.

### 2.5 `GraphData` widening

The three discriminated unions each widen by one entry:

```python
UserNode = Annotated[
    Union[UserModel, GitLabUserModel, ZenodoUserModel, InfosciencePerson],
    Field(discriminator="subkind"),
]
OrgNode = Annotated[
    Union[OrgModel, GitLabGroupModel, ZenodoCommunityModel, InfoscienceOrgUnit],
    Field(discriminator="subkind"),
]
RepoNode = Annotated[
    Union[RepoModel, GitLabProjectModel, ZenodoRecordModel, InfoscienceItem],
    Field(discriminator="subkind"),
]
```

`GraphData.users` / `orgs` / `repos` stay `dict[str, UserNode]` / `dict[str, OrgNode]` / `dict[str, RepoNode]`. No schema-version bump (Spec 3 is additive within v3.x — targets v3.2.0).

## 3. Adapter behavior

### 3.1 URL normalization

`InfoscienceAdapter.normalize_uri(raw) -> str` handles two canonical input forms:

1. **Canonical handle URL** — `https://infoscience.epfl.ch/handle/<prefix>/<id>`. Pass through `node_id.canonical_url` (lowercase host, strip trailing slash, drop fragment/query).
2. **Direct UUID URL** — `https://infoscience.epfl.ch/server/api/core/items/<uuid>`. Resolve by fetching once to learn the handle, then return the canonical handle URL. Cached on `self._uuid_to_handle: dict[str, str]`.

Non-canonical EPFL DOI URLs (`https://doi.org/10.5075/...`) are NOT rewritten — the `10.5075` prefix covers multiple EPFL services, not just Infoscience. Seeds in DOI form flow through the BFS unchanged; if they target Infoscience handles via doi.org redirect, the operator should resolve manually for now.

### 3.2 Classify

```python
def classify(self, uri: str) -> NodeKind | None:
    path = urlparse(self.normalize_uri(uri)).path.strip("/")
    # All three entity types use the /handle/<prefix>/<id> form; classify
    # collapses to USER_OR_ORG and fetch() inspects entityType to pick
    # the right subkind.
    if path.startswith("handle/"):
        return NodeKind.USER_OR_ORG
    return None
```

DSpace's REST surface doesn't expose entity type in the URL — `/handle/<prefix>/<id>` is universal. `classify` returns `USER_OR_ORG` for any handle URL; `fetch` does the real dispatch via the server-side `entityType` discriminator.

### 3.3 Fetch

Single API endpoint, branches on `entityType`:

```python
def fetch(self, uri: str):
    uri = self.normalize_uri(uri)
    handle = self._handle_from_uri(uri)
    raw = self._client.get_item_by_handle(handle)
    if raw is None:
        return None
    entity_type = (raw.get("entityType") or "").lower()
    if entity_type == "person":
        return self._build_person(uri, raw)
    if entity_type == "orgunit":
        return self._build_orgunit(uri, raw)
    # default: Publication, Resource, or unspecified — all map to InfoscienceItem
    return self._build_item(uri, raw)
```

Each `_build_*` builder reads the shared `metadata` dict and picks out the fields relevant to its subkind. 404 → `None`. The UUID→handle cache is updated for every item fetched, so subsequent author/orgunit URL resolutions skip the round-trip.

### 3.4 Expand — per-kind edge emission

**`_expand_item(item, opts)`** emits:
- For each `dc.contributor.author[]` entry with an `authority` field (CRIS Person UUID): `authored_by` edge → the Person's canonical handle URL (resolved via `_uuid_to_handle` cache; falls back to UUID-form URL if not yet cached, which the BFS will canonicalize on its next round).
- For each `cris.virtual.department[].authority`: `affiliated_with` edge → the OrgUnit's canonical URL.
- For each `dc.relation.*` metadata field where the qualifier maps to a DataCite RelationType (`dc.relation.isversionof` → `isVersionOf`, `dc.relation.issupplementto` → `isSupplementTo`, `dc.relation.uri` → generic `references`, etc.): compound `related_to.<RelationType>` edge via `datacite.synthesize_target_url(scheme, identifier)`. The scheme is inferred from the identifier's shape: `http(s)://...` → `"url"`, `10.*/...` → `"doi"`, `arXiv:*` → `"arxiv"`, etc.

**`_expand_person(person, opts)`** emits:
- For each item returned by `client.iter_person_items(person.uuid)` (paginated via `/server/api/discover/search/objects?query=author.authority:<uuid>&dsoType=item&size=100`): `authored` edge → the item's handle URL.
- For the person's `affiliation_uuid` (when set): `member_of` edge → the OrgUnit's canonical URL.

**`_expand_orgunit(orgunit, opts)`** emits:
- For each child OrgUnit (discovered via querying for `parent-organization.authority:<this.uuid>` with `dspace.entity.type:OrgUnit` filter): `parent_of` edge → the child's canonical URL (parent → child direction).
- For each item returned by `client.iter_orgunit_items(orgunit.uuid)`: `has_publication` edge → item URL.
- **Gated:** for each person returned by `client.iter_orgunit_persons(orgunit.uuid)` when `opts.crawl_members=True`: `has_member` edge → person URL. Default off because EPFL departments can have hundreds of members.

### 3.5 `InfoscienceClient` API

Thin `httpx.Client` wrapper:

```python
class InfoscienceClient:
    def __init__(self, host: str, tokens: list[str], _cache_dir: Path | None = None): ...

    # Single fetches (404 → None; other errors raise after 429 retry)
    def get_item_by_handle(self, handle: str) -> dict | None: ...
    def get_item_by_uuid(self, uuid: str) -> dict | None: ...

    # Iterators (DSpace's paginated discover/search; page size 100)
    def iter_person_items(self, person_uuid: str) -> Iterable[dict]: ...
    def iter_orgunit_items(self, orgunit_uuid: str) -> Iterable[dict]: ...
    def iter_orgunit_persons(self, orgunit_uuid: str) -> Iterable[dict]: ...

    def rate_limit_state(self) -> RateLimitInfo: ...
```

**429 retry-after handling** wraps every request: on HTTP 429, read `Retry-After` header (default 5s), `time.sleep`, retry once; second 429 raises `httpx.HTTPStatusError`. Essential — anonymous Infoscience returned 429 on multiple probes.

**Authentication via `Authorization: Bearer <token>`** when tokens configured; anonymous when not. DSpace 7 accepts Bearer tokens when a JWT-style API token is provisioned by EPFL.

### 3.6 DSpace 7 endpoint reference

| Operation | Endpoint | Notes |
|---|---|---|
| Get item by handle | `GET /server/api/handle/{prefix}/{id}` | Returns item with `entityType` discriminator |
| Get item by UUID | `GET /server/api/core/items/{uuid}` | Same shape; UUID-keyed |
| Discover search | `GET /server/api/discover/search/objects?dsoType=item&size=100&query=...` | HAL+JSON; paginated via `_links.next` |
| Items by author | `query=author.authority:<uuid>` | Items where the author's CRIS UUID matches |
| Items by department | `query=author.parent-organization.authority:<uuid>` | Items affiliated with this OrgUnit |
| Persons in OrgUnit | `query=dspace.entity.type:Person AND author.parent-organization.authority:<uuid>` | DSpace-CRIS pattern |

Pagination follows `_links.next` URLs verbatim (same shape as Zenodo's `links.next`). Anonymous page-size cap is **100** (no 25 limit like Zenodo).

## 4. Configuration, caching, testing

### 4.1 Configuration

No new mechanism. Host-keyed env vars from Spec 1 already cover Infoscience:

```bash
# Enable
CRAWLER_PLATFORMS="infoscience.epfl.ch"

# Optional API token (anonymous works without)
CRAWLER_TOKEN__INFOSCIENCE_EPFL_CH="dspace-api-token-here"
```

`cli.py:_build_registry` gets one new branch in both the authenticated and anonymous paths:

```python
elif host == "infoscience.epfl.ch" or host.endswith(".infoscience.epfl.ch"):
    from .platforms.infoscience.client import InfoscienceClient
    from .platforms.infoscience.adapter import InfoscienceAdapter
    isc = InfoscienceClient(host=host, tokens=tokens)  # tokens=[] OK
    reg.register(InfoscienceAdapter(isc, instance_host=host))
```

`crawler doctor` requires no changes — already enumerates hosts from `CRAWLER_PLATFORMS`.

### 4.2 Caching + rate-limit handling

- Per-host disk cache layout (Spec 1, Task 15) already covers Infoscience URLs. The client accepts the same optional `_cache_dir` parameter as the Zenodo client with the same TODO-for-later wiring (pre-allocate, don't yet wire into single-entity lookups).
- **429 retry-after** is enforced inside `_request_json` and `_iter_paginated`. One automatic retry honoring `Retry-After`; second 429 raises.
- **UUID → handle cache** on the adapter (`self._uuid_to_handle: dict[str, str]`) populated lazily — saves round-trips when the same Person co-authors multiple papers.

### 4.3 Testing

| Suite | Coverage |
|---|---|
| `tests/platforms/test_datacite.py` | The ~11 relocated tests for `synthesize_target_url(scheme, identifier)` (was `ZenodoAdapter._synthesize_target_url`). Same coverage, new module path. |
| `tests/platforms/test_infoscience_client.py` | Mock `httpx.Client`; assert each `get_*` and `iter_*` produces the right path + query and parses HAL+JSON. Includes 404 → `None`, 429 → retry-once-then-raise, keyset pagination via `_links.next`. |
| `tests/platforms/test_infoscience_adapter.py` | Mock `InfoscienceClient`; assert `classify`/`fetch`/`normalize_uri`/`expand` for each subkind. `entityType`-based dispatch (Person/OrgUnit/Item). All 8 edge kinds. UUID-URL → handle-URL canonicalization. Compound `related_to.<RelationType>` reuse. `crawl_members` gating. |
| `tests/integration/test_infoscience_dryrun.py` | Tiny live crawl against `infoscience.epfl.ch`, anonymous, with `time.sleep(2)` between requests to stay polite. Skipped via `CRAWLER_SKIP_INTEGRATION=1`. |

The Zenodo adapter's existing `_synthesize_target_url` static-method tests move to `test_datacite.py` and stay green. The 6 `_expand_record_emits_*` tests in `test_zenodo_adapter.py` stay (they exercise the call site, not the synthesizer).

### 4.4 Explore-script extension

`tools/scripts/fetch_public_projects.py` gains an Infoscience branch alongside GitLab + Zenodo:

```python
def _fetch_infoscience_urls(host: str, limit: int) -> Iterable[str]:
    """Yield up to `limit` Infoscience item handle URLs via DSpace's
    discover/search/objects endpoint, keyset-paginated.
    """
    # GET /server/api/discover/search/objects?dsoType=item&size=100
    # iterates _embedded.searchResult._embedded.objects[].handle
```

Host dispatch is one `if` at the top of `fetch_public_project_urls`. ~40 LoC added. Page size 100 (no anonymous-25 limit).

`DEFAULT_HOSTS` gains `"infoscience.epfl.ch"` under a new `# --- Swiss research institutional repositories ---` section.

### 4.5 OpenAPI examples

`POST /api/v2/crawl` gets three new dropdown examples in `api/v2.py`:

```python
"infoscience_publication_handle": {
    "summary": "Infoscience publication via handle URL",
    "description": (
        "Single EPFL publication. Round 0 fetches the item; round 1 walks "
        "authored_by → InfosciencePerson nodes and affiliated_with → "
        "InfoscienceOrgUnit nodes."
    ),
    "value": {"seeds": ["https://infoscience.epfl.ch/handle/20.500.14299/182247"], "max_rounds": 2},
},
"infoscience_person_authored_chain": {
    "summary": "EPFL researcher → all their publications",
    "description": (
        "Seed an InfosciencePerson; round 1 emits `authored` edges to every "
        "publication attributable to them. Useful for building a per-researcher "
        "publication graph anchored on a real EPFL profile."
    ),
    "value": {"seeds": ["https://infoscience.epfl.ch/handle/20.500.14299/99923"], "max_rounds": 2},
},
"cross_platform_infoscience_github": {
    "summary": "Infoscience publication → GitHub via dc.relation.*",
    "description": (
        "EPFL papers that link to GitHub repos via DSpace's dc.relation.uri or "
        "dc.relation.isVersionOf fields spawn a cross-platform crawl when "
        "CRAWLER_PLATFORMS=infoscience.epfl.ch,github.com. The exact seed will "
        "be picked during implementation by probing for a publication that "
        "carries a GitHub URL in its DataCite-flavored relation fields."
    ),
    "value": {"seeds": ["https://infoscience.epfl.ch/handle/20.500.14299/<TBD>"], "max_rounds": 2},
},
```

The third example's seed is filled in during implementation by probing for an EPFL publication that carries a `github.com` URL in its DataCite-flavored relation fields. If no such publication is easily findable, the example is dropped from the OpenAPI dropdown rather than left with a placeholder handle.

## 5. Effort estimate

| Block | Effort |
|---|---|
| Lift `_synthesize_target_url` → `platforms/datacite.py` (+ Zenodo import + test relocation) | ~0.25 day |
| Models (3 subclasses) + discriminated-union widening | ~0.25 day |
| `InfoscienceClient` (httpx, 5 endpoints, 429 retry, HAL+JSON pagination) | ~0.5 day |
| `InfoscienceAdapter` (classify/fetch/expand/normalize_uri, 8 edge kinds, UUID→handle cache) | ~0.75-1 day |
| Tests (datacite, client, adapter, integration, fake-driven crawler dispatch) | ~0.5 day |
| Explore-script extension (~40 LoC) | ~0.1 day |
| Docs (`docs/INFOSCIENCE.md`, README + CHANGELOG, OpenAPI examples) | ~0.25 day |
| **Total** | **~2.5-3 days** of focused work — similar profile to Spec 2's Zenodo block. |

## 6. Open questions for the implementation plan

Non-blocking; recorded so the executor doesn't have to invent answers.

- **UUID → handle round-trip for author/orgunit edges.** When `_expand_item` sees `dc.contributor.author[].authority` (a UUID), the canonical Person URL needs the Person's handle, which requires an item fetch. Recommendation: emit edges keyed by `https://infoscience.epfl.ch/server/api/core/items/<uuid>` (UUID-form URL), then `normalize_uri` resolves to handle-form on the round-trip fetch using `_uuid_to_handle`. Cheaper than pre-fetching every author handle.
- **DataCite RelationType camelCase normalization.** DSpace stores relations as `dc.relation.isversionof` (lowercased qualifier). Zenodo uses `isVersionOf` (camelCase). The adapter normalizes lowercase qualifiers to camelCase via a small lookup table covering the DataCite vocabulary so the `related_to.<RelationType>` edge kinds match Zenodo's casing across platforms.
- **DSpace API token format.** Spec assumes `Authorization: Bearer <token>`. If EPFL exposes only an `X-XSRF-TOKEN` form (CSRF-style), the client's auth header will need to extend. Verify during implementation by checking EPFL's API docs.
- **Real seed for the `cross_platform_infoscience_github` example.** Pick during implementation by querying `discover/search/objects?query=dc.relation.uri:*github.com*` (or similar). If no easy match, drop the example rather than ship a placeholder.

## 7. Deferred to later specs

- **DSpace-CRIS Project entities** (grants / funding). Would need a new `ProjectModel` base class — bigger architectural change.
- **Cross-platform identity resolution** — still out per the locked decision.
- **Other Swiss DSpace instances** (UZH ZORA, UNIBE BORIS, EPFL c4science archive). Refactor `InfoscienceAdapter` into a parameterized `DSpaceAdapter` when a second instance lands.
- **HuggingFace adapter** — Spec 4.
- **GitLab disk cache wiring** — pre-allocated in Task 15 but not yet used; Zenodo + Infoscience clients follow the same TODO pattern.
- **Real rate-limit headers from python-gitlab v5** — currently returns conservative defaults.
- **`crawler doctor` health probe** — actually call an API endpoint per host, not just count tokens.
