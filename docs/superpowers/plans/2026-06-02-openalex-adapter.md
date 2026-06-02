# OpenAlex Platform Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenAlex as a first-class host-keyed crawl adapter (Works/Authors/Institutions/Sources/Funders) that unifies the DOI/ORCID/ROR namespace under canonical keys, traverses citations in both directions (references + capped inbound `cited_by`), and takes precedence over DataCite on the shared hosts.

**Architecture:** Mirror `platforms/datacite_adapter/` (a thin `OpenAlexHTTPClient` + an `OpenAlexAdapter` implementing the `PlatformAdapter` ABC). 5 new per-platform subkinds in `models.py` registered into the existing discriminated unions. Works are the active crawl frontier; the other four are passive anchors (expanded only when used as seeds, capped). OpenAlex registers the shared hosts (`doi.org`/`orcid.org`/`ror.org`) and holds the DataCite adapter as a `fallback_adapter` for fetch-misses; the Crossref enricher (Spec 6) remains the final metadata fallback. Reference edges are kept canonical via a batch OpenAlex-ID→DOI resolver.

**Tech Stack:** Python 3.12, Pydantic v2 (discriminated unions on `subkind`), httpx, pytest. Tests run with `PYTHONPATH=src python -m pytest <paths> -q`.

**Spec:** `specs/openalex-adapter.md` (Spec 7).

**Conventions (ALL tasks):** TDD (failing test first). Commit after each task. NEVER add a `Co-Authored-By` trailer. NEVER pass `--no-verify`, `--no-gpg-sign`, or `-c commit.gpgsign=false`. Do NOT push. Work on branch `feat/multi-platform-gitlab` in `/workspaces/project/.worktrees/feat-multi-platform-gitlab`.

---

## File Structure

- `src/open_pulse_crawler/models.py` — **modify**: add 5 subkinds + register into `UserNode`/`OrgNode`/`RepoNode` unions (Task 1).
- `src/open_pulse_crawler/config.py` + `.env.dist` — **modify**: `CRAWLER_OPENALEX_MAILTO` (Task 2).
- `src/open_pulse_crawler/platforms/openalex_adapter/__init__.py` — **create** (Task 3).
- `src/open_pulse_crawler/platforms/openalex_adapter/client.py` — **create**: `OpenAlexHTTPClient` (Task 3).
- `src/open_pulse_crawler/platforms/openalex_adapter/adapter.py` — **create**: `OpenAlexAdapter` `normalize_uri`/`classify`/`fetch` + builders (Task 4), `expand` + traversal/caps (Task 6).
- `src/open_pulse_crawler/cli.py` — **modify**: `_build_registry` precedence wiring (Task 5); doctor/enablement (Task 7).
- `docs/OPENALEX.md`, `CHANGELOG.md` — **create/modify** (Task 7).
- Tests: `tests/test_models.py`, `tests/test_config.py`, `tests/platforms/test_openalex_client.py`, `tests/platforms/test_openalex_adapter.py`, `tests/test_openalex_precedence.py`, `tests/test_cli.py`.

Each task = its own commit, gated by spec-review + code-quality-review under subagent-driven-development.

---

## Task 1: OpenAlex subkind models

**Files:**
- Modify: `src/open_pulse_crawler/models.py` (add classes after `DataCiteWork`/`HuggingFace*`; extend the `UserNode`/`OrgNode`/`RepoNode` `Union`s near lines 650–665).
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_models.py  (append)
from open_pulse_crawler.models import (
    GraphData, OpenAlexWork, OpenAlexAuthor, OpenAlexInstitution,
    OpenAlexSource, OpenAlexFunder, ExternalIdentifier,
)

def test_openalex_work_minimal_and_subkind():
    w = OpenAlexWork(url="https://doi.org/10.1/x", doi="10.1/x")
    assert w.subkind == "OpenAlexWork"
    assert w.cited_by == [] and w.references == []

def test_openalex_author_keyed_by_orcid():
    a = OpenAlexAuthor(url="https://orcid.org/0000-0002-3336-0163",
                       login="0000-0002-3336-0163", openalex_id="A123")
    assert a.subkind == "OpenAlexAuthor"

def test_openalex_subkinds_roundtrip_through_graphdata():
    g = GraphData()
    g.add_repo(OpenAlexWork(url="https://doi.org/10.1/x", doi="10.1/x",
               external_identifiers=[ExternalIdentifier(scheme="pmid", value="42")]))
    g.add_user(OpenAlexAuthor(url="https://orcid.org/0000-0002-3336-0163",
               login="0000-0002-3336-0163", openalex_id="A1"))
    g.add_org(OpenAlexInstitution(url="https://ror.org/02kpeqv85",
               login="02kpeqv85", ror_id="02kpeqv85"))
    g.add_org(OpenAlexSource(url="https://openalex.org/S1", login="S1", openalex_id="S1"))
    g.add_org(OpenAlexFunder(url="https://doi.org/10.13039/501100001691",
               login="10.13039/501100001691", openalex_id="F1"))
    g2 = GraphData.model_validate_json(g.model_dump_json())
    assert isinstance(g2.repos["https://doi.org/10.1/x"], OpenAlexWork)
    assert g2.repos["https://doi.org/10.1/x"].external_identifiers[0].scheme == "pmid"
    assert isinstance(g2.users["https://orcid.org/0000-0002-3336-0163"], OpenAlexAuthor)
    assert isinstance(g2.orgs["https://openalex.org/S1"], OpenAlexSource)
```

- [ ] **Step 2: Run to verify fail** — `PYTHONPATH=src python -m pytest tests/test_models.py -q` → ImportError (classes undefined).

- [ ] **Step 3: Implement** — add to `models.py` (mirror `DataCiteWork`/`DataCitePerson`/`DataCiteOrganization` shapes):

```python
class OpenAlexWork(RepoModel):
    """A work (paper/preprint/dataset) from OpenAlex. Active crawl frontier."""
    subkind: Literal["OpenAlexWork"] = "OpenAlexWork"
    doi: str = ""
    openalex_id: str = ""            # W…
    title: str = ""
    publication_year: Optional[int] = None
    work_type: str = ""
    cited_by_count: Optional[int] = None
    is_oa: Optional[bool] = None
    # canonical https://doi.org/... (or https://openalex.org/W…) edge targets
    references: List[str] = Field(default_factory=list)      # outbound (backward)
    cited_by: List[str] = Field(default_factory=list)        # inbound (forward)
    authored_by: List[str] = Field(default_factory=list)     # orcid/openalex A urls
    funded_by: List[str] = Field(default_factory=list)       # funder urls
    published_in: str = ""                                   # source url
    creators: List[Dict[str, Any]] = Field(default_factory=list)  # {name,orcid,institutions:[ror]}

class OpenAlexAuthor(UserModel):
    """An author anchor (keyed by ORCID, else openalex A…)."""
    subkind: Literal["OpenAlexAuthor"] = "OpenAlexAuthor"
    orcid: str = ""
    openalex_id: str = ""
    affiliations: List[str] = Field(default_factory=list)    # ror urls

class OpenAlexInstitution(OrgModel):
    subkind: Literal["OpenAlexInstitution"] = "OpenAlexInstitution"
    ror_id: str = ""
    openalex_id: str = ""
    country_code: str = ""
    institution_type: str = ""

class OpenAlexSource(OrgModel):
    subkind: Literal["OpenAlexSource"] = "OpenAlexSource"
    openalex_id: str = ""
    issn_l: str = ""
    issns: List[str] = Field(default_factory=list)
    host_organization: str = ""
    is_oa: Optional[bool] = None

class OpenAlexFunder(OrgModel):
    subkind: Literal["OpenAlexFunder"] = "OpenAlexFunder"
    openalex_id: str = ""
    funder_doi: str = ""             # 10.13039/...
    country_code: str = ""
```

Then extend the unions (mirror the existing `DataCiteWork`/`DataCitePerson` entries):
- `RepoNode = Annotated[Union[..., DataCiteWork, CrossrefWork, OpenAlexWork], Field(discriminator="subkind")]`
- `UserNode = Annotated[Union[..., DataCitePerson, OpenAlexAuthor], ...]`
- `OrgNode = Annotated[Union[..., DataCiteOrganization, DataCiteClient, OpenAlexInstitution, OpenAlexSource, OpenAlexFunder], ...]`

- [ ] **Step 4: Run** — `PYTHONPATH=src python -m pytest tests/test_models.py -q` → PASS.
- [ ] **Step 5: Commit** — `git add src/open_pulse_crawler/models.py tests/test_models.py && git commit -m "feat(openalex): add 5 OpenAlex subkind models + union registration"`

---

## Task 2: `CRAWLER_OPENALEX_MAILTO` config

**Files:** Modify `src/open_pulse_crawler/config.py`, `.env.dist`; Test `tests/test_config.py`.

Mirror Task A of the Crossref work exactly (`resolve_crossref_mailto` at config.py is the template).

- [ ] **Step 1: Failing tests** (`tests/test_config.py`):

```python
from open_pulse_crawler.config import resolve_openalex_mailto, OPENALEX_MAILTO_ENV
def test_openalex_mailto_set(monkeypatch):
    monkeypatch.setenv(OPENALEX_MAILTO_ENV, " a@b.org ")
    assert resolve_openalex_mailto() == "a@b.org"
def test_openalex_mailto_unset(monkeypatch):
    monkeypatch.delenv(OPENALEX_MAILTO_ENV, raising=False)
    assert resolve_openalex_mailto() is None
def test_openalex_mailto_blank(monkeypatch):
    monkeypatch.setenv(OPENALEX_MAILTO_ENV, "   ")
    assert resolve_openalex_mailto() is None
```

- [ ] **Step 2: Run → fail** (`ImportError`).
- [ ] **Step 3: Implement** — add `OPENALEX_MAILTO_ENV = "CRAWLER_OPENALEX_MAILTO"` and `def resolve_openalex_mailto() -> Optional[str]:` returning the stripped value or `None` (copy `resolve_crossref_mailto`'s body). Add it to the module-docstring "Public surface" list. Document the var in `.env.dist` near `CRAWLER_CROSSREF_MAILTO` with a one-line comment.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(openalex): add CRAWLER_OPENALEX_MAILTO config"`

---

## Task 3: `OpenAlexHTTPClient`

**Files:**
- Create: `src/open_pulse_crawler/platforms/openalex_adapter/__init__.py` (empty), `.../client.py`.
- Test: `tests/platforms/test_openalex_client.py`

Mirror `platforms/datacite_adapter/client.py` for the httpx setup + `_do_get` 429/`Retry-After` retry (cap `MAX_RETRY_AFTER_SECONDS = 60`). Polite pool from `config.resolve_openalex_mailto()` (or constructor arg): send `mailto` query param + `User-Agent: OpenPulseCrawler (+https://openpulse.science; mailto:<addr>)`; one-time warning via a module flag when no mailto. Base URL `https://api.openalex.org`. All requests `Accept: application/json`. Provide `close()`/`__enter__`/`__exit__` (mirror the Crossref client).

API shapes (defensive — any key may be absent):
- `GET /works/{id}` → `{id, doi, title, publication_year, type, cited_by_count, open_access:{is_oa}, referenced_works:[W…], authorships:[{author:{id,orcid,display_name}, institutions:[{id,ror,display_name,country_code,type}]}], primary_location:{source:{id,issn_l,issn:[],display_name,host_organization_name,is_oa,type}}, grants:[{funder, funder_display_name}], ids:{openalex,doi,mag,pmid,pmcid}}`. `{id}` accepts `W…`, `doi:10…`, or a full `https://doi.org/…` URL.
- `GET /authors/{id}` (`A…`/`orcid:…`/orcid URL) → `{id, orcid, display_name, ids:{openalex,orcid,scopus,...}, affiliations/last_known_institutions:[{id,ror,...}]}`.
- `GET /institutions/{id}` (`I…`/`ror:…`/ror URL) → `{id, ror, display_name, country_code, type, ids:{openalex,ror,grid,wikidata,mag}}`.
- `GET /sources/{id}` (`S…`) → `{id, issn_l, issn:[], display_name, host_organization_name, is_oa, type, ids:{...}}`.
- `GET /funders/{id}` (`F…`) → `{id, display_name, country_code, ids:{openalex,ror,doi,wikidata}}`.

- [ ] **Step 1: Failing tests** — use `httpx.MockTransport` (mirror `tests/platforms/test_crossref_client.py`):

```python
def test_get_work_parses(monkeypatch):
    msg = {"id":"https://openalex.org/W4295510681","doi":"https://doi.org/10.1002/glia.24258",
           "title":"Control of Ca2+","publication_year":2022,"type":"article",
           "cited_by_count":35,"referenced_works":["https://openalex.org/W1","https://openalex.org/W2"]}
    client = make_client_returning(msg)        # MockTransport → 200 json=msg
    raw = client.get_work("10.1002/glia.24258")
    assert raw["doi"].endswith("10.1002/glia.24258")
    assert len(raw["referenced_works"]) == 2

def test_get_work_404_returns_none(): ...      # 404 → None
def test_mailto_polite_pool_param_and_ua(): ... # request has mailto= + UA contains addr
def test_no_mailto_warns_once(caplog): ...
def test_429_then_200_retries_once(monkeypatch): ...  # monkeypatch time.sleep
def test_iter_citing_works_paginates(): ...    # cursor pages: 2 pages → all results
def test_resolve_ids_to_doi_batches():         # /works?filter=ids.openalex:W1|W2&select=id,doi
    # mock returns [{"id":".../W1","doi":"https://doi.org/10.1/a"},{"id":".../W2","doi":None}]
    out = client.resolve_ids_to_canonical(["https://openalex.org/W1","https://openalex.org/W2"])
    assert out["https://openalex.org/W1"] == "https://doi.org/10.1/a"
    assert out["https://openalex.org/W2"] == "https://openalex.org/W2"  # no DOI → openalex url
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement `OpenAlexHTTPClient`** with methods:
  - `get_work/get_author/get_institution/get_source/get_funder(id) -> Optional[dict]` (404 → None; URL-quote the id path with `safe=":/"` so `doi:`/`https://` id forms survive).
  - `iter_citing_works(work_openalex_id, cap) -> Iterable[dict]`: `GET /works?filter=cites:{Wid}&per-page=200&sort=publication_date:desc&cursor=*`, follow `meta.next_cursor`, `select=id,doi`, stop after `cap` items.
  - `iter_works_by_entity(filter_key, entity_id, cap)`: same paging with `filter={filter_key}:{id}` (`author.id` / `institutions.id`), `select=id,doi`, stop after `cap`.
  - `resolve_ids_to_canonical(openalex_work_urls) -> dict[str,str]`: batch the W-IDs (≤50 per `filter=ids.openalex:W1|W2|...`, `select=id,doi`), map each to `https://doi.org/{doi}` (lowercased, prefix-stripped) when a DOI exists else its `https://openalex.org/W…` url. Unresolved IDs map to their openalex url.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(openalex): add OpenAlexHTTPClient + batch id→DOI resolver"`

---

## Task 4: `OpenAlexAdapter` core — `normalize_uri` / `classify` / `fetch` + builders

**Files:**
- Create: `src/open_pulse_crawler/platforms/openalex_adapter/adapter.py`
- Test: `tests/platforms/test_openalex_adapter.py`

Mirror `platforms/datacite_adapter/adapter.py` structure (regex path matchers, `urlsplit`, `canonical_url` fallback). `OpenAlexAdapter(PlatformAdapter)` with `platform="openalex"`, constructor `__init__(self, client, instance_host="api.openalex.org", fallback_adapter=None)`.

**Canonical keying (the heart of this task):**
- `doi.org/<doi>`: if prefix `10.13039` → Funder key `https://doi.org/10.13039/<rest>`; else Work key `https://doi.org/<doi>` (lowercased).
- `orcid.org/<id>` → Author key.
- `ror.org/<id>` → Institution key.
- `openalex.org/W…|A…|I…|S…|F…` and `api.openalex.org/works|authors|institutions|sources|funders/<id>` → canonical `https://openalex.org/<ID>`.

`classify(uri)` → `NodeKind.REPO` (work), `NodeKind.USER` (author/orcid), `NodeKind.ORG` (institution/source/funder/ror). (Use the same `NodeKind` import as DataCite.)

`fetch(uri)`:
- Work URI (doi.org non-funder, or openalex.org/W…): `raw = client.get_work(<doi or W>)`; `None` → **`return self.fallback_adapter.fetch(uri) if self.fallback_adapter else None`**; else `_build_work`.
- Author (orcid.org / openalex A…): `get_author`; on None → fallback; else `_build_author`.
- Institution (ror.org / openalex I…): `get_institution`; None → fallback; else `_build_institution`.
- Source (openalex S…): `get_source` → `_build_source` (no fallback; DataCite has no sources).
- Funder (doi.org/10.13039 or openalex F…): `get_funder` → `_build_funder`.

Builders populate the canonical `url`, the typed id fields, `external_identifiers` (every entry in the API `ids` block as `ExternalIdentifier(scheme=..., value=...)`), and `platform="openalex"`. `_build_work` keeps `references` as the **raw `referenced_works` W-urls for now** (Task 6 resolves them to canonical in `expand`); stores `creators` from `authorships`.

- [ ] **Step 1: Failing tests** — cover `normalize_uri` for every host form (incl. `10.13039`→funder vs work, `api.openalex.org/works/W1`→`openalex.org/W1`); `classify` returns the right `NodeKind`; `fetch` builds the right subkind keyed by the canonical URL (inject a fake client returning fixtures); **`fetch` falls back to a stub `fallback_adapter` when the client returns None** (assert the fallback's return value is propagated); `external_identifiers` populated from `ids`.

```python
def test_normalize_funder_doi_vs_work_doi():
    a = OpenAlexAdapter(FakeClient(), fallback_adapter=None)
    assert a.classify("https://doi.org/10.13039/501100001691") == NodeKind.ORG
    assert a.classify("https://doi.org/10.1002/glia.24258") == NodeKind.REPO

def test_fetch_work_miss_delegates_to_fallback():
    class FB:  # stub DataCite
        def fetch(self, uri): return "FALLBACK_NODE"
    a = OpenAlexAdapter(FakeClient(work=None), fallback_adapter=FB())
    assert a.fetch("https://doi.org/10.1/x") == "FALLBACK_NODE"
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `normalize_uri`/`classify`/`fetch` + the 5 builders + `rate_limit_state` (placeholder like DataCite). Wire the `fallback_adapter` delegation on every applicable miss.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(openalex): adapter normalize_uri/classify/fetch + builders + DataCite fallback"`

---

## Task 5: Precedence wiring in `_build_registry`

**Files:** Modify `src/open_pulse_crawler/cli.py` (`_build_registry`, lines ~119–232); Test `tests/test_openalex_precedence.py`.

**Behavior:** When `openalex.org` is in `platforms_list`, OpenAlex owns the shared hosts and DataCite owns only its own; when it isn't, DataCite keeps current behavior (owns all 5).

Implement with a deterministic two-phase wiring so list order doesn't matter and OpenAlex can hold the DataCite adapter as fallback:
- Detect `openalex_enabled = "openalex.org" in platforms_list`.
- In the DataCite branch(es), register hosts conditionally: `dc_hosts = ["api.datacite.org","commons.datacite.org"]` and, **only if not `openalex_enabled`**, also `["doi.org","ror.org","orcid.org"]`. Keep a reference to the constructed `DataCiteAdapter` (e.g. `datacite_adapter`) for the OpenAlex branch.
- Add an `openalex.org` branch (anonymous-capable, like DataCite): build `OpenAlexHTTPClient(...)`, construct `OpenAlexAdapter(client=..., instance_host="api.openalex.org", fallback_adapter=datacite_adapter)`, and `reg.register_hosts(["openalex.org","api.openalex.org","doi.org","orcid.org","ror.org"], adapter)`.
- Because `register_hosts` raises on conflict, ensure DataCite (when openalex_enabled) never registers the shared hosts. If `datacite.org` is NOT enabled but `openalex.org` is, OpenAlex still registers the shared hosts with `fallback_adapter=None`.
- Guarantee DataCite is constructed before OpenAlex regardless of `platforms_list` order (e.g. pre-scan: build DataCite adapter first if `datacite.org` present, then iterate; or sort so datacite precedes openalex). Document the chosen mechanism in a comment.

- [ ] **Step 1: Failing tests** (`tests/test_openalex_precedence.py`):

```python
from open_pulse_crawler.cli import _build_registry
def test_openalex_owns_shared_hosts_when_enabled():
    reg,_,_ = _build_registry(["datacite.org","openalex.org"])
    from open_pulse_crawler.platforms.openalex_adapter.adapter import OpenAlexAdapter
    for h in ["doi.org","orcid.org","ror.org","openalex.org","api.openalex.org"]:
        assert isinstance(reg.adapter_for(f"https://{h}/x"), OpenAlexAdapter)
    # DataCite keeps its own hosts
    from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter
    assert isinstance(reg.adapter_for("https://api.datacite.org/x"), DataCiteAdapter)

def test_openalex_fallback_is_datacite_when_both_enabled():
    reg,_,_ = _build_registry(["datacite.org","openalex.org"])
    oa = reg.adapter_for("https://doi.org/10.1/x")
    from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter
    assert isinstance(oa.fallback_adapter, DataCiteAdapter)

def test_datacite_alone_keeps_shared_hosts():
    reg,_,_ = _build_registry(["datacite.org"])
    from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter
    assert isinstance(reg.adapter_for("https://doi.org/10.1/x"), DataCiteAdapter)

def test_openalex_alone_no_fallback():
    reg,_,_ = _build_registry(["openalex.org"])
    assert reg.adapter_for("https://doi.org/10.1/x").fallback_adapter is None

def test_order_independent():
    reg,_,_ = _build_registry(["openalex.org","datacite.org"])  # reversed order
    assert reg.adapter_for("https://doi.org/10.1/x").fallback_adapter is not None
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** the two-phase wiring described above.
- [ ] **Step 4: Run** — `PYTHONPATH=src python -m pytest tests/test_openalex_precedence.py -q` → PASS. Also run `tests/test_cli.py` to confirm no regression in existing DataCite-only registration.
- [ ] **Step 5: Commit** — `git commit -m "feat(openalex): registry precedence — OpenAlex owns shared hosts, DataCite fallback"`

---

## Task 6: `expand` — bidirectional traversal, caps, seed-vs-reached

**Files:** Modify `src/open_pulse_crawler/platforms/openalex_adapter/adapter.py` (add `expand` + helpers); Modify `platforms/base.py` `ExpandOpts` to add `max_citations_per_work: Optional[int] = 50` and `max_works_per_entity: Optional[int] = 25`; Test `tests/platforms/test_openalex_adapter.py`.

`expand(node, opts)`:
- **OpenAlexWork:**
  - `references`: call `client.resolve_ids_to_canonical(node.references_raw)` → emit `Edge(src, "references", dst)` for each canonical url.
  - `cited_by`: iterate `client.iter_citing_works(node.openalex_id, cap=opts.max_citations_per_work)`; for each citing work emit `Edge(src, "cited_by", dst)` where dst = its DOI url (from the `select=id,doi`) else its openalex url. If the iterator hits the cap, `logger.info` the truncation against `node.cited_by_count`.
  - `authored_by`: for each creator, emit `Edge(src,"authored_by", orcid_url or openalex A-url)`.
  - `affiliated_with`: for each creator's institution ROR, emit `Edge(author_url,"affiliated_with", ror_url)`.
  - `published_in`: if `node.published_in`, one `Edge(src,"published_in", source_url)`.
  - `funded_by`: for each funder, `Edge(src,"funded_by", funder_url)`.
- **OpenAlexAuthor / OpenAlexInstitution:** passive when reached. Seed-expansion is governed by a flag the crawler sets on seeds; model it as: expand emits `authored`/`has_affiliated_work` edges via `client.iter_works_by_entity("author.id"/"institutions.id", node.openalex_id, cap=opts.max_works_per_entity)` **only when** `node.is_explored is False and <seed marker>`. Use the simplest available seed signal: expand these entities' works whenever called (the crawler only calls `expand` on dequeued nodes; anchors reached via edges are passive because Works don't queue authors/institutions for expansion — they're recorded as anchors). **Decision for this task:** Author/Institution `expand` always emits up to `max_works_per_entity` `authored`/`affiliated_work` edges; this makes ORCID/ROR seeds useful and is bounded by the cap. Document this and the cap-logging.
- **OpenAlexSource / OpenAlexFunder:** passive, no edges.

(Note: `references` is stored raw as W-urls by Task 4's builder — rename the stored field to `references_raw` on the model or keep `references` holding raw W-urls and resolve in-place; pick one and keep it consistent. Recommended: builder stores raw W-urls in `references`, and `expand` resolves them to canonical for the emitted Edge dst — the stored list documents provenance, the edges are canonical.)

- [ ] **Step 1: Failing tests** — fake client with canned `resolve_ids_to_canonical`, `iter_citing_works`, `iter_works_by_entity`:
  - work expand emits references (canonical), capped cited_by (+ truncation log via caplog), authored_by, affiliated_with, published_in, funded_by edges with correct dst URLs.
  - `max_citations_per_work` cap respected; `max_works_per_entity` cap respected on author/institution expand.
  - source/funder expand emit nothing.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `expand` + the two new `ExpandOpts` fields (default 50 / 25).
- [ ] **Step 4: Run → PASS** (and re-run `tests/platforms/test_openalex_adapter.py` whole file).
- [ ] **Step 5: Commit** — `git commit -m "feat(openalex): expand — references + capped cited_by + author/inst/source/funder edges"`

---

## Task 7: Enablement, doctor, docs, CHANGELOG

**Files:** Modify `cli.py` (doctor `_auth_required`/`_token_host_for` if needed so `openalex.org` reports ANONYMOUS; ensure `CRAWLER_PLATFORMS` accepts `openalex.org`), `.env.dist` (add `openalex.org` to the example `CRAWLER_PLATFORMS` line), create `docs/OPENALEX.md`, modify `CHANGELOG.md`. Test `tests/test_cli.py`.

- [ ] **Step 1: Failing test** — `tests/test_cli.py`: `doctor` (via `CliRunner`, monkeypatch `CRAWLER_PLATFORMS=openalex.org`, no tokens) reports OpenAlex as `ANONYMOUS` (not `MISSING`). Mirror existing doctor tests.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — make `openalex.org` anonymous-capable (it requires no token, like DataCite/Zenodo): ensure `_auth_required("openalex.org")` is False and it's reachable in doctor’s loop. Add `docs/OPENALEX.md` mirroring `docs/DATACITE.md` (rationale: bidirectional citations + ORCID/ROR unification; the 5 entities + keys; OpenAlex-first precedence with DataCite fallback; `CRAWLER_OPENALEX_MAILTO`; the `max_citations_per_work`/`max_works_per_entity` knobs; out-of-scope = Concepts/Topics/Publishers/Geo + code-layer discovery). Add a `CHANGELOG.md` entry under `## [Unreleased]`.
- [ ] **Step 4: Run → PASS.** Then full suite: `PYTHONPATH=src python -m pytest tests/ -q` (allow the known-unrelated gitlab import skips).
- [ ] **Step 5: Commit** — `git commit -m "feat(openalex): enablement + doctor + docs/OPENALEX.md + CHANGELOG"`

---

## Final review (after all tasks)
Dispatch a holistic reviewer over `git diff <first-task^>..HEAD` focused on: canonical-key consistency across builder ↔ adapter ↔ edges (especially the W-id→DOI resolution so reference edges match materialized node keys); the DataCite-fallback delegation path; cap logging; and union round-trip for all 5 subkinds. Then stop for the user (do NOT push/PR without explicit instruction — branch is held at develop per standing guidance).

---

## Self-review notes (plan vs spec)
- **Spec coverage:** entities (T1), config (T2), client incl. cites pagination + batch resolver (T3), normalize/classify/fetch + funder-DOI disambiguation + fallback (T4), precedence wiring incl. order-independence (T5), bidirectional expand + caps + seed behavior (T6), doctor/docs/CHANGELOG (T7). All spec sections mapped.
- **Refinement beyond spec:** the `referenced_works` are W-IDs → added the batch ID→DOI resolver (T3) and canonical-edge resolution (T6) so reference edges stay keyed by doi.org and don't fragment. This is the one design detail the spec under-specified.
- **Type consistency:** `OpenAlexWork.references` holds raw W-urls (provenance), `expand` resolves to canonical Edge dst; `openalex_id` field name used consistently across builders/tests; `fallback_adapter` attribute name used in T4 + T5 tests.
- **Open seam:** seed-vs-reached for Author/Institution is implemented as "expand always emits capped works" (T6) because the crawler doesn't queue anchors for expansion when reached via Work edges — flagged in T6 for the reviewer to confirm against the BFS dispatch.
