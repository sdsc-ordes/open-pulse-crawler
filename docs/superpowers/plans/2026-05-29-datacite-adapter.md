# DataCite Commons Adapter Implementation Plan (Spec 4, targets v3.3.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `DataCiteAdapter` so DataCite Commons (`doi.org` for non-Zenodo DOIs + `ror.org` + `orcid.org`) crawls alongside the GitHub / GitLab / Zenodo / Infoscience adapters, with cross-platform `related_to.<RelationType>` edges reusing the shared `synthesize_target_url`. Adds `PlatformRegistry.register_hosts` for multi-host registration; migrates the Zenodo DOI URL rewriter into a generalized `_DOI_PREFIX_REWRITERS` table.

**Architecture:** Single `DataCiteAdapter` instance registered against 5 hosts (`doi.org`, `ror.org`, `orcid.org`, `api.datacite.org`, `commons.datacite.org`). Plain `httpx` `DataCiteHTTPClient` mirroring the Infoscience pattern. Four subkinds: `DataCiteWork`, `DataCiteOrganization`, `DataCitePerson`, `DataCiteClient`. ROR/ORCID nodes are bare anchors (no secondary API calls); `DataCiteClient` is a passive node populated via `published_by` edges from works.

**Tech Stack:** Python 3.10+, Pydantic v2 (discriminated unions), `httpx` (already a dep), pytest, uv. No new third-party libraries.

**Spec:** `docs/superpowers/specs/2026-05-29-datacite-adapter-design.md` (committed `5ba9e95`).

**Branch / worktree:** Same branch as Specs 1+2+3 — `feat/multi-platform-gitlab` at `.worktrees/feat-multi-platform-gitlab/`.

**Test invocation note:** pytest with default `-n auto` hangs in this sandbox. Run with `-n 0`:
```
VIRTUAL_ENV= uv run pytest -n 0 <args>
```

---

## What Specs 1+2+3 already give us — skipped

- `PlatformAdapter` ABC + `PlatformRegistry` (host-keyed dispatch).
- `subkind` discriminator + discriminated-union dict types in `models.py`.
- `node_id.canonical_url` URL helper.
- `config.resolve_tokens(host)` host-keyed env-var resolution.
- Dual-path crawler dispatch (legacy github.com path + adapter path).
- `_FakeAdapter` test helper.
- Per-host cache layout in `APICache`.
- CLI `--platforms` / `--default-host` / `crawler doctor`.
- `/api/v2` REST surface with OpenAPI examples dropdown.
- `platforms/datacite.py` `synthesize_target_url` shared helper.

---

## File map

**New files:**
- `src/open_pulse_crawler/platforms/datacite_adapter/__init__.py`
- `src/open_pulse_crawler/platforms/datacite_adapter/client.py` — `DataCiteHTTPClient` (httpx wrapper)
- `src/open_pulse_crawler/platforms/datacite_adapter/adapter.py` — `DataCiteAdapter`
- `tests/platforms/test_datacite_client.py`
- `tests/platforms/test_datacite_adapter.py`
- `tests/integration/test_datacite_dryrun.py`
- `docs/DATACITE.md`

**Modified files:**
- `src/open_pulse_crawler/platforms/datacite.py` — add `_DOI_PREFIX_REWRITERS` + `rewrite_doi_url` + `is_owned_doi_url`; update `synthesize_target_url` DOI branch to call `rewrite_doi_url`.
- `src/open_pulse_crawler/node_id.py` — remove `rewrite_zenodo_doi_url` + `is_zenodo_doi_url`.
- `src/open_pulse_crawler/platforms/zenodo/adapter.py` — import `rewrite_doi_url` / `is_owned_doi_url` from `..datacite` instead of `..node_id`.
- `src/open_pulse_crawler/platforms/__init__.py` — add `PlatformRegistry.register_hosts(hosts, adapter)`.
- `src/open_pulse_crawler/models.py` — 4 new subclasses, widen 3 discriminated unions.
- `src/open_pulse_crawler/cli.py` — `_build_registry` `datacite.org` branch (anonymous + authenticated).
- `src/open_pulse_crawler/api/v2.py` — 3 new openapi_examples entries.
- `tests/platforms/test_datacite.py` — append `rewrite_doi_url` tests (relocated from `test_node_id.py`).
- `tests/test_node_id.py` — remove the 6 `rewrite_zenodo_doi_url` / `is_zenodo_doi_url` tests.
- `tests/test_models.py` — append 4 DataCite subkind tests.
- `tests/test_cli.py` — append `datacite.org` registration tests.
- `CHANGELOG.md`, `README.md`, `docs/index.md`.

---

## Task ordering

Block A (Tasks 1–2): Foundations — DOI prefix routing migration + four DataCite subkinds.
Block B (Tasks 3–4): `DataCiteHTTPClient` (skeleton + endpoints + 429 retry + cursor pagination).
Block C (Tasks 5–7): `DataCiteAdapter` classify/normalize_uri/fetch/expand.
Block D (Tasks 8–10): `PlatformRegistry.register_hosts`, CLI registration, `/api/v2` examples.
Block E (Tasks 11–13): Integration test, docs/CHANGELOG, final verification.

Every task is TDD-shaped: failing test → minimal implementation → passing test → commit. Conventional Commits per `AGENTS.md`. NEVER pass `--no-verify`, `-c commit.gpgsign=false`, or `--no-gpg-sign`.

---

# Block A — Foundations (routing + models)

## Task 1: Generalize Zenodo DOI rewriter into `_DOI_PREFIX_REWRITERS`

Migrate the prefix-routing logic from `node_id.rewrite_zenodo_doi_url` into `platforms/datacite.py` as a table-driven helper. Zenodo's behavior is unchanged.

**Files:**
- Modify: `src/open_pulse_crawler/platforms/datacite.py`
- Modify: `src/open_pulse_crawler/node_id.py` (remove `rewrite_zenodo_doi_url` + `is_zenodo_doi_url`)
- Modify: `src/open_pulse_crawler/platforms/zenodo/adapter.py` (migrate import)
- Modify: `tests/platforms/test_datacite.py` (append 6 relocated tests)
- Modify: `tests/test_node_id.py` (remove the 2 deleted tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_datacite.py`:

```python
# --- _DOI_PREFIX_REWRITERS / rewrite_doi_url -----------------------
from open_pulse_crawler.platforms.datacite import (
    rewrite_doi_url, is_owned_doi_url,
)


def test_rewrite_doi_url_zenodo_url_form():
    assert rewrite_doi_url("https://doi.org/10.5281/zenodo.42") == \
        "https://zenodo.org/records/42"


def test_rewrite_doi_url_zenodo_bare_doi():
    """The helper accepts a bare DOI string too, not just the URL form."""
    assert rewrite_doi_url("10.5281/zenodo.42") == "https://zenodo.org/records/42"


def test_rewrite_doi_url_sandbox_zenodo():
    assert rewrite_doi_url("https://doi.org/10.5072/zenodo.99") == \
        "https://sandbox.zenodo.org/records/99"
    assert rewrite_doi_url("10.5072/zenodo.99") == \
        "https://sandbox.zenodo.org/records/99"


def test_rewrite_doi_url_unowned_prefix_returns_none():
    assert rewrite_doi_url("https://doi.org/10.6084/m9.figshare.99") is None
    assert rewrite_doi_url("10.6084/m9.figshare.99") is None
    assert rewrite_doi_url("https://doi.org/10.5075/epfl-thesis-12345") is None  # EPFL — not auto-routed


def test_rewrite_doi_url_empty_or_malformed():
    assert rewrite_doi_url("") is None
    assert rewrite_doi_url("not-a-doi") is None
    assert rewrite_doi_url("https://example.com/not-doi") is None


def test_is_owned_doi_url_matches_rewrite_result():
    """`is_owned_doi_url` is the predicate form — True iff rewrite would succeed."""
    assert is_owned_doi_url("https://doi.org/10.5281/zenodo.42") is True
    assert is_owned_doi_url("https://doi.org/10.5072/zenodo.42") is True
    assert is_owned_doi_url("https://doi.org/10.6084/m9.figshare.99") is False
    assert is_owned_doi_url("https://zenodo.org/records/42") is False  # already-rewritten URL
    assert is_owned_doi_url("") is False
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite.py -v --no-cov
```
Expected: `ImportError: cannot import name 'rewrite_doi_url' from 'open_pulse_crawler.platforms.datacite'`.

- [ ] **Step 3: Implement `_DOI_PREFIX_REWRITERS`, `rewrite_doi_url`, `is_owned_doi_url` in `platforms/datacite.py`**

Append to `src/open_pulse_crawler/platforms/datacite.py` (after the existing `synthesize_target_url`):

```python
# ---- DOI prefix routing -----------------------------------------------------
# Maps DOI prefix → (canonical host, URL template using {suffix} for the post-prefix tail).
# When a DOI matches, BFS dispatch routes to the sibling adapter (Zenodo today;
# future Spec entries can add Infoscience or Crossref handlers here).
_DOI_PREFIX_REWRITERS: Dict[str, Tuple[str, str]] = {
    "10.5281": ("zenodo.org",         "https://zenodo.org/records/{suffix}"),
    "10.5072": ("sandbox.zenodo.org", "https://sandbox.zenodo.org/records/{suffix}"),
    # 10.5075 (EPFL) intentionally absent — opaque suffixes, no formulaic mapping
    # to Infoscience handles. Those DOIs flow through DataCite.
}


def rewrite_doi_url(doi_or_url: str) -> Optional[str]:
    """Map a doi.org URL or bare DOI to a sibling-adapter platform URL when
    the DOI's prefix is owned by another adapter (Zenodo today).

    Returns ``None`` to leave the DOI with DataCite. Accepts both forms:

        rewrite_doi_url("https://doi.org/10.5281/zenodo.42")  → "https://zenodo.org/records/42"
        rewrite_doi_url("10.5281/zenodo.42")                  → "https://zenodo.org/records/42"
        rewrite_doi_url("10.6084/m9.figshare.99")             → None
    """
    if not isinstance(doi_or_url, str) or not doi_or_url:
        return None
    # Strip doi.org URL form to bare DOI.
    bare = doi_or_url
    for prefix_url in ("https://doi.org/", "http://doi.org/"):
        if bare.startswith(prefix_url):
            bare = bare[len(prefix_url):]
            break
    # Bare DOI must look like "10.<prefix>/<suffix>".
    if "/" not in bare:
        return None
    prefix, _, suffix = bare.partition("/")
    if not prefix.startswith("10.") or not suffix:
        return None
    entry = _DOI_PREFIX_REWRITERS.get(prefix)
    if not entry:
        return None
    _host, template = entry
    return template.format(suffix=suffix)


def is_owned_doi_url(raw: str) -> bool:
    """True iff ``rewrite_doi_url(raw)`` would return a non-None URL.

    Only matches the ``doi.org`` URL form (so already-rewritten Zenodo URLs
    return False — they're "owned" by Zenodo's host, not by the DOI prefix).
    """
    if not isinstance(raw, str):
        return False
    if not (raw.startswith("https://doi.org/") or raw.startswith("http://doi.org/")):
        return False
    return rewrite_doi_url(raw) is not None
```

Also at the top of the file, ensure `Dict`, `Tuple`, `Optional` are imported (probably already are; if not, add to the existing `typing` import).

- [ ] **Step 4: Update `synthesize_target_url`'s DOI branch**

Find the current DOI branch in `platforms/datacite.py`:

```python
    if scheme == "doi":
        rewritten = rewrite_zenodo_doi_url(f"https://doi.org/{ident}")
        return rewritten or f"https://doi.org/{ident}"
```

Replace with:

```python
    if scheme == "doi":
        return rewrite_doi_url(ident) or f"https://doi.org/{ident}"
```

Remove the unused `from ..node_id import rewrite_zenodo_doi_url` import at the top of `datacite.py`.

- [ ] **Step 5: Update `ZenodoAdapter` to use the new helpers**

In `src/open_pulse_crawler/platforms/zenodo/adapter.py`, find the existing imports:

```python
from ..node_id import (
    rewrite_zenodo_doi_url,
    is_zenodo_doi_url,
    ...
)
```

Replace those two names with imports from `..datacite`:

```python
from ..datacite import rewrite_doi_url, is_owned_doi_url
```

(Keep any other `node_id` imports from the same line intact.)

In `ZenodoAdapter.normalize_uri` (around line 70), find:

```python
        if is_zenodo_doi_url(raw):
            rewritten = rewrite_zenodo_doi_url(raw)
            assert rewritten is not None  # is_zenodo_doi_url guarantees this
            return rewritten
```

Replace with:

```python
        if is_owned_doi_url(raw):
            rewritten = rewrite_doi_url(raw)
            assert rewritten is not None  # is_owned_doi_url guarantees this
            return rewritten
```

- [ ] **Step 6: Remove `rewrite_zenodo_doi_url` and `is_zenodo_doi_url` from `node_id.py`**

In `src/open_pulse_crawler/node_id.py`, delete the entire `def is_zenodo_doi_url(...)` function and the entire `def rewrite_zenodo_doi_url(...)` function. If they're referenced in `__all__`, remove those entries too.

- [ ] **Step 7: Remove the 2 deleted tests from `tests/test_node_id.py`**

Delete these test functions in `tests/test_node_id.py`:
- `test_rewrite_zenodo_doi_url` (or whatever the function name is at lines 244-271; cover all 5 assertions).
- `test_is_zenodo_doi_url` (lines 273-277).

Also update the import block at line 243 — remove `rewrite_zenodo_doi_url, is_zenodo_doi_url` from the `from open_pulse_crawler.node_id import ...` statement. If that line becomes empty after removal, delete the entire import line.

- [ ] **Step 8: Run all affected suites — verify everything still green**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite.py tests/platforms/test_zenodo_adapter.py tests/test_node_id.py -v --no-cov
```
Expected: ~6 new tests pass in test_datacite.py; all remaining Zenodo adapter + node_id tests pass.

- [ ] **Step 9: Commit**

```
git add src/open_pulse_crawler/platforms/datacite.py src/open_pulse_crawler/node_id.py src/open_pulse_crawler/platforms/zenodo/adapter.py tests/platforms/test_datacite.py tests/test_node_id.py
git commit -m "refactor(datacite): generalize Zenodo DOI rewriter into _DOI_PREFIX_REWRITERS"
```

---

## Task 2: Four DataCite subkinds in `models.py`

**Files:**
- Modify: `src/open_pulse_crawler/models.py`
- Modify: `tests/test_models.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_models.py`:

```python
# --- DataCite subkinds (Spec 4) ------------------------------------
from open_pulse_crawler.models import (
    DataCiteWork, DataCiteOrganization, DataCitePerson, DataCiteClient,
)


def test_datacite_work_subkind_and_fields():
    work = DataCiteWork(
        url="https://doi.org/10.6084/m9.figshare.99",
        full_name="10.6084/m9.figshare.99",
        platform="datacite",
        doi="10.6084/m9.figshare.99",
        resource_type="Dataset",
        resource_type_detail="Tabular dataset",
        title="My dataset",
        publication_year=2024,
        publisher="figshare",
        client_id="figshare.ars",
        creators=[
            {"name": "Doe, J.", "orcid": "0000-0001-2345-6789",
             "affiliations": [{"name": "EPFL", "ror": "02s376052", "scheme": "ROR"}]},
        ],
        affiliations=[{"name": "EPFL", "ror": "02s376052", "scheme": "ROR"}],
        relations=[{"relation_type": "IsSupplementTo", "target_type": "URL",
                    "target": "https://github.com/foo/bar"}],
        subjects=["genomics", "open data"],
        abstract="A short description.",
        container_title="Nature",
        language="en",
        registered_url="https://figshare.com/articles/dataset/My_dataset/99",
    )
    assert work.subkind == "DataCiteWork"
    assert work.doi == "10.6084/m9.figshare.99"
    assert work.publication_year == 2024
    assert work.client_id == "figshare.ars"
    assert work.is_fork is False  # inherited; no DataCite fork concept
    assert work.dependents == []


def test_datacite_organization_subkind_and_fields():
    org = DataCiteOrganization(
        url="https://ror.org/02s376052",
        login="02s376052",
        platform="datacite",
        ror_id="02s376052",
        ror_url="https://ror.org/02s376052",
    )
    assert org.subkind == "DataCiteOrganization"
    assert org.ror_id == "02s376052"
    assert org.members == []  # bare anchor — never populated


def test_datacite_person_subkind_and_fields():
    person = DataCitePerson(
        url="https://orcid.org/0000-0002-1825-0097",
        login="0000-0002-1825-0097",
        platform="datacite",
        orcid="0000-0002-1825-0097",
        orcid_url="https://orcid.org/0000-0002-1825-0097",
    )
    assert person.subkind == "DataCitePerson"
    assert person.orcid == "0000-0002-1825-0097"
    assert person.followers == []  # bare anchor — DataCite has no social graph


def test_datacite_client_subkind_and_fields():
    client = DataCiteClient(
        url="https://commons.datacite.org/repositories/cern.zenodo",
        login="cern.zenodo",
        platform="datacite",
        client_id="cern.zenodo",
        repository_name="Zenodo",
        alternate_name="Research. Shared",
        client_type="repository",
        repository_type=["disciplinary"],
        description="ZENODO builds and operates a simple and innovative service…",
        repository_url="https://zenodo.org/",
        domains=["openaire.cern.ch", "zenodo.org"],
        re3data_doi="https://doi.org/10.17616/R3QP53",
        year_registered=2013,
        is_active=True,
        doi_prefixes=["10.5281", "10.5072"],
    )
    assert client.subkind == "DataCiteClient"
    assert client.client_id == "cern.zenodo"
    assert "zenodo.org" in client.domains
    assert client.is_active is True


def test_graphdata_accepts_datacite_subclasses_in_existing_dicts():
    from open_pulse_crawler.models import GraphData
    g = GraphData()
    g.users["https://orcid.org/0000-0002-1825-0097"] = DataCitePerson(
        url="https://orcid.org/0000-0002-1825-0097",
        login="0000-0002-1825-0097", platform="datacite",
        orcid="0000-0002-1825-0097",
        orcid_url="https://orcid.org/0000-0002-1825-0097",
    )
    g.orgs["https://ror.org/02s376052"] = DataCiteOrganization(
        url="https://ror.org/02s376052",
        login="02s376052", platform="datacite",
        ror_id="02s376052",
        ror_url="https://ror.org/02s376052",
    )
    g.orgs["https://commons.datacite.org/repositories/cern.zenodo"] = DataCiteClient(
        url="https://commons.datacite.org/repositories/cern.zenodo",
        login="cern.zenodo", platform="datacite",
        client_id="cern.zenodo",
    )
    g.repos["https://doi.org/10.6084/m9.figshare.99"] = DataCiteWork(
        url="https://doi.org/10.6084/m9.figshare.99",
        full_name="10.6084/m9.figshare.99", platform="datacite",
        doi="10.6084/m9.figshare.99",
    )
    restored = GraphData.model_validate_json(g.model_dump_json())
    assert isinstance(restored.users["https://orcid.org/0000-0002-1825-0097"], DataCitePerson)
    assert isinstance(restored.orgs["https://ror.org/02s376052"], DataCiteOrganization)
    assert isinstance(restored.orgs["https://commons.datacite.org/repositories/cern.zenodo"], DataCiteClient)
    assert isinstance(restored.repos["https://doi.org/10.6084/m9.figshare.99"], DataCiteWork)
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_models.py -v --no-cov
```
Expected: `ImportError: cannot import name 'DataCiteWork'`.

- [ ] **Step 3: Add the four subclasses to `models.py`**

Find the existing Infoscience subclasses (`InfosciencePerson`, `InfoscienceOrgUnit`, `InfoscienceItem`). Add the DataCite subclasses RIGHT AFTER them — BEFORE the three discriminated-union definitions (`UserNode`, `OrgNode`, `RepoNode`):

```python
class DataCitePerson(UserModel):
    """A person identified by ORCID. Bare anchor — name populated
    opportunistically from creator entries seen in DataCiteWork fetches.
    NOT enriched via pub.orcid.org.

    Cross-platform identity resolution stays downstream — a `DataCitePerson`
    is purely a DataCite-vocabulary anchor, distinct from
    `InfosciencePerson` / `ZenodoUser` that may carry the same ORCID.
    """
    subkind: Literal["DataCitePerson"] = "DataCitePerson"
    orcid: str
    orcid_url: str


class DataCiteOrganization(OrgModel):
    """An organization identified by ROR. Bare anchor — name populated
    opportunistically from creator affiliation entries seen in DataCiteWork
    fetches. NOT enriched via api.ror.org.
    """
    subkind: Literal["DataCiteOrganization"] = "DataCiteOrganization"
    ror_id: str
    ror_url: str


class DataCiteClient(OrgModel):
    """A DataCite-registered repository (cern.zenodo, figshare.ars, dryad.dryad…).
    Passive node — ``expand`` emits no edges. Populated when DataCiteWork
    nodes carry ``published_by`` edges pointing at this client.

    Enriched from ``/clients/<id>`` on the round it's fetched: repo type,
    owned domains (cross-host routing hints for downstream tooling), and
    re3data registry cross-reference.
    """
    subkind: Literal["DataCiteClient"] = "DataCiteClient"
    client_id: str
    repository_name: str = ""
    alternate_name: str = ""
    client_type: str = ""
    repository_type: List[str] = Field(default_factory=list)
    description: str = ""
    repository_url: str = ""
    domains: List[str] = Field(default_factory=list)
    re3data_doi: str = ""
    year_registered: Optional[int] = None
    is_active: bool = True
    doi_prefixes: List[str] = Field(default_factory=list)


class DataCiteWork(RepoModel):
    """A DOI registered with DataCite. Cross-repository node —
    could be a Figshare dataset, Dryad submission, ETH WSL dataset, etc.

    `DataCiteWork` is intentionally NEVER produced for DOIs whose prefix
    matches `_DOI_PREFIX_REWRITERS` in `platforms/datacite.py` —
    those URLs are rewritten before classify, so a sibling adapter
    (Zenodo today) handles them.
    """
    subkind: Literal["DataCiteWork"] = "DataCiteWork"
    doi: str
    resource_type: str = ""
    resource_type_detail: str = ""
    title: str = ""
    publication_year: Optional[int] = None
    publisher: str = ""
    client_id: Optional[str] = None
    creators: List[Dict[str, Any]] = Field(default_factory=list)
    affiliations: List[Dict[str, Any]] = Field(default_factory=list)
    relations: List[Dict[str, Any]] = Field(default_factory=list)
    subjects: List[str] = Field(default_factory=list)
    abstract: str = ""
    container_title: str = ""
    language: str = ""
    registered_url: Optional[str] = None
```

Then widen the three discriminated unions (find the existing `UserNode`/`OrgNode`/`RepoNode` definitions):

```python
UserNode = Annotated[
    Union[UserModel, GitLabUserModel, ZenodoUserModel, InfosciencePerson,
          DataCitePerson],
    Field(discriminator="subkind"),
]
OrgNode = Annotated[
    Union[OrgModel, GitLabGroupModel, ZenodoCommunityModel, InfoscienceOrgUnit,
          DataCiteOrganization, DataCiteClient],
    Field(discriminator="subkind"),
]
RepoNode = Annotated[
    Union[RepoModel, GitLabProjectModel, ZenodoRecordModel, InfoscienceItem,
          DataCiteWork],
    Field(discriminator="subkind"),
]
```

`Any`, `Dict`, `List`, `Literal`, `Optional`, `Union`, `Annotated`, `Field` should already be imported.

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_models.py -v --no-cov
```
Expected: all 5 new DataCite tests pass; all existing tests still green.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/models.py tests/test_models.py
git commit -m "feat(models): DataCite subkinds — Work, Organization, Person, Client"
```

---

# Block B — `DataCiteHTTPClient`

## Task 3: `DataCiteHTTPClient` skeleton + Bearer auth + token rotation

**Files:**
- Create: `src/open_pulse_crawler/platforms/datacite_adapter/__init__.py`
- Create: `src/open_pulse_crawler/platforms/datacite_adapter/client.py`
- Create: `tests/platforms/test_datacite_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/platforms/test_datacite_client.py
"""Tests for the DataCite HTTP client wrapper."""
from unittest.mock import MagicMock, patch
import pytest
import httpx

from open_pulse_crawler.platforms.datacite_adapter.client import DataCiteHTTPClient


def test_authenticated_sets_bearer_header():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=["dc-tok"])
    assert c._session.headers.get("Authorization") == "Bearer dc-tok"


def test_anonymous_sends_no_auth_header():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    assert "Authorization" not in c._session.headers


def test_base_url_uses_https_with_host():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    assert c.base_url == "https://api.datacite.org"


def test_session_accepts_jsonapi_content_type():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    assert c._session.headers["Accept"] == "application/vnd.api+json"


def test_rotate_in_anonymous_mode_is_noop():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    c._rotate()
    assert "Authorization" not in c._session.headers


def test_rotate_cycles_token_pool():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=["a", "b"])
    assert c._session.headers["Authorization"] == "Bearer a"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer b"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer a"
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite_client.py -v --no-cov
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the skeleton**

```python
# src/open_pulse_crawler/platforms/datacite_adapter/__init__.py
"""DataCite Commons platform implementation."""
from .client import DataCiteHTTPClient

__all__ = ["DataCiteHTTPClient"]
```

```python
# src/open_pulse_crawler/platforms/datacite_adapter/client.py
"""Thin httpx wrapper for DataCite's REST API.

Mirrors the Infoscience client's shape but speaks JSON:API (vnd.api+json)
content type. Pagination follows ``links.next``. 429 responses honor the
``Retry-After`` header with one automatic retry, then raise.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

# DataCite's default page size is 25; max is 1000. We default to 100
# (matches Infoscience for consistency; raises by 4× without hitting the cap).
DEFAULT_PAGE_SIZE = 100

# Cap Retry-After honored — avoid hanging on a rogue server response.
MAX_RETRY_AFTER_SECONDS = 60


class DataCiteHTTPClient:
    """HTTP client for ``api.datacite.org`` endpoints.

    Authentication: ``Authorization: Bearer <token>`` when ``tokens`` is
    non-empty. Anonymous (no header) when ``tokens=[]`` — DataCite's
    public reads return 200 for ``/dois``, ``/clients``, search endpoints.

    Rate-limit handling: on HTTP 429, the client reads the ``Retry-After``
    header (or defaults to 5 seconds), sleeps, and retries once. A second
    429 raises ``httpx.HTTPStatusError``.
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
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            headers={"Accept": "application/vnd.api+json"},
        )
        if not self.tokens:
            logger.warning(
                "DataCiteHTTPClient(%s) constructed with no tokens — "
                "anonymous reads only; rate limits will be tighter.",
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

    # ---- token rotation ---------------------------------------------------

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
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite_client.py -v --no-cov
```
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/datacite_adapter/__init__.py src/open_pulse_crawler/platforms/datacite_adapter/client.py tests/platforms/test_datacite_client.py
git commit -m "feat(datacite): DataCiteHTTPClient skeleton (Bearer auth + token rotation)"
```

---

## Task 4: Endpoints + 429 retry + cursor pagination

**Files:**
- Modify: `src/open_pulse_crawler/platforms/datacite_adapter/client.py`
- Modify: `tests/platforms/test_datacite_client.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_datacite_client.py`:

```python
def _make_response(status_code: int, json_body=None, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.is_success = 200 <= status_code < 300
    r.json.return_value = json_body or {}
    r.headers = headers or {}
    if not (200 <= status_code < 300):
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}", request=MagicMock(), response=r,
        )
    else:
        r.raise_for_status.return_value = None
    return r


def test_get_doi_200_returns_data_dict():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    payload = {"data": {"id": "10.5281/zenodo.42",
                        "attributes": {"titles": [{"title": "x"}]}}}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_doi("10.5281/zenodo.42")
    g.assert_called_once_with("/dois/10.5281/zenodo.42")
    assert result == payload["data"]


def test_get_doi_404_returns_none():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_doi("10.1038/missing") is None


def test_get_client_200_returns_data_dict():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    payload = {"data": {"id": "cern.zenodo", "attributes": {"name": "Zenodo"}}}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_client("cern.zenodo")
    g.assert_called_once_with("/clients/cern.zenodo")
    assert result == payload["data"]


def test_get_client_prefixes_200_returns_list():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    payload = {"data": [{"id": "10.5281"}, {"id": "10.5072"}]}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_client_prefixes("cern.zenodo")
    g.assert_called_once_with("/clients/cern.zenodo/relationships/prefixes")
    assert result == ["10.5281", "10.5072"]


def test_429_retries_after_retry_after_header(monkeypatch):
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    responses = [
        _make_response(429, headers={"Retry-After": "1"}),
        _make_response(200, {"data": {"id": "10.x/y", "attributes": {}}}),
    ]
    sleeps = []
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.datacite_adapter.client.time.sleep",
        lambda s: sleeps.append(s),
    )
    with patch.object(c._session, "get", side_effect=responses):
        result = c.get_doi("10.x/y")
    assert result == {"id": "10.x/y", "attributes": {}}
    assert sleeps == [1.0]


def test_429_caps_retry_after_at_60s(monkeypatch):
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    sleeps = []
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.datacite_adapter.client.time.sleep",
        lambda s: sleeps.append(s),
    )
    responses = [_make_response(429, headers={"Retry-After": "9999"}),
                 _make_response(200, {"data": {"id": "x", "attributes": {}}})]
    with patch.object(c._session, "get", side_effect=responses):
        c.get_doi("x")
    assert sleeps == [60.0]


def test_429_twice_raises(monkeypatch):
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.datacite_adapter.client.time.sleep",
        lambda s: None,
    )
    responses = [_make_response(429, headers={"Retry-After": "0"}),
                 _make_response(429)]
    with patch.object(c._session, "get", side_effect=responses):
        with pytest.raises(httpx.HTTPStatusError):
            c.get_doi("x")


def test_iter_dois_by_ror_single_page():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    body = {
        "data": [
            {"id": "10.1/x", "attributes": {"doi": "10.1/x"}},
            {"id": "10.2/y", "attributes": {"doi": "10.2/y"}},
        ],
        "links": {},
        "meta": {"total": 2},
    }
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        works = list(c.iter_dois_by_ror("https://ror.org/02s376052"))
    g.assert_called_once_with(
        "/dois",
        params={
            "query": 'creators.affiliation.affiliationIdentifier:"https://ror.org/02s376052"',
            "page[size]": 100,
            "page[cursor]": 1,
        },
    )
    assert [w["id"] for w in works] == ["10.1/x", "10.2/y"]


def test_iter_dois_by_ror_follows_links_next():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    page1 = {
        "data": [{"id": "10.1/x", "attributes": {}}],
        "links": {"next": "https://api.datacite.org/dois?query=...&page[cursor]=2"},
    }
    page2 = {
        "data": [{"id": "10.2/y", "attributes": {}}],
        "links": {},
    }
    with patch.object(c._session, "get") as g:
        g.side_effect = [_make_response(200, page1), _make_response(200, page2)]
        works = list(c.iter_dois_by_ror("https://ror.org/02s376052"))
    assert [w["id"] for w in works] == ["10.1/x", "10.2/y"]
    assert g.call_count == 2


def test_iter_dois_by_orcid_query_shape():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    body = {"data": [], "links": {}}
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        list(c.iter_dois_by_orcid("https://orcid.org/0000-0002-1825-0097"))
    g.assert_called_once_with(
        "/dois",
        params={
            "query": 'creators.nameIdentifiers.nameIdentifier:"https://orcid.org/0000-0002-1825-0097"',
            "page[size]": 100,
            "page[cursor]": 1,
        },
    )
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite_client.py -v --no-cov
```
Expected: `AttributeError: 'DataCiteHTTPClient' object has no attribute 'get_doi'`.

- [ ] **Step 3: Append endpoints + 429 retry + pagination**

Append to `src/open_pulse_crawler/platforms/datacite_adapter/client.py`:

```python
    # ---- single-entity fetches with 429 retry ------------------------------

    def _do_get(self, path: str, params: Optional[Dict[str, Any]] = None,
                _retried: bool = False) -> httpx.Response:
        """GET ``path`` with one automatic retry on HTTP 429.

        Honors the ``Retry-After`` header (capped at ``MAX_RETRY_AFTER_SECONDS``).
        Second 429 raises via the caller's ``raise_for_status``.
        """
        resp = (self._session.get(path, params=params)
                if params is not None else self._session.get(path))
        if resp.status_code == 429 and not _retried:
            retry_after_raw = resp.headers.get("Retry-After", "5")
            try:
                retry_after = float(retry_after_raw)
            except (TypeError, ValueError):
                retry_after = 5.0
            retry_after = min(max(retry_after, 0.0), MAX_RETRY_AFTER_SECONDS)
            logger.warning(
                "%s on %s returned 429; sleeping %.1fs then retrying once.",
                path, self.host, retry_after,
            )
            time.sleep(retry_after)
            return self._do_get(path, params=params, _retried=True)
        return resp

    def _request_data(
        self, path: str, params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """GET ``path`` and return the JSON:API ``data`` payload.

        404 → ``None``. Any other non-2xx raises ``httpx.HTTPStatusError``.
        """
        resp = self._do_get(path, params)
        if resp.status_code == 404:
            return None
        if not resp.is_success:
            resp.raise_for_status()
        body = resp.json()
        return body.get("data")

    def get_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """Return the JSON:API ``data`` for the given DOI, or ``None`` on 404."""
        return self._request_data(f"/dois/{doi}")

    def get_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Return the JSON:API ``data`` for the given DataCite client id,
        or ``None`` on 404.
        """
        return self._request_data(f"/clients/{client_id}")

    def get_client_prefixes(self, client_id: str) -> List[str]:
        """Return the DOI prefixes owned by the given client (empty on 404)."""
        data = self._request_data(f"/clients/{client_id}/relationships/prefixes")
        if not data:
            return []
        return [p.get("id", "") for p in data if isinstance(p, dict) and p.get("id")]

    # ---- cursor-paginated search ------------------------------------------

    def _iter_dois_query(
        self, query: str,
    ) -> Iterable[Dict[str, Any]]:
        """Yield DOI records across all pages of a /dois search.

        First page uses ``page[cursor]=1``; subsequent pages follow
        ``links.next`` (absolute URL, params baked in).
        """
        path: str = "/dois"
        current_params: Optional[Dict[str, Any]] = {
            "query": query,
            "page[size]": DEFAULT_PAGE_SIZE,
            "page[cursor]": 1,
        }
        while True:
            resp = self._do_get(path, current_params)
            if not resp.is_success:
                resp.raise_for_status()
            body = resp.json()
            for item in body.get("data") or []:
                yield item
            next_link = (body.get("links") or {}).get("next")
            if not next_link:
                return
            path = next_link
            current_params = None  # next-link is absolute, params already in URL

    def iter_dois_by_ror(self, ror_url: str) -> Iterable[Dict[str, Any]]:
        """Yield DOI records affiliated with the given ROR organization."""
        return self._iter_dois_query(
            f'creators.affiliation.affiliationIdentifier:"{ror_url}"',
        )

    def iter_dois_by_orcid(self, orcid_url: str) -> Iterable[Dict[str, Any]]:
        """Yield DOI records authored by the given ORCID identifier."""
        return self._iter_dois_query(
            f'creators.nameIdentifiers.nameIdentifier:"{orcid_url}"',
        )
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite_client.py -v --no-cov
```
Expected: 16 tests pass (6 from Task 3 + 10 new).

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/datacite_adapter/client.py tests/platforms/test_datacite_client.py
git commit -m "feat(datacite): client endpoints + 429 retry-after + cursor pagination"
```

---

# Block C — `DataCiteAdapter`

## Task 5: Adapter classify + normalize_uri (10 input forms)

**Files:**
- Create: `src/open_pulse_crawler/platforms/datacite_adapter/adapter.py`
- Create: `tests/platforms/test_datacite_adapter.py`
- Modify: `src/open_pulse_crawler/platforms/datacite_adapter/__init__.py` (add re-export)

- [ ] **Step 1: Write failing tests**

```python
# tests/platforms/test_datacite_adapter.py
"""Tests for the DataCite PlatformAdapter."""
from unittest.mock import MagicMock
import pytest

from open_pulse_crawler.models import (
    DataCiteWork, DataCiteOrganization, DataCitePerson, DataCiteClient,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter


@pytest.fixture
def adapter():
    client = MagicMock()
    return DataCiteAdapter(client=client, instance_host="api.datacite.org")


# --- classify -------------------------------------------------------

def test_classify_doi_url(adapter):
    assert adapter.classify("https://doi.org/10.6084/m9.figshare.99") == NodeKind.REPO


def test_classify_ror_url(adapter):
    assert adapter.classify("https://ror.org/02s376052") == NodeKind.ORG


def test_classify_orcid_url(adapter):
    assert adapter.classify("https://orcid.org/0000-0002-1825-0097") == NodeKind.USER


def test_classify_client_url(adapter):
    assert adapter.classify("https://commons.datacite.org/repositories/cern.zenodo") == NodeKind.ORG


def test_classify_unknown_path(adapter):
    assert adapter.classify("https://api.datacite.org/somethingelse") is None


# --- normalize_uri --------------------------------------------------

def test_normalize_doi_url_with_owned_zenodo_prefix_rewrites(adapter):
    """A 10.5281/zenodo.X DOI URL gets rewritten to its Zenodo URL,
    so BFS dispatches to the Zenodo adapter — DataCite never sees it."""
    assert adapter.normalize_uri("https://doi.org/10.5281/zenodo.42") == \
        "https://zenodo.org/records/42"


def test_normalize_doi_url_with_unowned_prefix_unchanged(adapter):
    assert adapter.normalize_uri("https://doi.org/10.6084/m9.figshare.99") == \
        "https://doi.org/10.6084/m9.figshare.99"


def test_normalize_api_datacite_dois_rewrites_to_doi_org(adapter):
    assert adapter.normalize_uri("https://api.datacite.org/dois/10.6084/m9.figshare.99") == \
        "https://doi.org/10.6084/m9.figshare.99"


def test_normalize_commons_doi_alias(adapter):
    assert adapter.normalize_uri("https://commons.datacite.org/doi.org/10.6084/m9.figshare.99") == \
        "https://doi.org/10.6084/m9.figshare.99"


def test_normalize_ror_strips_trailing_slash(adapter):
    assert adapter.normalize_uri("https://ror.org/02s376052/") == \
        "https://ror.org/02s376052"


def test_normalize_commons_ror_alias(adapter):
    assert adapter.normalize_uri("https://commons.datacite.org/ror.org/02s376052") == \
        "https://ror.org/02s376052"


def test_normalize_orcid_strips_query_and_trailing_slash(adapter):
    assert adapter.normalize_uri("https://orcid.org/0000-0002-1825-0097/?x=1") == \
        "https://orcid.org/0000-0002-1825-0097"


def test_normalize_commons_orcid_alias(adapter):
    assert adapter.normalize_uri("https://commons.datacite.org/orcid.org/0000-0002-1825-0097") == \
        "https://orcid.org/0000-0002-1825-0097"


def test_normalize_api_clients_rewrites_to_commons(adapter):
    assert adapter.normalize_uri("https://api.datacite.org/clients/cern.zenodo") == \
        "https://commons.datacite.org/repositories/cern.zenodo"


def test_normalize_commons_repositories_canonical(adapter):
    assert adapter.normalize_uri("https://commons.datacite.org/repositories/cern.zenodo") == \
        "https://commons.datacite.org/repositories/cern.zenodo"
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite_adapter.py -v --no-cov
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement classify + normalize_uri**

```python
# src/open_pulse_crawler/platforms/datacite_adapter/adapter.py
"""DataCite Commons PlatformAdapter — classify / fetch / expand / normalize_uri.

See ``docs/superpowers/specs/2026-05-29-datacite-adapter-design.md``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Dict, Iterable, List, Optional
from urllib.parse import urlparse, urlsplit

from ...models import (
    DataCiteWork, DataCiteOrganization, DataCitePerson, DataCiteClient,
)
from ...node_id import NodeKind, canonical_url
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo
from ..datacite import rewrite_doi_url, synthesize_target_url
from .client import DataCiteHTTPClient

logger = logging.getLogger(__name__)

_DOI_PATH = re.compile(r"^(?P<doi>10\.[^/]+/.+)$")
_ROR_PATH = re.compile(r"^(?P<id>[a-z0-9]+)/?$")
_ORCID_PATH = re.compile(r"^(?P<id>\d{4}-\d{4}-\d{4}-\d{3}[\dX])/?$")
_API_DOI_PATH = re.compile(r"^dois/(?P<doi>10\.[^/]+/.+)$")
_API_CLIENT_PATH = re.compile(r"^clients/(?P<id>[a-z0-9.\-_]+)$")
_COMMONS_REPO_PATH = re.compile(r"^repositories/(?P<id>[a-z0-9.\-_]+)$")
_COMMONS_DOI_ALIAS = re.compile(r"^doi\.org/(?P<doi>10\.[^/]+/.+)$")
_COMMONS_ROR_ALIAS = re.compile(r"^ror\.org/(?P<id>[a-z0-9]+)$")
_COMMONS_ORCID_ALIAS = re.compile(r"^orcid\.org/(?P<id>\d{4}-\d{4}-\d{4}-\d{3}[\dX])$")


class DataCiteAdapter(PlatformAdapter):
    platform: ClassVar[str] = "datacite"

    def __init__(self, client: DataCiteHTTPClient, instance_host: str) -> None:
        self._client = client
        self.instance_host = instance_host

    # ---- normalize_uri -----------------------------------------------------

    def normalize_uri(self, raw: str) -> str:
        """Return the canonical DataCite-adapter URL for ``raw``.

        Accepts doi.org / ror.org / orcid.org / api.datacite.org / commons.datacite.org
        URL forms. For doi.org URLs whose prefix is owned by a sibling
        adapter (Zenodo today), rewrites to that adapter's canonical URL so
        the BFS dispatch routes there.
        """
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"empty or non-string seed: {raw!r}")

        parts = urlsplit(raw)
        host = (parts.netloc or self.instance_host).lower()
        # Strip query + fragment; collapse trailing slash on path.
        path = (parts.path or "/").rstrip("/")
        path_inner = path.lstrip("/")

        # --- doi.org ---
        if host == "doi.org":
            m = _DOI_PATH.match(path_inner)
            if m:
                doi = m.group("doi")
                rewritten = rewrite_doi_url(f"https://doi.org/{doi}")
                if rewritten:
                    return rewritten
                return f"https://doi.org/{doi}"

        # --- ror.org ---
        if host == "ror.org":
            m = _ROR_PATH.match(path_inner)
            if m:
                return f"https://ror.org/{m.group('id')}"

        # --- orcid.org ---
        if host == "orcid.org":
            m = _ORCID_PATH.match(path_inner)
            if m:
                return f"https://orcid.org/{m.group('id')}"

        # --- api.datacite.org ---
        if host == "api.datacite.org":
            m = _API_DOI_PATH.match(path_inner)
            if m:
                doi = m.group("doi")
                rewritten = rewrite_doi_url(f"https://doi.org/{doi}")
                return rewritten or f"https://doi.org/{doi}"
            m = _API_CLIENT_PATH.match(path_inner)
            if m:
                return f"https://commons.datacite.org/repositories/{m.group('id')}"

        # --- commons.datacite.org ---
        if host == "commons.datacite.org":
            m = _COMMONS_REPO_PATH.match(path_inner)
            if m:
                return f"https://commons.datacite.org/repositories/{m.group('id')}"
            m = _COMMONS_DOI_ALIAS.match(path_inner)
            if m:
                doi = m.group("doi")
                rewritten = rewrite_doi_url(f"https://doi.org/{doi}")
                return rewritten or f"https://doi.org/{doi}"
            m = _COMMONS_ROR_ALIAS.match(path_inner)
            if m:
                return f"https://ror.org/{m.group('id')}"
            m = _COMMONS_ORCID_ALIAS.match(path_inner)
            if m:
                return f"https://orcid.org/{m.group('id')}"

        # Fallback: hand back the canonicalized form for the BFS to drop.
        return canonical_url(host, path or "/")

    # ---- classify ----------------------------------------------------------

    def classify(self, uri: str) -> Optional[NodeKind]:
        """Return the NodeKind for ``uri``, based on URL host/path shape."""
        normed = self.normalize_uri(uri)
        parts = urlsplit(normed)
        host = parts.netloc.lower()
        path_inner = (parts.path or "/").lstrip("/").rstrip("/")
        if host == "doi.org" and _DOI_PATH.match(path_inner):
            return NodeKind.REPO
        if host == "ror.org" and _ROR_PATH.match(path_inner):
            return NodeKind.ORG
        if host == "orcid.org" and _ORCID_PATH.match(path_inner):
            return NodeKind.USER
        if host == "commons.datacite.org" and _COMMONS_REPO_PATH.match(path_inner):
            return NodeKind.ORG
        return None

    # ---- fetch (placeholder — Task 6 implements) ---------------------------

    def fetch(self, uri: str):
        raise NotImplementedError("Task 6 implements fetch()")

    # ---- expand (placeholder — Task 7 implements) --------------------------

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        raise NotImplementedError("Task 7 implements expand()")

    # ---- rate_limit --------------------------------------------------------

    def rate_limit_state(self) -> RateLimitInfo:
        # DataCite doesn't surface rate-limit headers reliably; conservative
        # placeholders matching the Infoscience adapter pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
```

Update `__init__.py`:

```python
# src/open_pulse_crawler/platforms/datacite_adapter/__init__.py
"""DataCite Commons platform implementation."""
from .client import DataCiteHTTPClient
from .adapter import DataCiteAdapter

__all__ = ["DataCiteHTTPClient", "DataCiteAdapter"]
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite_adapter.py -v --no-cov
```
Expected: 14 tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/datacite_adapter/adapter.py src/open_pulse_crawler/platforms/datacite_adapter/__init__.py tests/platforms/test_datacite_adapter.py
git commit -m "feat(datacite): DataCiteAdapter classify + normalize_uri (10 input forms)"
```

---

## Task 6: Adapter fetch (URL-shape dispatch + 4 builders)

**Files:**
- Modify: `src/open_pulse_crawler/platforms/datacite_adapter/adapter.py`
- Modify: `tests/platforms/test_datacite_adapter.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_datacite_adapter.py`:

```python
def test_fetch_doi_returns_work(adapter):
    adapter._client.get_doi.return_value = {
        "id": "10.6084/m9.figshare.99",
        "type": "dois",
        "attributes": {
            "doi": "10.6084/m9.figshare.99",
            "types": {"resourceTypeGeneral": "Dataset", "resourceType": "Tabular"},
            "titles": [{"title": "A study of X"}],
            "publicationYear": 2024,
            "publisher": "figshare",
            "url": "https://figshare.com/articles/dataset/A_study/99",
            "language": "en",
            "creators": [
                {"name": "Doe, J.",
                 "nameIdentifiers": [{"nameIdentifier": "https://orcid.org/0000-0001-2345-6789",
                                      "nameIdentifierScheme": "ORCID"}],
                 "affiliation": [
                     {"name": "EPFL",
                      "affiliationIdentifier": "https://ror.org/02s376052",
                      "affiliationIdentifierScheme": "ROR"},
                 ]},
            ],
            "subjects": [{"subject": "kw1"}, {"subject": "kw2"}],
            "descriptions": [{"description": "An abstract.", "descriptionType": "Abstract"}],
            "container": {"title": "Nature"},
            "relatedIdentifiers": [
                {"relationType": "IsSupplementTo",
                 "relatedIdentifierType": "URL",
                 "relatedIdentifier": "https://github.com/foo/bar"},
            ],
        },
        "relationships": {"client": {"data": {"id": "figshare.ars"}}},
    }
    node = adapter.fetch("https://doi.org/10.6084/m9.figshare.99")
    assert isinstance(node, DataCiteWork)
    assert node.doi == "10.6084/m9.figshare.99"
    assert node.resource_type == "Dataset"
    assert node.resource_type_detail == "Tabular"
    assert node.title == "A study of X"
    assert node.publication_year == 2024
    assert node.publisher == "figshare"
    assert node.client_id == "figshare.ars"
    assert node.language == "en"
    assert node.registered_url == "https://figshare.com/articles/dataset/A_study/99"
    assert node.subjects == ["kw1", "kw2"]
    assert node.abstract == "An abstract."
    assert node.container_title == "Nature"
    # creator with ORCID + ROR is extracted to typed creators list
    assert len(node.creators) == 1
    cre = node.creators[0]
    assert cre["name"] == "Doe, J."
    assert cre["orcid"] == "0000-0001-2345-6789"
    assert cre["affiliations"][0]["ror"] == "02s376052"
    assert cre["affiliations"][0]["name"] == "EPFL"
    assert cre["affiliations"][0]["scheme"] == "ROR"
    # deduped affiliations + relations
    assert any(a["ror"] == "02s376052" for a in node.affiliations)
    assert node.relations[0]["relation_type"] == "IsSupplementTo"
    assert node.relations[0]["target"] == "https://github.com/foo/bar"


def test_fetch_doi_404_returns_none(adapter):
    adapter._client.get_doi.return_value = None
    assert adapter.fetch("https://doi.org/10.6084/m9.figshare.missing") is None


def test_fetch_ror_returns_bare_organization(adapter):
    """ROR fetch makes NO network call — the node is constructed from URL alone."""
    node = adapter.fetch("https://ror.org/02s376052")
    assert isinstance(node, DataCiteOrganization)
    assert node.ror_id == "02s376052"
    assert node.ror_url == "https://ror.org/02s376052"
    assert node.name == ""  # bare anchor — name comes from creator entries elsewhere
    adapter._client.get_doi.assert_not_called()
    adapter._client.get_client.assert_not_called()


def test_fetch_orcid_returns_bare_person(adapter):
    """ORCID fetch makes NO network call — the node is constructed from URL alone."""
    node = adapter.fetch("https://orcid.org/0000-0002-1825-0097")
    assert isinstance(node, DataCitePerson)
    assert node.orcid == "0000-0002-1825-0097"
    assert node.orcid_url == "https://orcid.org/0000-0002-1825-0097"
    assert node.login == "0000-0002-1825-0097"


def test_fetch_client_returns_datacite_client(adapter):
    adapter._client.get_client.return_value = {
        "id": "cern.zenodo",
        "type": "clients",
        "attributes": {
            "name": "Zenodo",
            "alternateName": "Research. Shared",
            "symbol": "CERN.ZENODO",
            "year": 2013,
            "clientType": "repository",
            "repositoryType": ["disciplinary"],
            "description": "ZENODO builds and operates …",
            "url": "https://zenodo.org/",
            "domains": "openaire.cern.ch,zenodo.org",
            "re3data": "https://doi.org/10.17616/R3QP53",
            "isActive": True,
            "language": ["en"],
        },
    }
    adapter._client.get_client_prefixes.return_value = ["10.5281", "10.5072"]
    node = adapter.fetch("https://commons.datacite.org/repositories/cern.zenodo")
    assert isinstance(node, DataCiteClient)
    assert node.client_id == "cern.zenodo"
    assert node.repository_name == "Zenodo"
    assert node.alternate_name == "Research. Shared"
    assert node.client_type == "repository"
    assert node.repository_type == ["disciplinary"]
    assert node.repository_url == "https://zenodo.org/"
    assert node.domains == ["openaire.cern.ch", "zenodo.org"]
    assert node.re3data_doi == "https://doi.org/10.17616/R3QP53"
    assert node.year_registered == 2013
    assert node.is_active is True
    assert node.doi_prefixes == ["10.5281", "10.5072"]


def test_fetch_client_404_returns_none(adapter):
    adapter._client.get_client.return_value = None
    assert adapter.fetch("https://commons.datacite.org/repositories/missing") is None


def test_fetch_unknown_url_form_returns_none(adapter):
    assert adapter.fetch("https://api.datacite.org/somethingelse") is None
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite_adapter.py -v --no-cov
```
Expected: 7 new tests fail with `NotImplementedError`.

- [ ] **Step 3: Implement fetch + four builders**

In `src/open_pulse_crawler/platforms/datacite_adapter/adapter.py`, replace the `fetch` placeholder and add the four builders. Append after the `classify` method:

```python
    # ---- fetch -------------------------------------------------------------

    def fetch(self, uri: str):
        uri = self.normalize_uri(uri)
        parts = urlsplit(uri)
        host = parts.netloc.lower()
        path_inner = (parts.path or "/").lstrip("/").rstrip("/")

        # doi.org/<DOI> → DataCiteWork via API
        if host == "doi.org":
            m = _DOI_PATH.match(path_inner)
            if m:
                doi = m.group("doi")
                raw = self._client.get_doi(doi)
                if raw is None:
                    return None
                return self._build_work(uri, raw)

        # ror.org/<id> → bare DataCiteOrganization (no API call)
        if host == "ror.org":
            m = _ROR_PATH.match(path_inner)
            if m:
                return self._build_org_bare(uri, m.group("id"))

        # orcid.org/<id> → bare DataCitePerson (no API call)
        if host == "orcid.org":
            m = _ORCID_PATH.match(path_inner)
            if m:
                return self._build_person_bare(uri, m.group("id"))

        # commons.datacite.org/repositories/<client_id> → DataCiteClient via API
        if host == "commons.datacite.org":
            m = _COMMONS_REPO_PATH.match(path_inner)
            if m:
                client_id = m.group("id")
                raw = self._client.get_client(client_id)
                if raw is None:
                    return None
                prefixes = self._client.get_client_prefixes(client_id)
                return self._build_client(uri, raw, prefixes)

        return None

    # ---- builders ----------------------------------------------------------

    def _build_work(self, uri: str, raw: Dict[str, Any]) -> DataCiteWork:
        attr = raw.get("attributes", {}) or {}
        types = attr.get("types", {}) or {}
        titles = attr.get("titles", []) or []
        title = titles[0].get("title", "") if titles and isinstance(titles[0], dict) else ""
        descriptions = attr.get("descriptions", []) or []
        abstract = ""
        for d in descriptions:
            if isinstance(d, dict) and d.get("descriptionType") == "Abstract":
                abstract = d.get("description", "") or ""
                break

        creators = []
        affiliations_dedup: List[Dict[str, Any]] = []
        seen_ror: set = set()
        for c in attr.get("creators", []) or []:
            if not isinstance(c, dict):
                continue
            orcid = ""
            for nid in c.get("nameIdentifiers", []) or []:
                if (isinstance(nid, dict) and
                        nid.get("nameIdentifierScheme") == "ORCID"):
                    raw_id = nid.get("nameIdentifier", "") or ""
                    # Normalize "https://orcid.org/0000-…" → "0000-…"
                    orcid = raw_id.rsplit("/", 1)[-1] if raw_id else ""
                    break
            affs: List[Dict[str, Any]] = []
            for a in c.get("affiliation", []) or []:
                if isinstance(a, dict):
                    raw_id = a.get("affiliationIdentifier", "") or ""
                    ror = ""
                    scheme = a.get("affiliationIdentifierScheme", "") or ""
                    if scheme == "ROR" and raw_id:
                        ror = raw_id.rsplit("/", 1)[-1]
                    entry = {"name": a.get("name", "") or "",
                             "ror": ror, "scheme": scheme}
                    affs.append(entry)
                    if ror and ror not in seen_ror:
                        seen_ror.add(ror)
                        affiliations_dedup.append(entry)
                elif isinstance(a, str):
                    # String-form affiliation (no identifier scheme)
                    affs.append({"name": a, "ror": "", "scheme": ""})
            creators.append({
                "name": c.get("name", "") or "",
                "orcid": orcid,
                "affiliations": affs,
            })

        relations = []
        for r in attr.get("relatedIdentifiers", []) or []:
            if not isinstance(r, dict):
                continue
            relations.append({
                "relation_type": r.get("relationType", "") or "",
                "target_type": r.get("relatedIdentifierType", "") or "",
                "target": r.get("relatedIdentifier", "") or "",
            })

        subjects = []
        for s in attr.get("subjects", []) or []:
            if isinstance(s, dict):
                v = s.get("subject", "") or ""
                if v:
                    subjects.append(v)

        container_title = ""
        container = attr.get("container", {})
        if isinstance(container, dict):
            container_title = container.get("title", "") or ""

        client_id = None
        rels = raw.get("relationships", {})
        if isinstance(rels, dict):
            client_rel = rels.get("client", {}).get("data", {})
            if isinstance(client_rel, dict):
                client_id = client_rel.get("id") or None

        pub_year_raw = attr.get("publicationYear")
        try:
            pub_year = int(pub_year_raw) if pub_year_raw is not None else None
        except (TypeError, ValueError):
            pub_year = None

        return DataCiteWork(
            url=uri,
            full_name=attr.get("doi", raw.get("id", "")),
            platform="datacite",
            name=title,
            doi=attr.get("doi", raw.get("id", "")) or "",
            resource_type=types.get("resourceTypeGeneral", "") or "",
            resource_type_detail=types.get("resourceType", "") or "",
            title=title,
            publication_year=pub_year,
            publisher=attr.get("publisher", "") or "",
            client_id=client_id,
            creators=creators,
            affiliations=affiliations_dedup,
            relations=relations,
            subjects=subjects,
            abstract=abstract,
            container_title=container_title,
            language=attr.get("language", "") or "",
            registered_url=attr.get("url") or None,
        )

    def _build_org_bare(self, uri: str, ror_id: str) -> DataCiteOrganization:
        return DataCiteOrganization(
            url=uri,
            login=ror_id,
            platform="datacite",
            ror_id=ror_id,
            ror_url=f"https://ror.org/{ror_id}",
        )

    def _build_person_bare(self, uri: str, orcid: str) -> DataCitePerson:
        return DataCitePerson(
            url=uri,
            login=orcid,
            platform="datacite",
            orcid=orcid,
            orcid_url=f"https://orcid.org/{orcid}",
        )

    def _build_client(
        self, uri: str, raw: Dict[str, Any], prefixes: List[str],
    ) -> DataCiteClient:
        attr = raw.get("attributes", {}) or {}
        domains_raw = attr.get("domains", "") or ""
        if isinstance(domains_raw, str):
            domains = [d.strip() for d in domains_raw.split(",") if d.strip()]
        elif isinstance(domains_raw, list):
            domains = [str(d) for d in domains_raw if d]
        else:
            domains = []
        repo_type_raw = attr.get("repositoryType")
        if isinstance(repo_type_raw, list):
            repo_type = [str(r) for r in repo_type_raw]
        elif isinstance(repo_type_raw, str) and repo_type_raw:
            repo_type = [repo_type_raw]
        else:
            repo_type = []
        year_raw = attr.get("year")
        try:
            year = int(year_raw) if year_raw is not None else None
        except (TypeError, ValueError):
            year = None
        is_active_raw = attr.get("isActive", True)
        if isinstance(is_active_raw, str):
            is_active = is_active_raw.lower() == "true"
        else:
            is_active = bool(is_active_raw)
        return DataCiteClient(
            url=uri,
            login=raw.get("id", "") or "",
            platform="datacite",
            name=attr.get("name", "") or "",
            client_id=raw.get("id", "") or "",
            repository_name=attr.get("name", "") or "",
            alternate_name=attr.get("alternateName", "") or "",
            client_type=attr.get("clientType", "") or "",
            repository_type=repo_type,
            description=attr.get("description", "") or "",
            repository_url=attr.get("url", "") or "",
            domains=domains,
            re3data_doi=attr.get("re3data", "") or "",
            year_registered=year,
            is_active=is_active,
            doi_prefixes=prefixes,
        )
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite_adapter.py -v --no-cov
```
Expected: all 21 tests pass (14 from Task 5 + 7 new).

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/datacite_adapter/adapter.py tests/platforms/test_datacite_adapter.py
git commit -m "feat(datacite): adapter fetch with URL-shape dispatch + 4 builders"
```

---

## Task 7: Adapter expand (6 edge kinds)

**Files:**
- Modify: `src/open_pulse_crawler/platforms/datacite_adapter/adapter.py`
- Modify: `tests/platforms/test_datacite_adapter.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_datacite_adapter.py`:

```python
from open_pulse_crawler.platforms.base import ExpandOpts, Edge


# --- expand: DataCiteWork ---------------------------------------------

def test_expand_work_emits_authored_by_for_each_orcid_creator(adapter):
    work = DataCiteWork(
        url="https://doi.org/10.6084/m9.figshare.99",
        full_name="10.6084/m9.figshare.99", platform="datacite",
        doi="10.6084/m9.figshare.99",
        creators=[
            {"name": "Doe, J.",   "orcid": "0000-0001-2345-6789", "affiliations": []},
            {"name": "Anon, A.",  "orcid": "",                    "affiliations": []},  # skipped
            {"name": "Smith, K.", "orcid": "0000-0002-3456-7890", "affiliations": []},
        ],
    )
    edges = [e for e in adapter.expand(work, ExpandOpts()) if e.kind == "authored_by"]
    assert sorted(e.dst for e in edges) == [
        "https://orcid.org/0000-0001-2345-6789",
        "https://orcid.org/0000-0002-3456-7890",
    ]


def test_expand_work_emits_affiliated_with_per_ror_affiliation(adapter):
    work = DataCiteWork(
        url="https://doi.org/10.6084/m9.figshare.99",
        full_name="10.6084/m9.figshare.99", platform="datacite",
        doi="10.6084/m9.figshare.99",
        affiliations=[
            {"name": "EPFL",  "ror": "02s376052", "scheme": "ROR"},
            {"name": "Other", "ror": "",         "scheme": "ISNI"},  # skipped (non-ROR)
            {"name": "ETHZ",  "ror": "05a28rw58", "scheme": "ROR"},
        ],
    )
    edges = [e for e in adapter.expand(work, ExpandOpts()) if e.kind == "affiliated_with"]
    assert sorted(e.dst for e in edges) == [
        "https://ror.org/02s376052",
        "https://ror.org/05a28rw58",
    ]


def test_expand_work_emits_published_by_when_client_id_set(adapter):
    work = DataCiteWork(
        url="https://doi.org/10.6084/m9.figshare.99",
        full_name="10.6084/m9.figshare.99", platform="datacite",
        doi="10.6084/m9.figshare.99",
        client_id="figshare.ars",
    )
    edges = [e for e in adapter.expand(work, ExpandOpts()) if e.kind == "published_by"]
    assert len(edges) == 1
    assert edges[0].dst == "https://commons.datacite.org/repositories/figshare.ars"


def test_expand_work_emits_related_to_via_synthesize_target_url(adapter):
    work = DataCiteWork(
        url="https://doi.org/10.6084/m9.figshare.99",
        full_name="10.6084/m9.figshare.99", platform="datacite",
        doi="10.6084/m9.figshare.99",
        relations=[
            {"relation_type": "IsSupplementTo", "target_type": "URL",
             "target": "https://github.com/foo/bar"},
            {"relation_type": "IsVersionOf", "target_type": "DOI",
             "target": "10.5281/zenodo.42"},  # routes via DOI prefix table
            {"relation_type": "References", "target_type": "arXiv",
             "target": "arXiv:2401.12345"},
        ],
    )
    edges = [e for e in adapter.expand(work, ExpandOpts()) if e.kind.startswith("related_to.")]
    kinds_dsts = sorted((e.kind, e.dst) for e in edges)
    assert kinds_dsts == [
        ("related_to.IsSupplementTo", "https://github.com/foo/bar"),
        ("related_to.IsVersionOf",    "https://zenodo.org/records/42"),
        ("related_to.References",     "https://arxiv.org/abs/2401.12345"),
    ]


def test_expand_work_drops_relations_whose_target_cant_be_synthesized(adapter):
    work = DataCiteWork(
        url="https://doi.org/10.6084/m9.figshare.99",
        full_name="10.6084/m9.figshare.99", platform="datacite",
        doi="10.6084/m9.figshare.99",
        relations=[
            {"relation_type": "References", "target_type": "ISBN",
             "target": "978-0-13-110362-7"},  # ISBN — not a URL scheme; dropped
        ],
    )
    edges = [e for e in adapter.expand(work, ExpandOpts()) if e.kind.startswith("related_to.")]
    assert edges == []


# --- expand: DataCiteOrganization -------------------------------------

def test_expand_organization_walks_has_publication_via_ror_query(adapter):
    adapter._client.iter_dois_by_ror.return_value = iter([
        {"id": "10.1/x", "attributes": {"doi": "10.1/x"}},
        {"id": "10.2/y", "attributes": {"doi": "10.2/y"}},
    ])
    org = DataCiteOrganization(
        url="https://ror.org/02s376052",
        login="02s376052", platform="datacite",
        ror_id="02s376052",
        ror_url="https://ror.org/02s376052",
    )
    edges = [e for e in adapter.expand(org, ExpandOpts()) if e.kind == "has_publication"]
    adapter._client.iter_dois_by_ror.assert_called_once_with("https://ror.org/02s376052")
    assert sorted(e.dst for e in edges) == [
        "https://doi.org/10.1/x",
        "https://doi.org/10.2/y",
    ]


def test_expand_organization_routes_zenodo_doi_to_zenodo_url(adapter):
    """If a ROR-affiliated DOI is a Zenodo prefix, the edge target uses
    the rewritten Zenodo URL (so the BFS routes to the Zenodo adapter)."""
    adapter._client.iter_dois_by_ror.return_value = iter([
        {"id": "10.5281/zenodo.42", "attributes": {"doi": "10.5281/zenodo.42"}},
    ])
    org = DataCiteOrganization(
        url="https://ror.org/02s376052",
        login="02s376052", platform="datacite",
        ror_id="02s376052",
        ror_url="https://ror.org/02s376052",
    )
    edges = list(adapter.expand(org, ExpandOpts()))
    assert any(e.dst == "https://zenodo.org/records/42" for e in edges)


# --- expand: DataCitePerson -------------------------------------------

def test_expand_person_walks_authored_via_orcid_query(adapter):
    adapter._client.iter_dois_by_orcid.return_value = iter([
        {"id": "10.1/p1", "attributes": {"doi": "10.1/p1"}},
    ])
    person = DataCitePerson(
        url="https://orcid.org/0000-0002-1825-0097",
        login="0000-0002-1825-0097", platform="datacite",
        orcid="0000-0002-1825-0097",
        orcid_url="https://orcid.org/0000-0002-1825-0097",
    )
    edges = list(adapter.expand(person, ExpandOpts()))
    adapter._client.iter_dois_by_orcid.assert_called_once_with("https://orcid.org/0000-0002-1825-0097")
    assert edges == [Edge(
        src=person.url, kind="authored",
        dst="https://doi.org/10.1/p1",
    )]


# --- expand: DataCiteClient (passive) ---------------------------------

def test_expand_client_emits_nothing(adapter):
    client = DataCiteClient(
        url="https://commons.datacite.org/repositories/cern.zenodo",
        login="cern.zenodo", platform="datacite",
        client_id="cern.zenodo",
    )
    edges = list(adapter.expand(client, ExpandOpts()))
    assert edges == []
    adapter._client.get_doi.assert_not_called()
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite_adapter.py -v --no-cov
```
Expected: 9 new tests fail with `NotImplementedError`.

- [ ] **Step 3: Implement `expand` + helpers**

Replace the `expand` placeholder body in `src/open_pulse_crawler/platforms/datacite_adapter/adapter.py`:

```python
    # ---- expand ------------------------------------------------------------

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        if isinstance(node, DataCiteWork):
            yield from self._expand_work(node, opts)
        elif isinstance(node, DataCiteOrganization):
            yield from self._expand_organization(node, opts)
        elif isinstance(node, DataCitePerson):
            yield from self._expand_person(node, opts)
        # DataCiteClient is passive — no edges.

    def _doi_target_url(self, doi: str) -> str:
        """Map a DOI to its canonical target URL, rewriting Zenodo prefixes."""
        return rewrite_doi_url(doi) or f"https://doi.org/{doi}"

    def _expand_work(self, node: DataCiteWork, opts: ExpandOpts) -> Iterable[Edge]:
        # authored_by — each creator with an ORCID
        for c in node.creators or []:
            orcid = c.get("orcid", "") if isinstance(c, dict) else ""
            if not orcid:
                continue
            yield Edge(
                src=node.url,
                kind="authored_by",
                dst=f"https://orcid.org/{orcid}",
            )

        # affiliated_with — each ROR affiliation
        for a in node.affiliations or []:
            if not isinstance(a, dict):
                continue
            if a.get("scheme") != "ROR":
                continue
            ror = a.get("ror", "")
            if not ror:
                continue
            yield Edge(
                src=node.url,
                kind="affiliated_with",
                dst=f"https://ror.org/{ror}",
            )

        # published_by — DataCiteClient anchor
        if node.client_id:
            yield Edge(
                src=node.url,
                kind="published_by",
                dst=f"https://commons.datacite.org/repositories/{node.client_id}",
            )

        # related_to.<RelationType> — DataCite relatedIdentifiers via shared helper
        for r in node.relations or []:
            if not isinstance(r, dict):
                continue
            relation_type = r.get("relation_type", "") or "references"
            scheme = (r.get("target_type", "") or "").lower()
            ident = r.get("target", "") or ""
            if not ident:
                continue
            target = synthesize_target_url(scheme, ident)
            if not target:
                continue
            yield Edge(
                src=node.url,
                kind=f"related_to.{relation_type}",
                dst=target,
            )

    def _expand_organization(
        self, node: DataCiteOrganization, opts: ExpandOpts,
    ) -> Iterable[Edge]:
        for item in self._client.iter_dois_by_ror(node.ror_url):
            attr = item.get("attributes", {}) if isinstance(item, dict) else {}
            doi = attr.get("doi") or item.get("id", "")
            if not doi:
                continue
            yield Edge(
                src=node.url,
                kind="has_publication",
                dst=self._doi_target_url(doi),
            )

    def _expand_person(
        self, node: DataCitePerson, opts: ExpandOpts,
    ) -> Iterable[Edge]:
        for item in self._client.iter_dois_by_orcid(node.orcid_url):
            attr = item.get("attributes", {}) if isinstance(item, dict) else {}
            doi = attr.get("doi") or item.get("id", "")
            if not doi:
                continue
            yield Edge(
                src=node.url,
                kind="authored",
                dst=self._doi_target_url(doi),
            )
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite_adapter.py -v --no-cov
```
Expected: all 30 tests pass (21 from Tasks 5+6 + 9 new).

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/datacite_adapter/adapter.py tests/platforms/test_datacite_adapter.py
git commit -m "feat(datacite): adapter expand — 6 edge kinds with DOI prefix routing"
```

---

# Block D — Registry + CLI + API

## Task 8: `PlatformRegistry.register_hosts`

**Files:**
- Modify: `src/open_pulse_crawler/platforms/__init__.py`
- Modify: `tests/platforms/test_base.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_base.py`:

```python
def test_register_hosts_registers_under_multiple_hosts():
    """The same adapter instance can own multiple URL hosts."""
    from open_pulse_crawler.platforms import PlatformRegistry
    from tests.platforms._fake_adapter import _FakeAdapter

    reg = PlatformRegistry()
    adapter = _FakeAdapter(instance_host="api.example.org")
    reg.register_hosts(["a.example.com", "b.example.com"], adapter)
    assert reg.adapter_for("https://a.example.com/x") is adapter
    assert reg.adapter_for("https://b.example.com/y") is adapter
    # The adapter's own instance_host is NOT auto-registered by register_hosts;
    # callers list it explicitly if needed.


def test_register_hosts_raises_on_conflict_and_rolls_back():
    """If any host in the list is already registered, raise and undo
    any partial registrations from this call."""
    from open_pulse_crawler.platforms import PlatformRegistry
    from tests.platforms._fake_adapter import _FakeAdapter

    reg = PlatformRegistry()
    a = _FakeAdapter(instance_host="a.example.com")
    reg.register(a)
    b = _FakeAdapter(instance_host="other.example.com")
    with pytest.raises(ValueError, match="already registered"):
        reg.register_hosts(["c.example.com", "a.example.com"], b)
    # Partial rollback: c.example.com should NOT be registered after the failure.
    assert "c.example.com" not in reg.hosts()
    # And the pre-existing a.example.com still points at its original adapter.
    assert reg.adapter_for("https://a.example.com/x") is a
```

(If `pytest` and `_FakeAdapter` are not already imported in `tests/platforms/test_base.py`, add them at the top.)

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_base.py -v --no-cov
```
Expected: `AttributeError: 'PlatformRegistry' object has no attribute 'register_hosts'`.

- [ ] **Step 3: Implement `register_hosts`**

Append the method inside `class PlatformRegistry` in `src/open_pulse_crawler/platforms/__init__.py` (after `register`):

```python
    def register_hosts(self, hosts: list[str], adapter: PlatformAdapter) -> None:
        """Register the same adapter instance under multiple hosts.

        Used when one adapter owns several URL hosts (e.g., DataCite owns
        doi.org / ror.org / orcid.org / api.datacite.org / commons.datacite.org).
        Raises ``ValueError`` on any conflict; partial registrations from
        this call are rolled back so the registry's state is unchanged on
        failure.
        """
        lowered = [h.lower() for h in hosts]
        conflicts = [h for h in lowered if h in self._adapters]
        if conflicts:
            raise ValueError(
                f"adapter already registered for host(s): {conflicts!r}"
            )
        # All-or-nothing: assign in one pass; there's nothing to roll back
        # because we checked upfront.
        for h in lowered:
            self._adapters[h] = adapter
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_base.py -v --no-cov
```
Expected: 2 new tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/__init__.py tests/platforms/test_base.py
git commit -m "feat(platforms): PlatformRegistry.register_hosts for multi-host adapters"
```

---

## Task 9: CLI registration for `datacite.org`

**Files:**
- Modify: `src/open_pulse_crawler/cli.py`
- Modify: `tests/test_cli.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cli.py`:

```python
def test_build_registry_datacite_anonymous_registers_against_five_hosts(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter
    monkeypatch.delenv("CRAWLER_TOKEN__API_DATACITE_ORG", raising=False)
    registry, _gh, missing = _build_registry(["datacite.org"])
    assert "datacite.org" in missing
    # All five hosts resolve to the same DataCiteAdapter instance
    a = registry.adapter_for("https://doi.org/10.6084/m9.figshare.99")
    b = registry.adapter_for("https://ror.org/02s376052")
    c = registry.adapter_for("https://orcid.org/0000-0002-1825-0097")
    d = registry.adapter_for("https://api.datacite.org/dois/10.x/y")
    e = registry.adapter_for("https://commons.datacite.org/repositories/cern.zenodo")
    assert a is b is c is d is e
    assert isinstance(a, DataCiteAdapter)


def test_build_registry_datacite_with_token_marks_present(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    monkeypatch.setenv("CRAWLER_TOKEN__API_DATACITE_ORG", "dc-pat-test")
    registry, _gh, missing = _build_registry(["datacite.org"])
    assert "datacite.org" not in missing
    a = registry.adapter_for("https://doi.org/10.6084/m9.figshare.99")
    from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter
    assert isinstance(a, DataCiteAdapter)
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_cli.py::test_build_registry_datacite_anonymous_registers_against_five_hosts tests/test_cli.py::test_build_registry_datacite_with_token_marks_present -v --no-cov
```
Expected: `KeyError: 'no adapter registered for host 'doi.org''` or similar.

- [ ] **Step 3: Add the `datacite.org` branches in `_build_registry`**

Read the current `_build_registry` function in `src/open_pulse_crawler/cli.py`. Find the existing Infoscience branches (both anonymous and authenticated paths) — Datacite mirrors that structure.

Anonymous path — insert after the Infoscience anonymous branch:

```python
            if host == "datacite.org":
                from .platforms.datacite_adapter.client import DataCiteHTTPClient
                from .platforms.datacite_adapter.adapter import DataCiteAdapter
                dcc = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
                adapter = DataCiteAdapter(client=dcc, instance_host="api.datacite.org")
                reg.register_hosts(
                    ["doi.org", "ror.org", "orcid.org",
                     "api.datacite.org", "commons.datacite.org"],
                    adapter,
                )
                continue
```

Authenticated path — parallel branch after the Infoscience authenticated branch:

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

Match the existing indentation + `if`/`elif` conventions; the snippets above are templates.

The `missing` reporting comes from the generic token-resolution machinery — it should detect `datacite.org` has no token (because `CRAWLER_TOKEN__DATACITE_ORG` and `CRAWLER_TOKEN_POOL__DATACITE_ORG` resolve through `resolve_tokens("datacite.org")`). The user-facing platform key is `datacite.org`; the token env var matches that host. If the test fails because the missing list uses the wrong host string, log + report the actual behavior in your final report.

**Naming note:** for DataCite specifically, the actual API host is `api.datacite.org`, but the user-facing CLI platform key is `datacite.org`. The token env var the test sets is `CRAWLER_TOKEN__API_DATACITE_ORG` because that's what the underlying client uses (the HTTPClient's `host` field is `api.datacite.org`). The `_build_registry` should call `resolve_tokens("api.datacite.org")` rather than `resolve_tokens("datacite.org")` to pull the bearer token. Adjust the existing branch shape accordingly. If the existing `_build_registry` uses a host-to-token mapping convention that differs, follow that convention.

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_cli.py -v --no-cov
```
Expected: all existing CLI tests + 2 new pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/cli.py tests/test_cli.py
git commit -m "feat(cli): register DataCiteAdapter for datacite.org against 5 hosts"
```

---

## Task 10: OpenAPI examples for DataCite

**Files:**
- Modify: `src/open_pulse_crawler/api/v2.py`
- Modify: `tests/test_api_v2.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_api_v2.py`:

```python
def test_v2_crawl_openapi_examples_include_datacite():
    from fastapi.testclient import TestClient
    from open_pulse_crawler.api import app
    client = TestClient(app)
    spec = client.get("/api/v1/openapi.json").json()
    crawl_path = spec["paths"]["/api/v2/crawl"]
    body = crawl_path["post"]["requestBody"]["content"]["application/json"]
    examples = body.get("examples", {})
    assert "datacite_work_by_doi" in examples
    assert "datacite_org_by_ror_epfl" in examples
    assert "datacite_person_by_orcid" in examples
```

- [ ] **Step 2: Run, verify failure**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_api_v2.py::test_v2_crawl_openapi_examples_include_datacite -v --no-cov
```
Expected: assertion failure.

- [ ] **Step 3: Add three examples**

Find `_CRAWL_V2_REQUEST_EXAMPLES` in `src/open_pulse_crawler/api/v2.py`. Append three new entries at the end of the dict:

```python
    "datacite_work_by_doi": {
        "summary": "DataCite work via doi.org URL (non-Zenodo prefix)",
        "description": (
            "Fetches a Figshare or Dryad DOI. Round 1 walks `authored_by` "
            "(per creator ORCID), `affiliated_with` (per creator ROR), "
            "`published_by` (DataCite client), and `related_to.<RelationType>` "
            "edges from the work's `relatedIdentifiers`."
        ),
        "value": {
            "seeds": ["https://doi.org/10.6084/m9.figshare.99"],
            "max_rounds": 2,
        },
    },
    "datacite_org_by_ror_epfl": {
        "summary": "All DataCite works affiliated with EPFL (via ROR)",
        "description": (
            "Seed EPFL's ROR; round 1 emits `has_publication` edges to every "
            "DOI in DataCite tagged with `https://ror.org/02s376052` as a "
            "creator affiliation. Note: only the subset of EPFL output deposited "
            "in external DataCite repositories — Infoscience's own publications "
            "are not necessarily ROR-tagged."
        ),
        "value": {
            "seeds": ["https://ror.org/02s376052"],
            "max_rounds": 2,
        },
    },
    "datacite_person_by_orcid": {
        "summary": "All DataCite works for a researcher (by ORCID)",
        "description": (
            "Seed an ORCID; round 1 emits `authored` edges to every DOI in "
            "DataCite whose `creators[].nameIdentifiers[]` includes this ORCID."
        ),
        "value": {
            "seeds": ["https://orcid.org/0000-0002-1825-0097"],
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
git commit -m "feat(api): /api/v2/crawl OpenAPI examples for DataCite seeds"
```

---

# Block E — Integration test + docs + verification

## Task 11: Integration test against live `api.datacite.org`

**Files:**
- Create: `tests/integration/test_datacite_dryrun.py`

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_datacite_dryrun.py
"""Tiny live dryrun against api.datacite.org.

Anonymous-friendly — runs whenever ``CRAWLER_SKIP_INTEGRATION`` is unset.
Skipped when explicitly opted out (e.g., offline CI).

Hits the live DataCite API with a 2-second delay between requests to
stay well under the public rate limit.
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("CRAWLER_SKIP_INTEGRATION") == "1",
    reason="CRAWLER_SKIP_INTEGRATION=1 set",
)
def test_fetch_real_datacite_work_via_epfl_ror() -> None:
    """Walk EPFL's ROR to one of its affiliated DataCite works.

    Seeds the EPFL ROR (a bare anchor, no API call), walks one round of
    `has_publication` edges, fetches the first DOI. Asserts non-empty
    DataCiteWork with a known affiliation.
    """
    from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter
    from open_pulse_crawler.platforms.datacite_adapter.client import DataCiteHTTPClient
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import DataCiteOrganization, DataCiteWork

    client = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    adapter = DataCiteAdapter(client=client, instance_host="api.datacite.org")

    epfl_ror = "https://ror.org/02s376052"

    # 1. Bare-anchor fetch returns an Organization with no API call.
    org = adapter.fetch(epfl_ror)
    assert isinstance(org, DataCiteOrganization)
    assert org.ror_id == "02s376052"

    # 2. Walk one has_publication edge to verify the search query works live.
    time.sleep(2.0)  # be polite
    first_edge = next(iter(adapter.expand(org, ExpandOpts())), None)
    if first_edge is None:
        pytest.skip("EPFL ROR returned 0 DataCite works — unexpected; investigate live data.")
    assert first_edge.kind == "has_publication"
    assert first_edge.dst.startswith("https://doi.org/") or first_edge.dst.startswith("https://zenodo.org/")

    # 3. Fetch the underlying DOI (only if it stayed at doi.org; Zenodo URLs
    #    would be the Zenodo adapter's job).
    if first_edge.dst.startswith("https://doi.org/"):
        time.sleep(2.0)
        work = adapter.fetch(first_edge.dst)
        if work is None:
            pytest.skip(f"Live fetch of {first_edge.dst} returned 404 — DOI may have been retracted.")
        assert isinstance(work, DataCiteWork)
        assert work.doi
        assert work.resource_type  # non-empty resourceTypeGeneral
```

- [ ] **Step 2: Run the integration test (live)**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/integration/test_datacite_dryrun.py -v --no-cov
```
Expected: PASS. If the live API throttles, retry once. If still throttled, note in the final report — the test itself is correct.

- [ ] **Step 3: Confirm opt-out**

```
VIRTUAL_ENV= CRAWLER_SKIP_INTEGRATION=1 uv run pytest -n 0 tests/integration/test_datacite_dryrun.py -v --no-cov
```
Expected: SKIPPED.

- [ ] **Step 4: Commit**

```
git add tests/integration/test_datacite_dryrun.py
git commit -m "test(integration): tiny api.datacite.org dryrun via EPFL ROR seed (anonymous, polite)"
```

---

## Task 12: Documentation + CHANGELOG

**Files:**
- Create: `docs/DATACITE.md`
- Modify: `README.md`
- Modify: `docs/index.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write `docs/DATACITE.md`**

Create the file with this exact content:

```markdown
# DataCite Commons adapter

Open Pulse Crawler v3.3+ supports **DataCite Commons** —
DOI-identified works from any DataCite-registered repository (Figshare,
Dryad, ETH WSL EnviDat, etc.), ROR-identified organizations, and
ORCID-identified researchers. Anonymous reads work without a token.

## Supported entities

- **`DataCiteWork`** — a DOI registered with DataCite. The `resource_type`
  field carries `resourceTypeGeneral` (Dataset, JournalArticle, Software,
  Text, …); the `resource_type_detail` field carries free-text
  `resourceType`. Cross-repository — does **not** cover Crossref-issued
  DOIs (Nature, ACM, IEEE).
- **`DataCiteOrganization`** — bare anchor for a ROR identifier
  (`https://ror.org/<id>`). Name populated opportunistically from
  `DataCiteWork.creators[].affiliation[].name` entries; NOT enriched via
  `api.ror.org`.
- **`DataCitePerson`** — bare anchor for an ORCID identifier
  (`https://orcid.org/<id>`). Name populated opportunistically; NOT
  enriched via `pub.orcid.org`.
- **`DataCiteClient`** — a DataCite-registered repository (e.g.
  `cern.zenodo`, `figshare.ars`, `dryad.dryad`). Passive node — `expand`
  emits no edges. Enriched from `/clients/<id>` with `clientType`,
  `domains`, and the `re3data` registry cross-reference.

## Edge kinds

| Source | Kind | Target |
|---|---|---|
| `DataCiteWork` | `authored_by` | `DataCitePerson` (orcid.org URL) |
| `DataCiteWork` | `affiliated_with` | `DataCiteOrganization` (ror.org URL) |
| `DataCiteWork` | `related_to.<RelationType>` | URL on any platform |
| `DataCiteWork` | `published_by` | `DataCiteClient` (commons.datacite.org/repositories/<id>) |
| `DataCiteOrganization` | `has_publication` | `DataCiteWork` (doi.org URL) |
| `DataCitePerson` | `authored` | `DataCiteWork` (doi.org URL) |
| `DataCiteClient` | *(passive)* | — |

`related_to.<RelationType>` reuses the shared DataCite RelationType
vocabulary (also consumed by Zenodo and Infoscience): the URL
synthesizer in `platforms/datacite.py` handles `arxiv`, `orcid`, `pmid`,
`pmcid`, `swh`, `doi`, and `url` schemes uniformly.

## DOI prefix routing

`DataCiteWork` is intentionally **never** produced for DOIs whose prefix
is owned by a sibling adapter. The `_DOI_PREFIX_REWRITERS` table in
`platforms/datacite.py` handles the rewrite:

- `10.5281/zenodo.X` → `https://zenodo.org/records/X` (Zenodo adapter)
- `10.5072/zenodo.X` → `https://sandbox.zenodo.org/records/X` (Zenodo sandbox)

Other DOI prefixes route through DataCite. New entries can be added as
sibling adapters land.

## Configuring tokens

Anonymous reads work for `/dois`, `/clients`, `/dois?query=…`. Tokens
raise rate limits:

```bash
CRAWLER_PLATFORMS=datacite.org
CRAWLER_TOKEN__API_DATACITE_ORG=<bearer-token>
# Or rotation pool:
CRAWLER_TOKEN_POOL__API_DATACITE_ORG=<tok-a>,<tok-b>
```

DataCite tokens are provisioned at <https://commons.datacite.org/sign-in>
for organization members.

## Seed forms accepted

- Canonical: `https://doi.org/<DOI>` (non-Zenodo prefix), `https://ror.org/<id>`,
  `https://orcid.org/<id>`, `https://commons.datacite.org/repositories/<client_id>`.
- Aliases (rewritten to canonical form):
  - `https://api.datacite.org/dois/<DOI>` → `https://doi.org/<DOI>`
  - `https://api.datacite.org/clients/<id>` → `https://commons.datacite.org/repositories/<id>`
  - `https://commons.datacite.org/doi.org/<DOI>` → `https://doi.org/<DOI>`
  - `https://commons.datacite.org/ror.org/<id>` → `https://ror.org/<id>`
  - `https://commons.datacite.org/orcid.org/<id>` → `https://orcid.org/<id>`

**Not supported as seeds:** Crossref-issued DOIs (Nature, ACM, IEEE,
Elsevier, …). They 404 against `/dois/<doi>`. A future Crossref adapter
could cover them.

## Manual-test recipes

### Single DataCite work (Figshare)

```bash
opc crawl --platforms datacite.org \
    --default-host api.datacite.org --rounds 2 \
    https://doi.org/10.6084/m9.figshare.99
```

### All EPFL works in DataCite (via ROR)

```bash
opc crawl --platforms datacite.org \
    --default-host api.datacite.org --rounds 2 \
    https://ror.org/02s376052
```

Round 0 fetches the bare Organization anchor (no API call); round 1
walks `has_publication` edges to every DOI in DataCite tagged with
EPFL's ROR. Yields ~3,500 works at time of writing.

### All works by a single ORCID

```bash
opc crawl --platforms datacite.org \
    --default-host api.datacite.org --rounds 2 \
    https://orcid.org/0000-0002-1825-0097
```

### Cross-platform crawl (DataCite → Zenodo)

```bash
CRAWLER_PLATFORMS=datacite.org,zenodo.org \
    opc crawl --rounds 3 \
    https://ror.org/02s376052
```

DataCite emits `has_publication` edges to every affiliated DOI; the
DOI-prefix routing rewrites Zenodo-prefix DOIs to `zenodo.org/records/<id>`
so the Zenodo adapter picks them up in round 2.

## Limitations (v3.3)

- **Crossref-issued DOIs (Nature, ACM, IEEE, Elsevier, …) are not in
  DataCite's index.** They 404 against `/dois/<doi>`. A future Crossref
  adapter could cover them.
- **ROR and ORCID nodes are bare anchors.** Name / country / type fields
  are not enriched from `api.ror.org` or `pub.orcid.org` — identity
  resolution stays downstream of this tool.
- **`DataCiteClient` is passive.** A `cern.zenodo` seed doesn't fan out
  to its corpus (millions of DOIs). Work nodes link to it via
  `published_by`; expand on a client emits nothing.
- **Coverage of institutional output via ROR is partial.** EPFL ROR
  returns ~3,500 works — vastly less than Infoscience holds, because
  most EPFL submissions to Infoscience aren't ROR-tagged in their
  creator affiliations.
- **No Bearer token validation.** Anonymous reads degrade gracefully; an
  invalid token silently 401s on protected endpoints (none are used in
  v3.3 — all reads are public).
- **No deposit/submit/draft flows.** Read-only.
```

- [ ] **Step 2: Update `README.md`**

Find the existing "Multi-platform support" section (Specs 1+2+3 — should list GitLab + Zenodo + Infoscience). Add a DataCite bullet right after the Infoscience bullet:

```markdown
- **DataCite Commons** (`doi.org` / `ror.org` / `orcid.org` /
  `api.datacite.org` / `commons.datacite.org`): the DataCite-indexed
  cross-repository graph. DOI-identified works (Figshare, Dryad, ETH
  WSL, …), ROR-identified organizations, ORCID-identified researchers,
  and DataCite-registered repositories. Anonymous reads supported.
  Routes Zenodo DOIs back to the Zenodo adapter via DOI prefix table.
  See [docs/DATACITE.md](docs/DATACITE.md).
```

- [ ] **Step 3: Update `docs/index.md`**

Add a pointer below the existing Infoscience link:

```markdown
- [DataCite Commons support](DATACITE.md) — DOI / ROR / ORCID graph
  with the shared DataCite RelationType vocabulary.
```

- [ ] **Step 4: Update `CHANGELOG.md`**

Under `[Unreleased]` (will become `[3.3.0]` on release), add:

```markdown
### Added (v3.3 — DataCite Commons adapter)
- `DataCiteAdapter` and `DataCiteHTTPClient` under
  `src/open_pulse_crawler/platforms/datacite_adapter/`. Crawls DataCite
  Commons — DOI-identified works (any DataCite repository), ROR
  organizations, ORCID researchers, and DataCite-registered repositories.
- Four subkind models: `DataCiteWork`, `DataCiteOrganization`,
  `DataCitePerson`, `DataCiteClient`.
- Six new edge kinds: `authored_by`, `affiliated_with`,
  `related_to.<RelationType>`, `published_by` (from work), `has_publication`
  (from organization), `authored` (from person). `DataCiteClient` is
  passive — no edges emitted.
- DOI prefix routing table (`_DOI_PREFIX_REWRITERS` in
  `platforms/datacite.py`): Zenodo-prefix DOIs are rewritten to their
  canonical `zenodo.org` URLs at seed-time, so the BFS routes them to
  the Zenodo adapter (no duplicate node).
- `PlatformRegistry.register_hosts(hosts, adapter)` for multi-host
  registration. The DataCite adapter is registered against five hosts
  (`doi.org`, `ror.org`, `orcid.org`, `api.datacite.org`,
  `commons.datacite.org`); user-facing platform key is `datacite.org`.
- HTTP 429 retry-after handling in `DataCiteHTTPClient` (one automatic
  retry honoring `Retry-After` header, capped at 60s; second 429 raises).
- DataCite-CamelCase relation-type pass-through (no normalization needed —
  DataCite emits canonical PascalCase like `IsVersionOf` natively).
- CRIS-flavored client metadata enrichment: `clientType`, `domains` (for
  cross-host routing hints), `re3data_doi` (cross-registry anchor),
  `doi_prefixes` (via `/clients/<id>/relationships/prefixes`).
- CLI registers `DataCiteAdapter` against `datacite.org` (anonymous + tokens).
- `POST /api/v2/crawl` OpenAPI examples: `datacite_work_by_doi`,
  `datacite_org_by_ror_epfl`, `datacite_person_by_orcid`.
- Integration test against live `api.datacite.org`
  (`tests/integration/test_datacite_dryrun.py`).
- `docs/DATACITE.md`.

### Refactored (v3.3)
- `node_id.rewrite_zenodo_doi_url` and `node_id.is_zenodo_doi_url`
  removed. Replaced by the generalized `rewrite_doi_url` /
  `is_owned_doi_url` in `platforms/datacite.py`, table-driven via
  `_DOI_PREFIX_REWRITERS`. Behavior is unchanged for Zenodo.

### Out of scope (v3.3)
- Crossref-issued DOIs (Nature, ACM, IEEE). A future `CrossrefAdapter`
  could share the DOI host via the prefix routing table.
- ROR / ORCID secondary API enrichment. Cross-platform identity
  resolution stays downstream.
- Active `DataCiteClient` expansion (walking all DOIs published by a
  client). Could be added behind a `--crawl-client-works` flag.
- `tools/scripts/fetch_public_projects.py` extension for DataCite —
  no natural "browse all DOIs" use case yet.
```

- [ ] **Step 5: Commit**

```
git add docs/DATACITE.md README.md docs/index.md CHANGELOG.md
git commit -m "docs: DataCite Commons adapter (v3.3.0) — DATACITE.md, README, CHANGELOG"
```

---

## Task 13: Final lint + test verification

- [ ] **Step 1: Lint**

```
VIRTUAL_ENV= uv run ruff check src/
```
Expected: no NEW errors beyond the develop baseline (~22-25 pre-existing).

- [ ] **Step 2: Full unit suite (serial)**

```
VIRTUAL_ENV= uv run pytest -n 0 -m "not integration" --ignore=tests/test_api.py --ignore=tests/test_auth.py -q --no-cov
```
Expected: all tests pass (~410+; ~30 new from DataCite tasks above plus all pre-existing).

- [ ] **Step 3: Diff stat**

```
git diff --stat origin/develop...HEAD | tail -20
```
Eyeball: each new module is ~150-300 LoC; routing-table extension to `platforms/datacite.py` is ~50 LoC; tests dominate (~700 LoC across new + extended files).

- [ ] **Step 4: Stop and report ready for review**

Do NOT push. Report:
- Total new/modified files.
- Total tests added.
- Pass count.
- Any deferred concerns (e.g., did the integration test pass against live DataCite? did any 429 backoff fire during the live probe?).

---

## Self-Review

**Spec coverage:**

| Spec section | Tasks |
|---|---|
| §0 Starting point | n/a (informational) |
| §1 Architecture & module layout | 1, 3, 5, 8, 9 |
| §2 Data model (4 subkinds) | 2 |
| §3.1 Hosts owned | 5, 9 |
| §3.2 normalize_uri (10 input forms) | 5 |
| §3.3 classify | 5 |
| §3.4 fetch (4 URL shapes) | 6 |
| §3.5 expand (6 edge kinds) | 7 |
| §3.6 rate_limit_state | 5 |
| §4.1 Generalize rewrite_zenodo_doi_url | 1 |
| §4.2 register_hosts | 8 |
| §4.3 _build_registry datacite.org branch | 9 |
| §4.4 doctor command surface | n/a (passive — generic machinery already iterates registry.hosts()) |
| §5.1 Configuration | 9, 12 |
| §5.2 Caching | inherited from Task 3 (`_cache_dir` parameter) |
| §5.3 Testing | every task + 11 (integration) |
| §6 Effort estimate | n/a (informational) |
| §7 Open questions | resolved or deferred during execution |

Pre-flight migration (Task 1) — generalize `rewrite_zenodo_doi_url` to `rewrite_doi_url` — is covered.

**Placeholder scan:** No "TBD" / "TODO" / "implement later" / vague phrases. Each task has actual code + commands the executor needs.

**Type consistency:** `DataCiteWork` / `DataCiteOrganization` / `DataCitePerson` / `DataCiteClient` spelled identically across Tasks 2, 5, 6, 7. `DataCiteHTTPClient` method names (`get_doi`, `get_client`, `get_client_prefixes`, `iter_dois_by_ror`, `iter_dois_by_orcid`) match across Tasks 4, 6, 7. Edge kinds (`authored_by`, `affiliated_with`, `related_to.<RelationType>`, `published_by`, `has_publication`, `authored`) consistent across spec, tests, adapter, and docs. `rewrite_doi_url` / `is_owned_doi_url` consistent across Tasks 1, 5, 7. `register_hosts` consistent across Tasks 8, 9.

**One known shape question** carried into the executor's notebook: Task 9's note about whether `_build_registry` looks up tokens via `resolve_tokens("datacite.org")` (user-facing platform key) or `resolve_tokens("api.datacite.org")` (the actual API host). The convention in the existing codebase (Zenodo, Infoscience) is that the user-facing platform key matches the API host, so this is the first instance where they diverge. Recommend: use `api.datacite.org` for the token lookup since that's the host the Bearer header targets, and the user-facing key `datacite.org` is just the CLI shorthand. Document the convention in the final report.
