# DataCite Commons adapter (Spec 4, targets v3.3.0)

**Status:** Draft for review
**Date:** 2026-05-29
**Branch:** `feat/multi-platform-gitlab` (same branch the multi-platform work lives on)
**Worktree:** `.worktrees/feat-multi-platform-gitlab/`
**Sequence:** Spec 4 of N — GitLab (Spec 1, shipped) → Zenodo (Spec 2, shipped) → Infoscience (Spec 3, shipped) → **DataCite (this)**

## Starting point: what Specs 1–3 already shipped

The platform abstraction, Zenodo adapter, and Infoscience adapter are in place. Both DataCite-consuming siblings (Zenodo + Infoscience) already share the URL synthesizer at `src/open_pulse_crawler/platforms/datacite.py` (lifted out of `ZenodoAdapter` in Spec 3). Spec 4 expands that shared module into a true DataCite adapter that owns the `doi.org` / `ror.org` / `orcid.org` URL hosts and consumes `https://api.datacite.org/`.

## Goal

Crawl DataCite Commons — DOI-issuing repositories, ROR-identified organizations, and ORCID-identified researchers — as part of the unified open-science graph. Cross-platform value: a `DataCiteWork` whose `relatedIdentifiers[]` carries a Zenodo DOI / Infoscience handle / arxiv ID / GitHub URL produces edges through the existing shared synthesizer, with no DataCite-specific routing code. Seeds can be canonical `doi.org/<DOI>`, `ror.org/<id>`, or `orcid.org/<id>` URLs.

## Non-goals (explicitly deferred)

- **Crossref-issued DOIs** (Nature, IEEE, ACM, Elsevier, etc.). DataCite's `/dois/<doi>` endpoint returns 404 for them. Out of scope until a Crossref adapter is added.
- **ROR / ORCID secondary API enrichment.** `DataCiteOrganization` and `DataCitePerson` are intentionally bare anchors — name and affiliation come from `DataCiteWork.creators[]` opportunistically, not from `api.ror.org` or `pub.orcid.org`. Identity resolution stays downstream.
- **Active `DataCiteClient` expansion.** Repository nodes are passive — they appear via `published_by` edges from works, never fan out to walk their corpus. (User-explicit decision; an `--crawl-client-works` follow-up flag is possible but deferred.)
- **Cross-platform identity resolution.** Still locked: another tool owns merging `DataCitePerson` ↔ `ZenodoUser` ↔ `InfosciencePerson` by ORCID.
- **`tools/scripts/fetch_public_projects.py` random-DOI sampling.** Mentioned in §6 as a possible future addition; not part of this spec's scope.

## Locked-in design choices

| Topic | Decision |
|---|---|
| Entity scope | Four subkinds: `DataCiteWork`, `DataCiteOrganization` (ROR), `DataCitePerson` (ORCID), `DataCiteClient` (DataCite-registered repository). |
| Items split | One `DataCiteWork(RepoModel)` carrying `resource_type` (Dublin Core `resourceTypeGeneral`) + `resource_type_detail` (free-text `resourceType`). Same precedent as `ZenodoRecord.resource_type` and `InfoscienceItem.resource_type`. |
| Identity scheme | `DataCiteWork` URL = `https://doi.org/<DOI>`. `DataCiteOrganization` = `https://ror.org/<id>`. `DataCitePerson` = `https://orcid.org/<id>`. `DataCiteClient` = `https://commons.datacite.org/repositories/<client_id>`. Five hosts total. |
| Adapter wiring | Single `DataCiteAdapter` instance registered against all five hosts (novel — every other adapter is 1:1 with one host). `PlatformRegistry` gains a `register_hosts(hosts, adapter)` helper. |
| User-facing platform key | `datacite.org` — the single `--platforms` token that triggers registration against the five hosts. |
| DOI prefix routing | `_DOI_PREFIX_REWRITERS` table in `platforms/datacite.py` maps owned prefixes (`10.5281` → zenodo, `10.5072` → sandbox-zenodo) to their canonical platform URLs. DataCite owns everything else. Replaces the Zenodo-specific `node_id.rewrite_zenodo_doi_url`. |
| Token auth | `Authorization: Bearer <tok>` header. Anonymous when no token configured (public reads return 200). Optional `CRAWLER_TOKEN__API_DATACITE_ORG` + rotation pool. |
| Rate-limit handling | Honor `Retry-After` header on HTTP 429 with one automatic retry; second 429 raises. Same pattern as Infoscience. |
| Client node fan-out | `DataCiteClient.expand` emits nothing (passive). Locked. |
| ROR/ORCID fan-out | Uncapped per-node — same as `ZenodoCommunity.contains` and `InfoscienceOrgUnit.has_publication`. BFS `--max-rounds` is the only cap. |
| `relatedIdentifiers` | Same shape as Zenodo/Infoscience — reuses `synthesize_target_url` verbatim. |

## 1. Architecture & module layout

```
src/open_pulse_crawler/
  platforms/
    __init__.py                      # gains PlatformRegistry.register_hosts()
    datacite.py                      # EXTEND — add _DOI_PREFIX_REWRITERS table + rewrite_doi_url()
    datacite_adapter/                # NEW — the actual adapter package
      __init__.py
      client.py                      # DataCiteHTTPClient (httpx wrapper — class named with -HTTP- to avoid colliding with the DataCiteClient Pydantic subkind)
      adapter.py                     # DataCiteAdapter
  node_id.py                         # rewrite_zenodo_doi_url becomes a deprecation shim → removed
  models.py                          # 4 new subclasses + wider unions
  cli.py                             # _build_registry gains the 'datacite.org' branch
  api/v2.py                          # OpenAPI examples for DataCite seeds
tools/scripts/
  fetch_public_projects.py           # untouched in this spec (deferred — see §6)
tests/
  platforms/
    test_datacite.py                 # gains _DOI_PREFIX_REWRITERS / rewrite_doi_url tests
    test_datacite_adapter.py         # NEW — adapter unit tests
    test_datacite_client.py          # NEW — client unit tests
  test_models.py                     # 4 new subkind tests
  test_cli.py                        # 'datacite.org' platform key tests
  integration/
    test_datacite_dryrun.py          # NEW — live anonymous dryrun against api.datacite.org
docs/
  DATACITE.md                        # NEW — adapter guide
  index.md, ../README.md, ../CHANGELOG.md  # updated
```

**Notable naming:** the adapter package is `datacite_adapter/`, not `datacite/`, because `datacite.py` already exists at the same level as the platform packages. (Renaming `datacite.py` would also work — see §7 open question.)

## 2. Data model (four subkinds in `src/open_pulse_crawler/models.py`)

```python
class DataCiteWork(RepoModel):
    """A DOI registered with DataCite. Cross-repository node — could be
    a Zenodo record, Figshare dataset, Dryad submission, ETH WSL dataset, …

    Cross-platform identity resolution stays downstream. A `DataCiteWork`
    with DOI 10.5281/zenodo.X is intentionally NEVER produced — the DOI
    prefix routing in `normalize_uri` rewrites Zenodo prefixes to their
    canonical zenodo.org URLs before classify, so the Zenodo adapter
    handles those records. Same applies to any future prefix added to
    `_DOI_PREFIX_REWRITERS`."""
    subkind: Literal["DataCiteWork"] = "DataCiteWork"
    doi: str                                          # "10.6084/m9.figshare.X"
    resource_type: str = ""                           # resourceTypeGeneral
    resource_type_detail: str = ""                    # resourceType (free text)
    title: str = ""
    publication_year: Optional[int] = None
    publisher: str = ""
    client_id: Optional[str] = None                   # "figshare.ars" → DataCiteClient
    creators: List[Dict[str, Any]] = Field(default_factory=list)
    # [{name, orcid, affiliations: [{name, ror, scheme}]}]
    affiliations: List[Dict[str, Any]] = Field(default_factory=list)
    # de-duped flat list of {name, ror, scheme} across all creators
    relations: List[Dict[str, Any]] = Field(default_factory=list)
    # [{relation_type, target_type, target}] — direct from relatedIdentifiers
    subjects: List[str] = Field(default_factory=list)
    abstract: str = ""
    container_title: str = ""
    language: str = ""
    registered_url: Optional[str] = None              # attributes.url


class DataCiteOrganization(OrgModel):
    """An organization identified by ROR. Bare anchor — name populated
    opportunistically from creator affiliation entries seen in DataCiteWork
    fetches. NOT enriched via api.ror.org."""
    subkind: Literal["DataCiteOrganization"] = "DataCiteOrganization"
    ror_id: str                                       # "02s376052" (suffix only)
    ror_url: str                                      # "https://ror.org/02s376052"


class DataCitePerson(UserModel):
    """A person identified by ORCID. Bare anchor — name populated
    opportunistically from creator entries seen in DataCiteWork fetches.
    NOT enriched via pub.orcid.org."""
    subkind: Literal["DataCitePerson"] = "DataCitePerson"
    orcid: str                                        # "0000-0002-1825-0097"
    orcid_url: str                                    # "https://orcid.org/0000-0002-1825-0097"


class DataCiteClient(OrgModel):
    """A DataCite-registered repository (cern.zenodo, figshare.ars, dryad.dryad…).
    Passive node — `expand` emits no edges. Populated when DataCiteWork
    nodes carry `published_by` edges pointing at this client.

    Enriched from `/clients/<id>` on the round it's fetched: repo type,
    owned domains (cross-host routing hints for downstream tooling), and
    re3data registry cross-reference."""
    subkind: Literal["DataCiteClient"] = "DataCiteClient"
    client_id: str                                    # "cern.zenodo"
    repository_name: str = ""                         # attributes.name
    alternate_name: str = ""                          # attributes.alternateName
    client_type: str = ""                             # "repository" | "periodical" | …
    repository_type: List[str] = Field(default_factory=list)
    description: str = ""
    repository_url: str = ""                          # attributes.url
    domains: List[str] = Field(default_factory=list) # ["zenodo.org", "openaire.cern.ch"]
    re3data_doi: str = ""                             # "https://doi.org/10.17616/R3QP53"
    year_registered: Optional[int] = None
    is_active: bool = True
    doi_prefixes: List[str] = Field(default_factory=list)  # via relationships.prefixes (lazy)
```

**Widen the three discriminated unions** in `models.py`:

```python
UserNode = Annotated[
    Union[UserModel, GitLabUserModel, ZenodoUserModel, InfosciencePerson, DataCitePerson],
    Field(discriminator="subkind"),
]
OrgNode = Annotated[
    Union[OrgModel, GitLabGroupModel, ZenodoCommunityModel, InfoscienceOrgUnit,
          DataCiteOrganization, DataCiteClient],
    Field(discriminator="subkind"),
]
RepoNode = Annotated[
    Union[RepoModel, GitLabProjectModel, ZenodoRecordModel, InfoscienceItem, DataCiteWork],
    Field(discriminator="subkind"),
]
```

`DataCiteClient` joins `OrgNode` alongside `DataCiteOrganization`; the `subkind` discriminator keeps them apart.

## 3. Adapter behavior

### 3.1 Hosts owned

| Host | Role |
|---|---|
| `doi.org` | Canonical DOI seed host. After prefix routing, owned for non-Zenodo/non-sandbox DOIs. |
| `ror.org` | Canonical ROR seed host. |
| `orcid.org` | Canonical ORCID seed host. |
| `api.datacite.org` | API endpoint. Accepted as alias input; rewritten to `doi.org/<DOI>` canonical form. |
| `commons.datacite.org` | Commons UI. Accepted as alias input; rewritten to per-type canonical URLs. Also the canonical host for `DataCiteClient` nodes (`/repositories/<id>`). |

### 3.2 `normalize_uri` rules

Input forms accepted and their canonical outputs:

| Input | Canonical output |
|---|---|
| `https://doi.org/<DOI>` (DOI prefix NOT in `_DOI_PREFIX_REWRITERS`) | unchanged |
| `https://doi.org/<DOI>` (DOI prefix IS in table, e.g. `10.5281/zenodo.X`) | rewritten to `https://zenodo.org/records/X` (now routes to Zenodo adapter) |
| `https://api.datacite.org/dois/<DOI>` | rewritten to `https://doi.org/<DOI>` |
| `https://commons.datacite.org/doi.org/<DOI>` | rewritten to `https://doi.org/<DOI>` |
| `https://ror.org/<id>` (with or without trailing /) | `https://ror.org/<id>` (no trailing /) |
| `https://commons.datacite.org/ror.org/<id>` | `https://ror.org/<id>` |
| `https://orcid.org/<id>` | `https://orcid.org/<id>` (strip query, no trailing /) |
| `https://commons.datacite.org/orcid.org/<id>` | `https://orcid.org/<id>` |
| `https://commons.datacite.org/repositories/<client_id>` | unchanged (canonical `DataCiteClient` URL) |
| `https://api.datacite.org/clients/<client_id>` | rewritten to `https://commons.datacite.org/repositories/<client_id>` |

The DOI-prefix gate happens *inside* `normalize_uri` — when the rewriter returns a non-DataCite URL, the BFS engine routes the seed to the matching adapter (Zenodo today; potentially others tomorrow). DataCite never sees that URL again.

### 3.3 `classify`

| URL path | NodeKind |
|---|---|
| `doi.org/<DOI>` | `REPO` |
| `ror.org/<id>` | `ORG` |
| `orcid.org/<id>` | `USER` |
| `commons.datacite.org/repositories/<id>` | `ORG` |
| anything else | `None` |

No DSpace-style USER_OR_ORG ambiguity — every DataCite URL form is self-descriptive.

### 3.4 `fetch`

| URL form | API call | Returns |
|---|---|---|
| `doi.org/<DOI>` | `GET /dois/<doi>` | `DataCiteWork` |
| `ror.org/<id>` | (no network call) | `DataCiteOrganization(ror_id=id, ror_url=URL, name="")` |
| `orcid.org/<id>` | (no network call) | `DataCitePerson(orcid=id, orcid_url=URL, login=id, name="")` |
| `commons.datacite.org/repositories/<id>` | `GET /clients/<id>` | `DataCiteClient` |

- 404 on `/dois/<doi>` → returns `None` (BFS drops the node). No fall-through to a DOI resolver. Crossref DOIs end here.
- 401/403 on `/clients/<id>` (rare; would only happen with a bad token) → return `None` and log a warning.
- Bare-anchor Org/Person construction is intentional — see §0 / Non-goals on identity resolution.

### 3.5 `expand` — edge kinds

```
DataCiteWork:
  authored_by      → DataCitePerson (orcid.org URL)        per creators[].nameIdentifiers[] with scheme=ORCID
  affiliated_with  → DataCiteOrganization (ror.org URL)    per creators[].affiliation[].affiliationIdentifier with scheme=ROR
  related_to.<RT>  → URL on any platform                   per relatedIdentifiers[] via synthesize_target_url
  published_by     → DataCiteClient (commons.datacite.org/repositories/<id>)

DataCiteOrganization:
  has_publication  → DataCiteWork (doi.org URL)            via /dois?query=creators.affiliation.affiliationIdentifier:"<ror_url>"

DataCitePerson:
  authored         → DataCiteWork (doi.org URL)            via /dois?query=creators.nameIdentifiers.nameIdentifier:"<orcid_url>"

DataCiteClient:
  (passive — emits no edges)
```

**Non-ORCID author identifiers + non-ROR affiliation identifiers don't get edges** — they stay embedded in the work's `creators` / `affiliations` lists with their scheme attribute (e.g. `{"name": "...", "scheme": "Scopus"}`). No graph anchor on this platform.

`related_to.<RT>` reuses `synthesize_target_url` verbatim — same code path as Zenodo and Infoscience. DataCite's `relatedIdentifierType` enum lowercases cleanly to the schemes the helper already knows (`DOI` → `doi`, `arXiv` → `arxiv`, `Handle` → falls back to URL passthrough or None, `URL` → `url`).

### 3.6 `rate_limit_state`

DataCite exposes no rate-limit headers reliably. Return conservative placeholders (`remaining=1000, limit=2000, reset_at=None`) matching the Infoscience pattern.

## 4. Routing migration (the load-bearing piece)

### 4.1 Generalize `rewrite_zenodo_doi_url` → `rewrite_doi_url` in `platforms/datacite.py`

Today:

```python
# node_id.py (current — to be removed)
def rewrite_zenodo_doi_url(raw: str) -> Optional[str]: ...

# platforms/datacite.py (current — uses the node_id helper inside synthesize_target_url)
from ..node_id import rewrite_zenodo_doi_url
# ...
if scheme == "doi":
    rewritten = rewrite_zenodo_doi_url(f"https://doi.org/{ident}")
    return rewritten or f"https://doi.org/{ident}"
```

After Spec 4:

```python
# platforms/datacite.py (new)
_DOI_PREFIX_REWRITERS: Dict[str, Tuple[str, str]] = {
    # prefix → (canonical host, URL template using `{suffix}`)
    "10.5281": ("zenodo.org",         "https://zenodo.org/records/{suffix}"),
    "10.5072": ("sandbox.zenodo.org", "https://sandbox.zenodo.org/records/{suffix}"),
    # 10.5075 (EPFL) intentionally absent — opaque suffixes, no formulaic mapping.
    # Those DOIs route through DataCite via /dois/<doi>.
}

def rewrite_doi_url(doi_or_url: str) -> Optional[str]:
    """Map a doi.org URL or bare DOI to a sibling-adapter platform URL
    when the prefix is owned. Returns None to leave the DOI with DataCite.

    Accepts:
        "https://doi.org/10.5281/zenodo.42"  → "https://zenodo.org/records/42"
        "10.5281/zenodo.42"                  → "https://zenodo.org/records/42"
        "10.6084/m9.figshare.99"             → None
    """

# synthesize_target_url's DOI branch becomes:
if scheme == "doi":
    return rewrite_doi_url(ident) or f"https://doi.org/{ident}"
```

`node_id.rewrite_zenodo_doi_url` becomes a one-line shim → delete after `tests/test_node_id.py` migrates. The `ZenodoAdapter.normalize_uri` caller (line 70) updates to call `rewrite_doi_url` from `platforms/datacite.py` directly. Net behavior is unchanged for Zenodo — same inputs produce the same outputs.

### 4.2 `PlatformRegistry.register_hosts(hosts, adapter)`

Today's `register(adapter)` reads `adapter.instance_host` and registers under exactly one host. DataCite needs the same adapter under five hosts. Add:

```python
def register_hosts(self, hosts: list[str], adapter: PlatformAdapter) -> None:
    """Register the same adapter instance under multiple hosts.

    Used when one adapter owns several URL hosts (e.g., DataCite owns
    doi.org / ror.org / orcid.org / api.datacite.org / commons.datacite.org).
    Raises ValueError on any conflict; partial registration is rolled back.
    """
```

Idempotent across calls within a single CLI invocation (atomic check then write). Existing single-host `register()` keeps working unchanged — current adapters call it as before.

### 4.3 `_build_registry` branch for `datacite.org`

```python
elif host == "datacite.org":
    from .platforms.datacite_adapter.client import DataCiteHTTPClient
    from .platforms.datacite_adapter.adapter import DataCiteAdapter
    dcc = DataCiteHTTPClient(host="api.datacite.org", tokens=tokens)
    adapter = DataCiteAdapter(client=dcc, instance_host="api.datacite.org")
    reg.register_hosts(
        ["doi.org", "ror.org", "orcid.org",
         "api.datacite.org", "commons.datacite.org"],
        adapter,
    )
```

(Anonymous path mirrors this with `tokens=[]`.)

**Naming note:** the client class is `DataCiteHTTPClient` (not `DataCiteClient`) to avoid colliding with the `DataCiteClient` Pydantic subkind. Same convention as Infoscience's separation (file `client.py` holds class `InfoscienceClient`, subkind models live in `models.py`).

### 4.4 Doctor command

`crawler doctor` already iterates `registry.hosts()`. With the multi-host registration, all five DataCite hosts show up as separate rows sharing the same token-status indicator. Worth a comment in the doctor output noting the five-row group belongs to one adapter (purely cosmetic; out of scope to redesign here).

## 5. Configuration / caching / testing

### 5.1 Configuration

```bash
# Anonymous (recommended for discovery crawls — public API):
CRAWLER_PLATFORMS=datacite.org

# Authenticated (raises rate limits):
CRAWLER_TOKEN__API_DATACITE_ORG=<bearer-token>
# Or rotation pool:
CRAWLER_TOKEN_POOL__API_DATACITE_ORG=<tok-a>,<tok-b>
```

DataCite tokens are provisioned via `https://commons.datacite.org/sign-in` for organization members. Most use cases run anonymous.

### 5.2 Caching

Same `APICache` shape as Zenodo / Infoscience. Per-host cache directories under `cache/api.datacite.org/` (the underlying API endpoint, regardless of which of the five hosts the BFS originally entered through).

### 5.3 Testing

Follow the Spec-3 TDD template:

- **Unit tests** (mocked httpx): `tests/platforms/test_datacite_client.py`, `tests/platforms/test_datacite_adapter.py`.
- **Routing tests** (no network): `tests/platforms/test_datacite.py` gains `rewrite_doi_url` tests covering each known prefix + a "leave-alone" non-owned prefix + bare-DOI vs URL forms.
- **Integration test** (live anonymous API, opt-out via `CRAWLER_SKIP_INTEGRATION=1`): seed EPFL ROR, fetch one of the affiliated DOIs, assert subkind + DOI + ROR edge. `tests/integration/test_datacite_dryrun.py`. 2-second polite delay (DataCite is friendlier than Infoscience but still rate-limited).
- **CLI tests**: `tests/test_cli.py` gains tests for `--platforms datacite.org` registering against five hosts + token resolution.
- **Pagination tests**: cursor-based, follow `links.next`. The `_DOI_PREFIX_REWRITERS` plug-in pattern matters here — a `DataCiteOrganization.expand` walking 3,583 EPFL works should emit zero "duplicate Zenodo" works because any 10.5281 hit is rewritten before it becomes a node.

## 6. Effort estimate

~2-3 days for an experienced Pythonist familiar with the Zenodo + Infoscience adapters. Bulk of effort is the test surface (15+ adapter unit tests, ~12 client unit tests). The routing migration is mostly mechanical because the existing Zenodo behavior is fully covered by current tests. Single LoC additions:

| Component | Approx LoC |
|---|---|
| `platforms/datacite.py` extension (routing table + rewrite_doi_url) | ~40 |
| `platforms/datacite_adapter/client.py` (httpx wrapper + 5 endpoints + cursor pagination) | ~250 |
| `platforms/datacite_adapter/adapter.py` (normalize/classify/fetch/expand + 4 builders) | ~350 |
| `platforms/__init__.py` (register_hosts) | ~15 |
| `models.py` (4 subkinds + 3 union widenings) | ~80 |
| `cli.py` (datacite.org branch) | ~15 |
| `api/v2.py` (2-3 OpenAPI examples) | ~30 |
| `tests/*` (new + extended) | ~700 |
| `docs/DATACITE.md` | ~150 |

**Total:** ~1,600 LoC including tests.

## 7. Open questions / deferred items

1. **`platforms/datacite.py` vs `platforms/datacite_adapter/` naming.** The shared synthesizer (lifted in Spec 3) and the adapter package both want the name `datacite`. Options at implementation time: (a) keep current `datacite.py` + new `datacite_adapter/` package (chosen above, slightly ugly); (b) rename `datacite.py` → `datacite_vocabulary.py` and use `datacite/` as the adapter package (cleaner but touches more imports). Recommend (a) for v3.3.0, defer rename to a follow-up cleanup.

2. **`tools/scripts/fetch_public_projects.py` for DataCite.** The script lists hosts to crawl. DataCite has no natural "browse public DOIs" endpoint — a random-sample probe via `/dois?random=true&page[size]=100` could populate a starter list, but the data is so broad (millions of DOIs) the sample isn't useful as a seed list. Deferred — separate decision once a real use case appears.

3. **`commons.datacite.org/dois?<query>` Commons UI URLs as seed.** These are user-friendly faceted-search URLs (e.g., `commons.datacite.org/?query=Mersch`); DataCite Commons doesn't expose stable URLs for individual queries. Out of scope.

4. **`DataCiteClient.doi_prefixes` lazy fetch.** `/clients/<id>/relationships/prefixes` is a separate API call. Implementation could (a) skip and leave `doi_prefixes=[]` (simplest), (b) fetch eagerly on `fetch_client` (one extra HTTP call per client), or (c) fetch only when explicitly opted in. Recommend (b) for v3.3.0 since each client is fetched at most once and the data is useful for downstream routing.

5. **Crossref bridge.** Crossref-issued DOIs (Nature, ACM, IEEE, …) currently 404 against DataCite. A `CrossrefAdapter` could live alongside DataCite and share the DOI URL host via prefix routing in the same `_DOI_PREFIX_REWRITERS`-style table. Genuinely useful follow-up — Spec 5 candidate.

6. **`re3data` registry integration.** `DataCiteClient.re3data_doi` is a DOI to a separate registry. A future `Re3DataAdapter` could resolve those as cross-platform repository anchors. Spec 6 candidate.

7. **ROR / ORCID API enrichment.** Currently bare anchors. A separate identity-resolution layer (not this crawler) could enrich them. Locked out of scope.
