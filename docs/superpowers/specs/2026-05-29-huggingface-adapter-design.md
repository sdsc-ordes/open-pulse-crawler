# HuggingFace adapter (Spec 5, targets v3.4.0)

**Status:** Draft for review
**Date:** 2026-05-29
**Branch:** `feat/multi-platform-gitlab` (same branch the multi-platform work lives on)
**Worktree:** `.worktrees/feat-multi-platform-gitlab/`
**Sequence:** Spec 5 of N — GitLab (Spec 1, shipped) → Zenodo (Spec 2, shipped) → Infoscience (Spec 3, shipped) → DataCite (Spec 4, shipped) → **HuggingFace (this)**

## Starting point: what Specs 1–4 already shipped

The platform abstraction, four adapters, and the DOI-prefix routing layer are in place. The shared `synthesize_target_url` helper in `platforms/datacite.py` handles arxiv / orcid / pmid / pmcid / swh / doi / url schemes uniformly — HuggingFace papers reuse it for cross-platform `related_to.<RelationType>` edges. Anonymous-first registration, tri-state `doctor`, multi-host `register_hosts`, and the v2 API platform parity (commit `8ee6419`) all carry over.

## Goal

Crawl HuggingFace — users, organizations, models, datasets, spaces, papers, and collections — as part of the unified open-science graph. Cross-platform value: HuggingFace papers carry `linkedModels` / `linkedDatasets` / `linkedSpaces` + `githubRepo` + arxiv ID, making them the strongest cross-platform pivot in the graph. A Zenodo dataset citing the same arxiv ID and an HF paper for that arxiv ID both emit edges to the same canonical `arxiv.org/abs/<id>` URL — natural convergence point for downstream identity resolution.

## Non-goals (explicitly deferred)

- **Cross-platform identity resolution.** Locked: downstream tooling owns merging `HuggingFaceUser` ↔ `ZenodoUser` ↔ … by ORCID / SciPer / email. HuggingFace authors on papers are bare `{name}` strings with no ORCID — embedded metadata only.
- **Org → User `has_member` edges.** The `/api/organizations/<name>/members` endpoint is auth-gated. Same situation as Infoscience's removed `has_member` flow. Skip; revisit if a public endpoint surfaces.
- **Model card / dataset card README parsing.** `cardData.tags` sometimes contains `arxiv:<id>` references, but card formats vary wildly. Defer to a v3.4.1 if useful.
- **Discussion / community / dataset-viewer / model-leaderboard data.** Read-only metadata crawl only.
- **User → Paper edges.** No public "papers authored by HF user X" endpoint; author lists on papers are bare names without HF-account linkage.
- **`tools/scripts/fetch_public_projects.py` extension.** No natural "browse all HuggingFace entities" use case.
- **Crossref-issued DOIs in model/dataset cards.** Stays with the future Crossref adapter (Spec 6 candidate).

## Locked-in design choices

| Topic | Decision |
|---|---|
| Entity scope | Five subkinds: `HuggingFaceUser`, `HuggingFaceOrg`, `HuggingFaceRepo` (models/datasets/spaces unified with `repo_type: Literal["model","dataset","space"]`), `HuggingFacePaper`, `HuggingFaceCollection`. |
| Subkind split | One `HuggingFaceRepo` covering models/datasets/spaces — mirrors `ZenodoRecord.resource_type` / `InfoscienceItem.resource_type` / `DataCiteWork.resource_type` precedent. Two subkinds (Repo + Paper) join `RepoNode`; two (Org + Collection) join `OrgNode`; one (User) joins `UserNode`. |
| Hosts owned | `huggingface.co` only. No multi-host like DataCite. |
| URL forms | `huggingface.co/<single>` (user-or-org), `<owner>/<name>` (model), `datasets/<owner>/<name>`, `spaces/<owner>/<name>`, `papers/<arxiv-id>`, `collections/<owner>/<slug>`. Reserved first-segment words: `datasets`, `spaces`, `papers`, `collections`. |
| User/Org disambiguation | Same `USER_OR_ORG` pattern as GitHub. `classify` returns `NodeKind.USER_OR_ORG`; `fetch` probes `/api/users/<x>/overview` first, falls through to `/api/organizations/<x>/overview` on 404. |
| Token auth | `Authorization: Bearer hf_...`. Anonymous-first; optional `CRAWLER_TOKEN__HUGGINGFACE_CO` + `CRAWLER_TOKEN_POOL__HUGGINGFACE_CO`. |
| Rate-limit handling | One automatic 429 retry honoring `Retry-After` (capped at 60s); second 429 raises. HuggingFace doesn't surface standard `X-RateLimit-*` headers — `rate_limit_state()` returns conservative placeholders. |
| Pagination | `Link` header with `cursor=<opaque>` query parameter (same shape as GitLab keyset). |
| `_token_host_for` | Returns `huggingface.co` unchanged — no API-host divergence like DataCite. |
| `_auth_required` | Returns `False` — anonymous-friendly. `doctor` reports ANONYMOUS without a token. |
| Cross-platform paper bridge | Papers emit `related_to.IsIdenticalTo` → arxiv.org URL, `related_to.IsSupplementedBy` → github.com URL (when `githubRepo` populated), via the shared `synthesize_target_url`. No HF-specific routing code. |

## 1. Architecture & module layout

```
src/open_pulse_crawler/
  platforms/
    huggingface/                    # NEW — the adapter package
      __init__.py
      client.py                     # HuggingFaceHTTPClient (httpx wrapper)
      adapter.py                    # HuggingFaceAdapter
  models.py                          # 5 new subclasses + 3 union widenings
  cli.py                             # _build_registry gains the 'huggingface.co' branch
  api/v2.py                          # OpenAPI examples for HF seeds
tests/
  platforms/
    test_huggingface_client.py       # NEW
    test_huggingface_adapter.py      # NEW
  test_models.py                     # 5 new subkind tests
  test_cli.py                        # 'huggingface.co' registration tests
  test_api_v2.py                     # OpenAPI examples test
  integration/
    test_huggingface_dryrun.py       # NEW — live paper seed
docs/
  HUGGINGFACE.md                     # NEW — adapter guide
  index.md, ../README.md, ../CHANGELOG.md  # updated
```

No new shared module — the `synthesize_target_url` + `rewrite_doi_url` infrastructure in `platforms/datacite.py` already handles every cross-platform edge HF needs. No `PlatformRegistry` API changes — single-host adapter.

## 2. Data model (five subkinds in `src/open_pulse_crawler/models.py`)

```python
class HuggingFaceUser(UserModel):
    """A HuggingFace user account. Distinguished from `HuggingFaceOrg` only
    by which API endpoint returned 200 (``/users/<x>/overview`` vs
    ``/organizations/<x>/overview``) — URL form is identical, so classify
    returns ``USER_OR_ORG`` and fetch disambiguates.

    Cross-platform identity stays downstream: `member_orgs` is HF-internal
    org names only, not a cross-platform ORCID/email join.
    """
    subkind: Literal["HuggingFaceUser"] = "HuggingFaceUser"
    username: str
    fullname: str = ""
    is_pro: bool = False
    avatar_url: str = ""
    num_models: int = 0
    num_datasets: int = 0
    num_spaces: int = 0
    num_papers: int = 0
    num_followers: int = 0
    member_orgs: List[str] = Field(default_factory=list)


class HuggingFaceOrg(OrgModel):
    """A HuggingFace organization (e.g. meta-llama, openai, BigScience)."""
    subkind: Literal["HuggingFaceOrg"] = "HuggingFaceOrg"
    org_name: str
    fullname: str = ""
    is_verified: bool = False
    plan: str = ""
    avatar_url: str = ""
    num_models: int = 0
    num_datasets: int = 0
    num_spaces: int = 0
    num_papers: int = 0
    num_users: int = 0
    num_followers: int = 0


class HuggingFaceRepo(RepoModel):
    """Unified repo subkind covering models / datasets / spaces. The three
    share git-repo plumbing (owner/name, sha, commits, files, tags,
    downloads, likes, cardData) — only the URL path prefix and a handful
    of typed fields differ. Mirrors the ``resource_type`` precedent.

    Typed-but-unused fields (``pipeline_tag``, ``sdk``, ``used_models``,
    ``paperswithcode_id``) are the cost of the single-subkind collapse.
    """
    subkind: Literal["HuggingFaceRepo"] = "HuggingFaceRepo"
    repo_type: Literal["model", "dataset", "space"]
    repo_id: str
    owner: str
    repo_name: str
    sha: str = ""
    tags: List[str] = Field(default_factory=list)
    downloads: int = 0
    likes: int = 0
    license: str = ""
    language: List[str] = Field(default_factory=list)
    gated: bool = False
    # Model-only:
    pipeline_tag: str = ""
    library_name: str = ""
    # Space-only:
    sdk: str = ""
    runtime_stage: str = ""
    used_models: List[str] = Field(default_factory=list)
    # Dataset-only:
    paperswithcode_id: str = ""


class HuggingFacePaper(RepoModel):
    """A paper on HuggingFace — keyed by arxiv ID
    (``huggingface.co/papers/2307.09288``). HF aggregates arxiv metadata
    plus HF-specific cross-references (``linkedModels`` / ``linkedDatasets`` /
    ``linkedSpaces`` / ``githubRepo``) that make papers the strongest
    cross-platform pivot in the graph.

    Cross-platform identity stays downstream: authors are bare ``{name}``
    strings without ORCID — no `authored_by` edges to ORCID URLs (unlike
    DataCite).
    """
    subkind: Literal["HuggingFacePaper"] = "HuggingFacePaper"
    arxiv_id: str
    arxiv_url: str
    title: str = ""
    summary: str = ""
    ai_summary: str = ""
    ai_keywords: List[str] = Field(default_factory=list)
    authors: List[Dict[str, Any]] = Field(default_factory=list)
    upvotes: int = 0
    published_at: str = ""
    github_repo: str = ""
    num_linked_models: int = 0
    num_linked_datasets: int = 0
    num_linked_spaces: int = 0


class HuggingFaceCollection(OrgModel):
    """A user-curated grouping (\"bucket\"): the owner picks N models /
    datasets / spaces / papers and gives the bundle a title + description.
    Crawled via ``contains`` edges to each item.
    """
    subkind: Literal["HuggingFaceCollection"] = "HuggingFaceCollection"
    slug: str
    owner: str
    title: str = ""
    description: str = ""
    upvotes: int = 0
    last_updated: str = ""
```

**Widen the three discriminated unions:**

```python
UserNode = Annotated[
    Union[UserModel, GitLabUserModel, ZenodoUserModel, InfosciencePerson,
          DataCitePerson, HuggingFaceUser],
    Field(discriminator="subkind"),
]
OrgNode = Annotated[
    Union[OrgModel, GitLabGroupModel, ZenodoCommunityModel, InfoscienceOrgUnit,
          DataCiteOrganization, DataCiteClient,
          HuggingFaceOrg, HuggingFaceCollection],
    Field(discriminator="subkind"),
]
RepoNode = Annotated[
    Union[RepoModel, GitLabProjectModel, ZenodoRecordModel, InfoscienceItem,
          DataCiteWork,
          HuggingFaceRepo, HuggingFacePaper],
    Field(discriminator="subkind"),
]
```

## 3. Adapter behavior

### 3.1 Hosts owned

Only `huggingface.co`. No multi-host registration.

### 3.2 `normalize_uri` rules

URL forms accepted and their canonical outputs:

| Input | Canonical output |
|---|---|
| `https://huggingface.co/<owner>/<name>` (model) | unchanged (after host-lowercase + trailing-slash strip) |
| `https://huggingface.co/datasets/<owner>/<name>` | unchanged |
| `https://huggingface.co/spaces/<owner>/<name>` | unchanged |
| `https://huggingface.co/papers/<arxiv-id>` | unchanged |
| `https://huggingface.co/papers/<arxiv-id>v2` | unchanged (version suffix preserved) |
| `https://huggingface.co/collections/<owner>/<slug>` | unchanged |
| `https://huggingface.co/<single>` (user-or-org) | unchanged when `<single>` ∉ reserved set |
| Any of the above with query string / fragment | stripped |
| Any of the above with trailing `/` | stripped |

Reserved first-segment words (`datasets`, `spaces`, `papers`, `collections`) cause the model-shape regex to skip. Path-prefix matching order matters; tests pin it.

### 3.3 `classify`

| Path shape | NodeKind |
|---|---|
| `papers/<arxiv-id>` | `REPO` |
| `datasets/<owner>/<name>` | `REPO` |
| `spaces/<owner>/<name>` | `REPO` |
| `<owner>/<name>` where `owner` ∉ reserved | `REPO` (model) |
| `collections/<owner>/<slug>` | `ORG` |
| `<single>` where `single` ∉ reserved | `USER_OR_ORG` |
| `datasets` / `spaces` / `papers` / `collections` alone | `None` |
| anything else (`/blog`, `/learn`, `/pricing`, …) | `None` |

### 3.4 `fetch`

| URL form | API call(s) | Returns |
|---|---|---|
| `<owner>/<name>` | `GET /api/models/<owner>/<name>` | `HuggingFaceRepo(repo_type="model")` |
| `datasets/<owner>/<name>` | `GET /api/datasets/<owner>/<name>` | `HuggingFaceRepo(repo_type="dataset")` |
| `spaces/<owner>/<name>` | `GET /api/spaces/<owner>/<name>` | `HuggingFaceRepo(repo_type="space")` |
| `papers/<arxiv-id>` | `GET /api/papers/<arxiv-id>` (may return list — take first) | `HuggingFacePaper` |
| `collections/<owner>/<slug>` | `GET /api/collections/<owner>/<slug>` | `HuggingFaceCollection` |
| `<single>` (USER_OR_ORG) | `GET /api/users/<single>/overview`; on 404 → `GET /api/organizations/<single>/overview` | `HuggingFaceUser` or `HuggingFaceOrg` |

- 404 / both-404 → `None` (BFS drops the node).
- 401/403 on gated content (rare with anonymous) → `None` with a warning log; same `degrade_on_auth` pattern as Infoscience client.
- USER_OR_ORG cost: at most 2 API calls per disambiguation. The user endpoint is tried first because it's the more common case (~80% of seeds in practice).

### 3.5 `expand` — edge kinds

Full table (12 edges across 5 subkinds):

```
HuggingFaceUser:
  owns          → HuggingFaceRepo (model/dataset/space)   per /api/{models,datasets,spaces}?author=<user>
  member_of     → HuggingFaceOrg                          per user.orgs[].name

HuggingFaceOrg:
  owns          → HuggingFaceRepo (model/dataset/space)   per /api/{models,datasets,spaces}?author=<org_name>

HuggingFaceRepo:
  owned_by      → HuggingFaceUser OR HuggingFaceOrg       from `author` field (BFS resolves type)
  uses_model    → HuggingFaceRepo (model)                 (Spaces only) per space.models[]

HuggingFacePaper:
  related_to.IsIdenticalTo     → URL on any platform (arxiv.org)
                                  via synthesize_target_url("arxiv", arxiv_id)
  related_to.IsSupplementedBy  → URL on any platform (github.com)
                                  when paper.github_repo is populated
  references_model     → HuggingFaceRepo (model)         per paper.linkedModels[].id
  references_dataset   → HuggingFaceRepo (dataset)       per paper.linkedDatasets[].id
  references_space     → HuggingFaceRepo (space)         per paper.linkedSpaces[].id

HuggingFaceCollection:
  owned_by      → HuggingFaceUser OR HuggingFaceOrg       from collection.owner.name
  contains      → HuggingFaceRepo OR HuggingFacePaper     per item; routed by item.type
```

**Edge-kind rationale:**

- **`related_to.IsIdenticalTo` for arxiv** — HF doesn't host its own paper PDF; the HF page is a metadata view of the arxiv paper. DataCite's `IsIdenticalTo` ("A is identical to B") fits.
- **`related_to.IsSupplementedBy` for github** — DataCite vocabulary: "B supplements A"; code supplements paper.
- **Typed `references_model` / `references_dataset` / `references_space`** — intra-platform edges with known target type; avoids forcing downstream to parse the target URL. Same pattern as Zenodo's typed `contains` rather than `related_to.X`.
- **`owns` / `owned_by` instead of `authored`** — HF-platform-neutral language ("models by meta-llama"); "authored" is awkward for Spaces.
- **No `has_member`** — auth-gated endpoint, locked out of scope.
- **`owns` iteration is uncapped** — matches `ZenodoCommunity.contains` and `InfoscienceOrgUnit.has_publication`. BFS `--max-rounds` is the only cap.

### 3.6 `rate_limit_state`

HuggingFace doesn't reliably surface rate-limit headers. Return conservative placeholders matching the Infoscience/DataCite pattern: `RateLimitInfo(remaining=1000, limit=2000, reset_at=None)`.

## 4. Configuration / caching / testing

### 4.1 Configuration

```bash
# Anonymous (recommended for discovery crawls):
CRAWLER_PLATFORMS=huggingface.co

# Authenticated (raises rate limits + unlocks gated content):
CRAWLER_TOKEN__HUGGINGFACE_CO=hf_<token>
# Or rotation pool:
CRAWLER_TOKEN_POOL__HUGGINGFACE_CO=hf_a,hf_b
```

Tokens are provisioned at <https://huggingface.co/settings/tokens>. Read-only tokens suffice for the crawl-only workload.

### 4.2 Caching

Per-host cache under `cache/huggingface.co/` via the existing `APICache` machinery. No HF-specific changes.

### 4.3 Cross-platform integration — concrete walk

Seed `https://huggingface.co/papers/2307.09288` (Llama 2 paper), `CRAWLER_PLATFORMS=huggingface.co,github.com,zenodo.org`:

```
Round 0:  HuggingFacePaper(arxiv_id=2307.09288)
Round 1 emits edges:
  related_to.IsIdenticalTo     → https://arxiv.org/abs/2307.09288
  related_to.IsSupplementedBy  → https://github.com/facebookresearch/llama
  references_model             → https://huggingface.co/meta-llama/Llama-2-7b
  references_model             → https://huggingface.co/meta-llama/Llama-2-13b
  references_dataset           → https://huggingface.co/datasets/<...>

Round 2 routes:
  github.com/facebookresearch/llama → GitHub adapter (fetches the repo)
  huggingface.co/meta-llama/Llama-2-7b → HF adapter (fetches the model)
  ...
```

A Zenodo dataset that *also* references arxiv:2307.09288 emits `related_to.IsSupplementTo → https://arxiv.org/abs/2307.09288` via the same `synthesize_target_url("arxiv", ...)` call — the **same canonical URL** the HF paper produces. Downstream identity resolution sees the convergence.

### 4.4 Testing

TDD shape mirroring Spec 4:

- **Unit tests** (mocked httpx):
  - ~20-25 client tests in `tests/platforms/test_huggingface_client.py` — one per endpoint + 429 retry + cursor pagination + USER_OR_ORG 404-fallthrough.
  - ~30-35 adapter tests in `tests/platforms/test_huggingface_adapter.py` — classify, normalize_uri (reserved-word disambiguation), fetch (5 subkinds), expand (12 edge kinds).
- **Integration test** (live anonymous, opt-out via `CRAWLER_SKIP_INTEGRATION=1`): seed `papers/2307.09288`, walk one round of `expand`, assert all three cross-platform edge targets (arxiv URL + github URL + at least one HF repo URL) appear. `tests/integration/test_huggingface_dryrun.py`. 2-second polite delay.
- **CLI tests**: `tests/test_cli.py` gains `huggingface.co` anonymous + tokens cases.
- **OpenAPI examples test**: `tests/test_api_v2.py` gains a HuggingFace-examples assertion.

## 5. Effort estimate

~3-3.5 days for an engineer familiar with the Infoscience + DataCite adapters. Approximate LoC:

| Component | Approx LoC |
|---|---|
| `platforms/huggingface/client.py` (httpx wrapper + 6+ endpoints + cursor pagination) | ~280 |
| `platforms/huggingface/adapter.py` (normalize/classify/fetch + 5 builders + expand + 6 expanders) | ~450 |
| `models.py` (5 subkinds + 3 union widenings) | ~120 |
| `cli.py` (huggingface.co branch — anonymous + authenticated) | ~15 |
| `api/v2.py` (3-4 OpenAPI examples) | ~50 |
| `tests/*` (new + extended) | ~850 |
| `docs/HUGGINGFACE.md` | ~180 |

**Total:** ~1,950 LoC including tests.

## 6. Open questions / deferred items

1. **`HuggingFaceRepo` denormalization.** Typed-but-unused fields (`pipeline_tag` empty for datasets, `sdk` empty for models, `used_models` empty for everything except spaces) are the cost of the single-subkind collapse. Alternative: three subkinds (Model/Dataset/Space) with shared base class. Defer the call to implementation; if model+dataset+space tests start needing a lot of `if repo.repo_type == "model"` switches, split is worth revisiting.

2. **`papers/<arxiv-id>` arxiv-ID regex coverage.** The proposed `^\d{4}\.\d+(?:v\d+)?$` accepts modern arxiv IDs (post-April 2007). Legacy IDs (`cond-mat/0303517` form) aren't accepted — HuggingFace only indexes modern IDs in practice. If a paper seed surfaces with a legacy ID, broaden the regex in a follow-up.

3. **`HuggingFaceCollection.items[]` edge type dispatch.** Items have `type ∈ {"model","dataset","space","paper"}`. Implementation needs to emit the right URL form per type (`<owner>/<name>` for model, `datasets/<owner>/<name>` for dataset, …). Edge case: an item.type that's outside the known set should log a warning and skip, not crash.

4. **Per-author iteration order.** `/api/models?author=<x>` returns models by trending score by default. For deterministic crawl ordering we'd pass `sort=createdAt&direction=1` — but it doesn't matter for correctness, only for reproducibility. Implementer's call.

5. **HuggingFace UI URL aliases.** Some UI pages use slightly different URL forms (`/models?author=...` for filtered listings). Accepting these as seeds adds normalization complexity for little crawl value. Skip; user paste the canonical entity URL.

6. **OAuth2 flow.** HuggingFace supports OAuth2 for app integrations (write access). Out of scope — read-only crawl uses Bearer tokens directly.

7. **`tools/scripts/fetch_public_projects.py` for HF.** Listing all HF models / datasets / spaces (millions of entities each) doesn't have a natural use case. Defer; add when concrete need surfaces.
