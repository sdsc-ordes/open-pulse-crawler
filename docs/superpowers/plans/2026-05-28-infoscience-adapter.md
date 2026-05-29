# Infoscience Adapter Implementation Plan (Spec 3, targets v3.2.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `InfoscienceAdapter` so EPFL's Infoscience repository (DSpace 7.6.2 + DSpace-CRIS) crawls alongside the GitHub / GitLab / Zenodo adapters, with cross-platform `related_to.<RelationType>` edges reusing Zenodo's DataCite URL synthesizer (now lifted to a shared `platforms/datacite.py` module).

**Architecture:** Approach A — plain `httpx` `InfoscienceClient` + `InfoscienceAdapter` mirroring the Zenodo pattern. Three subkinds (`InfoscienceItem`, `InfosciencePerson`, `InfoscienceOrgUnit`) all reach through the same DSpace `/server/api/core/items/<uuid>` endpoint, dispatched server-side via the `entityType` field. 429 retry-after handling because EPFL rate-limits hard. No new dependencies.

**Tech Stack:** Python 3.10+, Pydantic v2 (discriminated unions), `httpx` (already a dep), pytest, uv. No new third-party libraries.

**Spec:** `docs/superpowers/specs/2026-05-28-infoscience-adapter-design.md` (committed `6062e11`).

**Branch / worktree:** Same branch as Specs 1+2 — `feat/multi-platform-gitlab` at `.worktrees/feat-multi-platform-gitlab/`.

**Test invocation note:** pytest with the default `-n auto` hangs in this sandbox. Run with `-n 0`:
```
VIRTUAL_ENV= uv run pytest -n 0 <args>
```

---

## What Specs 1+2 already give us — skipped

- `PlatformAdapter` ABC + `PlatformRegistry` (host-keyed dispatch).
- `subkind` discriminator + discriminated-union dict types in `models.py`.
- `node_id.canonical_url` URL helper.
- `config.resolve_tokens(host)` host-keyed env-var resolution.
- Dual-path crawler dispatch (`urlparse(uri).netloc.lower() != "github.com"` → adapter path).
- `_FakeAdapter` test helper.
- Per-host cache layout in `APICache`.
- CLI `--platforms` / `--default-host` / `crawler doctor`.
- `/api/v2` REST surface with OpenAPI examples dropdown.
- `ZenodoAdapter._synthesize_target_url` (to be lifted to `platforms/datacite.py` in Task 1 of this plan — net same code surface).

---

## File map

**New files:**
- `src/open_pulse_crawler/platforms/datacite.py` — shared DataCite URL synthesizer (lifted from Zenodo).
- `src/open_pulse_crawler/platforms/infoscience/__init__.py`
- `src/open_pulse_crawler/platforms/infoscience/client.py`
- `src/open_pulse_crawler/platforms/infoscience/adapter.py`
- `tests/platforms/test_datacite.py` — relocated tests for the URL synthesizer.
- `tests/platforms/test_infoscience_client.py`
- `tests/platforms/test_infoscience_adapter.py`
- `tests/integration/test_infoscience_dryrun.py`
- `docs/INFOSCIENCE.md`

**Modified files:**
- `src/open_pulse_crawler/models.py` — three Infoscience subclasses, widen the three discriminated unions.
- `src/open_pulse_crawler/platforms/zenodo/adapter.py` — import `synthesize_target_url` from `platforms.datacite`; remove the local static-method definition.
- `src/open_pulse_crawler/cli.py` — `_build_registry` Infoscience branch (anonymous + authenticated paths).
- `src/open_pulse_crawler/api/v2.py` — three new openapi_examples entries.
- `tools/scripts/fetch_public_projects.py` — host-dispatch Infoscience listing endpoint; add `infoscience.epfl.ch` to `DEFAULT_HOSTS`.
- `tests/platforms/test_zenodo_adapter.py` — remove the `_synthesize_target_url` static-method tests (they move to `test_datacite.py`); keep the `_expand_record_emits_*` tests that exercise the call site.
- `CHANGELOG.md`, `README.md`, `docs/index.md`.

---

## Task ordering

Block A (Tasks 1–2): Foundations — relocate the DataCite helper + three Infoscience subkinds in `models.py`.
Block B (Tasks 3–4): `InfoscienceClient` (skeleton + endpoints + 429 retry).
Block C (Tasks 5–7): `InfoscienceAdapter` classify/fetch/expand.
Block D (Tasks 8–10): CLI registration, `/api/v2` examples, explore-script extension.
Block E (Tasks 11–13): integration test, docs/CHANGELOG, final verification.

Every task is TDD-shaped: failing test → minimal implementation → passing test → commit. Conventional Commits per `AGENTS.md`. NEVER pass `--no-verify`, `-c commit.gpgsign=false`, or `--no-gpg-sign`.

---

# Block A — Foundations (DataCite refactor + models)

## Task 1: Lift `_synthesize_target_url` into `platforms/datacite.py`

Pre-flight refactor inside the spec. Behavior is unchanged for Zenodo — the function moves to a module-level helper and Zenodo imports it.

**Files:**
- Create: `src/open_pulse_crawler/platforms/datacite.py`
- Create: `tests/platforms/test_datacite.py`
- Modify: `src/open_pulse_crawler/platforms/zenodo/adapter.py` — remove `_synthesize_target_url`; import from datacite.
- Modify: `tests/platforms/test_zenodo_adapter.py` — remove the 11 `test_synthesize_*` tests (they move to `test_datacite.py`).

- [ ] **Step 1: Create the test file by relocating the existing Zenodo tests**

Read `tests/platforms/test_zenodo_adapter.py` and find the existing tests named `test_synthesize_passthrough_https_under_any_scheme`, `test_synthesize_arxiv_strips_prefix`, `test_synthesize_orcid_handles_prefix_and_bare`, `test_synthesize_pmid`, `test_synthesize_pmcid_normalizes_prefix`, `test_synthesize_swh_urn`, `test_synthesize_doi_zenodo_rewrites_to_platform_url`, `test_synthesize_unknown_scheme_drops_non_url_identifier`, `test_synthesize_empty_inputs`, `test_expand_record_emits_all_synthesized_schemes` (the last one stays in `test_zenodo_adapter.py` because it tests the adapter, not the helper).

Create `tests/platforms/test_datacite.py` with the relocated tests, rewriting the import + invocation:

```python
# tests/platforms/test_datacite.py
"""Tests for the shared DataCite URL synthesizer.

Lifted from `ZenodoAdapter._synthesize_target_url` static method when both
the Zenodo and Infoscience adapters needed the same scheme→URL mapping.
"""
from open_pulse_crawler.platforms.datacite import synthesize_target_url


def test_synthesize_passthrough_https_under_any_scheme():
    """Identifiers that are already https:// always pass through verbatim,
    regardless of declared scheme."""
    assert synthesize_target_url("doi", "https://doi.org/10.1234/x") == "https://doi.org/10.1234/x"
    assert synthesize_target_url("arxiv", "https://arxiv.org/abs/2401.12345") == "https://arxiv.org/abs/2401.12345"
    assert synthesize_target_url("url", "https://example.com/foo") == "https://example.com/foo"


def test_synthesize_arxiv_strips_prefix():
    assert synthesize_target_url("arxiv", "2401.12345") == "https://arxiv.org/abs/2401.12345"
    assert synthesize_target_url("arxiv", "arXiv:2401.12345") == "https://arxiv.org/abs/2401.12345"
    assert synthesize_target_url("arxiv", "ARXIV:2401.12345") == "https://arxiv.org/abs/2401.12345"


def test_synthesize_orcid_handles_prefix_and_bare():
    assert synthesize_target_url("orcid", "0000-0002-1825-0097") == "https://orcid.org/0000-0002-1825-0097"
    assert synthesize_target_url("orcid", "ORCID:0000-0002-1825-0097") == "https://orcid.org/0000-0002-1825-0097"


def test_synthesize_pmid():
    assert synthesize_target_url("pmid", "12345678") == "https://pubmed.ncbi.nlm.nih.gov/12345678/"


def test_synthesize_pmcid_normalizes_prefix():
    assert synthesize_target_url("pmcid", "PMC1234567") == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"
    assert synthesize_target_url("pmcid", "1234567") == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"
    assert synthesize_target_url("pmcid", "pmc1234567") == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"


def test_synthesize_swh_urn():
    assert synthesize_target_url("swh", "swh:1:dir:abc123") == "https://archive.softwareheritage.org/swh:1:dir:abc123"
    assert synthesize_target_url("swh", "swh:1:cnt:deadbeef") == "https://archive.softwareheritage.org/swh:1:cnt:deadbeef"


def test_synthesize_doi_zenodo_rewrites_to_platform_url():
    assert synthesize_target_url("doi", "10.5281/zenodo.99") == "https://zenodo.org/records/99"
    assert synthesize_target_url("doi", "10.5072/zenodo.42") == "https://sandbox.zenodo.org/records/42"
    assert synthesize_target_url("doi", "10.1234/foo.bar") == "https://doi.org/10.1234/foo.bar"


def test_synthesize_unknown_scheme_drops_non_url_identifier():
    assert synthesize_target_url("isbn", "978-3-16-148410-0") is None
    assert synthesize_target_url("issn", "0001-1234") is None
    assert synthesize_target_url("ark", "ark:/12345/abc") is None


def test_synthesize_empty_inputs():
    assert synthesize_target_url("doi", "") is None
    assert synthesize_target_url("url", "") is None
    assert synthesize_target_url("", "") is None
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite.py -v --no-cov
```
Expected: `ModuleNotFoundError: No module named 'open_pulse_crawler.platforms.datacite'`.

- [ ] **Step 3: Create `platforms/datacite.py` with the lifted function**

Read the existing `ZenodoAdapter._synthesize_target_url` body in `src/open_pulse_crawler/platforms/zenodo/adapter.py` and lift its content. Create:

```python
# src/open_pulse_crawler/platforms/datacite.py
"""Shared DataCite URL synthesizer for adapters that consume Zenodo /
Infoscience / other DataCite-flavored related-identifier vocabularies.

A related identifier is a (scheme, identifier) pair drawn from DataCite's
RelationType + identifier-scheme vocabularies, e.g.
``("doi", "10.5281/zenodo.42")`` or ``("arxiv", "2401.12345")``.

`synthesize_target_url(scheme, ident)` turns such a pair into the canonical
HTTPS URL the BFS engine can route by host, or returns ``None`` when the
identifier can't be made into a URL.
"""
from __future__ import annotations

from typing import Optional

from ..node_id import rewrite_zenodo_doi_url


def synthesize_target_url(scheme: str, ident: str) -> Optional[str]:
    """Turn a (scheme, identifier) pair from a DataCite-flavored
    ``related_identifiers`` entry into a full canonical URL.

    Returns ``None`` when the entry can't be made into a URL — those
    edges are dropped rather than emitted with a useless target.

    Supported schemes:
        url    pass through if already https://; otherwise drop
        doi    Zenodo DOIs → canonical platform URL; other DOIs → doi.org
        arxiv  "2401.12345" (or "arXiv:2401.12345") → arxiv.org/abs/<id>
        orcid  "0000-0002-1825-0097" → orcid.org/<id>
        pmid   "12345678" → pubmed.ncbi.nlm.nih.gov/<id>/
        pmcid  "PMC1234567" or "1234567" → ncbi.nlm.nih.gov/pmc/articles/PMC<id>/
        swh    "swh:1:dir:..." → archive.softwareheritage.org/<urn>

    Identifiers that already start with ``http(s)://`` pass through under
    any declared scheme — sources sometimes stamp the URL directly into
    the identifier regardless of the declared scheme.
    """
    if not ident:
        return None

    # Always honor an already-resolved URL, regardless of declared scheme.
    if ident.startswith(("http://", "https://")):
        return ident

    scheme = (scheme or "").lower()
    if scheme == "url":
        return None  # url scheme but identifier wasn't a URL — drop

    if scheme == "doi":
        rewritten = rewrite_zenodo_doi_url(f"https://doi.org/{ident}")
        return rewritten or f"https://doi.org/{ident}"

    if scheme == "arxiv":
        arxiv_id = ident
        if arxiv_id.lower().startswith("arxiv:"):
            arxiv_id = arxiv_id[len("arxiv:"):]
        arxiv_id = arxiv_id.strip()
        return f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None

    if scheme == "orcid":
        oid = ident
        if oid.lower().startswith("orcid:"):
            oid = oid[len("orcid:"):]
        oid = oid.strip().strip("/")
        return f"https://orcid.org/{oid}" if oid else None

    if scheme == "pmid":
        pid = ident.strip()
        return f"https://pubmed.ncbi.nlm.nih.gov/{pid}/" if pid else None

    if scheme == "pmcid":
        pid = ident.strip()
        if not pid:
            return None
        if not pid.upper().startswith("PMC"):
            pid = "PMC" + pid
        else:
            pid = "PMC" + pid[3:]  # canonical-case prefix
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pid}/"

    if scheme == "swh":
        return f"https://archive.softwareheritage.org/{ident.strip()}"

    return None
```

- [ ] **Step 4: Update `ZenodoAdapter` to import from `datacite`**

Read `src/open_pulse_crawler/platforms/zenodo/adapter.py`. Find the static-method definition:

```python
    @staticmethod
    def _synthesize_target_url(scheme: str, ident: str) -> Optional[str]:
        ...
```

Delete the entire method (including its docstring). Then update the call site in `_expand_record` — it currently looks like:

```python
            target_url = self._synthesize_target_url(scheme, ident)
```

Change to:

```python
            target_url = synthesize_target_url(scheme, ident)
```

Add the import near the top of `adapter.py`:

```python
from ..datacite import synthesize_target_url
```

- [ ] **Step 5: Remove relocated tests from `test_zenodo_adapter.py`**

Delete the 9 test functions named in Step 1 (`test_synthesize_passthrough_https_under_any_scheme` through `test_synthesize_empty_inputs`) from `tests/platforms/test_zenodo_adapter.py`. **Keep** `test_expand_record_emits_all_synthesized_schemes` (that one tests the adapter's expand call site, not the synthesizer itself — it stays).

- [ ] **Step 6: Run all affected suites — verify everything still green**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_datacite.py tests/platforms/test_zenodo_adapter.py -v --no-cov
```
Expected: ~9 tests pass in test_datacite.py + all remaining Zenodo adapter tests pass.

- [ ] **Step 7: Commit**

```
git add src/open_pulse_crawler/platforms/datacite.py tests/platforms/test_datacite.py src/open_pulse_crawler/platforms/zenodo/adapter.py tests/platforms/test_zenodo_adapter.py
git commit -m "refactor(datacite): lift _synthesize_target_url to shared platforms/datacite.py"
```

---

## Task 2: Three Infoscience subkinds in `models.py`

**Files:**
- Modify: `src/open_pulse_crawler/models.py`
- Modify: `tests/test_models.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_models.py`:

```python
# --- Infoscience subkinds (Spec 3) ---------------------------------
from open_pulse_crawler.models import (
    InfoscienceItem, InfosciencePerson, InfoscienceOrgUnit,
)


def test_infoscience_item_subkind_and_fields():
    item = InfoscienceItem(
        url="https://infoscience.epfl.ch/handle/20.500.14299/182247",
        full_name="20.500.14299/182247",
        platform="infoscience",
        handle="20.500.14299/182247",
        uuid="80f7da77-dc21-430e-88a2-f07ede2cb194",
        resource_type="master thesis",
        title="A study of X",
        publication_date="2024-06-30",
        authors=[
            {"name": "Doe, J.", "orcid": "0000-0001-2345-6789",
             "authority_uuid": "31b1115e-c04a-445d-b905-18616ef2aacb"},
        ],
        keywords=["urban planning", "thesis"],
        license="CC-BY-4.0",
    )
    assert item.subkind == "InfoscienceItem"
    assert item.handle == "20.500.14299/182247"
    assert item.uuid == "80f7da77-dc21-430e-88a2-f07ede2cb194"
    assert item.resource_type == "master thesis"
    assert item.is_fork is False  # inherited; no DSpace fork concept
    assert item.dependents == []  # inherited; not a DSpace concept


def test_infoscience_person_subkind_and_fields():
    person = InfosciencePerson(
        url="https://infoscience.epfl.ch/handle/20.500.14299/99923",
        login="123456",  # SciPer ID
        platform="infoscience",
        handle="20.500.14299/99923",
        uuid="31b1115e-c04a-445d-b905-18616ef2aacb",
        given_name="Nicholas",
        family_name="Molyneaux",
        orcid="0000-0001-2345-6789",
        sciper_id="123456",
        affiliation_name="TRANSP-OR",
        affiliation_uuid="aaaa1111-2222-3333-4444-555566667777",
    )
    assert person.subkind == "InfosciencePerson"
    assert person.handle == "20.500.14299/99923"
    assert person.sciper_id == "123456"
    assert person.orcid == "0000-0001-2345-6789"
    assert person.followers == []  # Infoscience has no social graph


def test_infoscience_orgunit_subkind_and_fields():
    ou = InfoscienceOrgUnit(
        url="https://infoscience.epfl.ch/handle/20.500.14299/77777",
        login="TRANSP-OR",
        platform="infoscience",
        handle="20.500.14299/77777",
        uuid="aaaa1111-2222-3333-4444-555566667777",
        unit_id="TRANSP-OR",
        parent_uuid="bbbb2222-3333-4444-5555-666677778888",
        parent_url="https://infoscience.epfl.ch/handle/20.500.14299/11111",
        unit_type="laboratory",
    )
    assert ou.subkind == "InfoscienceOrgUnit"
    assert ou.parent_uuid == "bbbb2222-3333-4444-5555-666677778888"
    assert ou.unit_type == "laboratory"


def test_graphdata_accepts_infoscience_subclasses_in_existing_dicts():
    from open_pulse_crawler.models import GraphData
    g = GraphData()
    g.users["https://infoscience.epfl.ch/handle/20.500.14299/99923"] = InfosciencePerson(
        url="https://infoscience.epfl.ch/handle/20.500.14299/99923",
        login="123456", platform="infoscience",
        handle="20.500.14299/99923",
        uuid="31b1115e-c04a-445d-b905-18616ef2aacb",
    )
    g.orgs["https://infoscience.epfl.ch/handle/20.500.14299/77777"] = InfoscienceOrgUnit(
        url="https://infoscience.epfl.ch/handle/20.500.14299/77777",
        login="TRANSP-OR", platform="infoscience",
        handle="20.500.14299/77777",
        uuid="aaaa1111-2222-3333-4444-555566667777",
    )
    g.repos["https://infoscience.epfl.ch/handle/20.500.14299/182247"] = InfoscienceItem(
        url="https://infoscience.epfl.ch/handle/20.500.14299/182247",
        full_name="20.500.14299/182247", platform="infoscience",
        handle="20.500.14299/182247",
        uuid="80f7da77-dc21-430e-88a2-f07ede2cb194",
    )
    restored = GraphData.model_validate_json(g.model_dump_json())
    assert isinstance(restored.users["https://infoscience.epfl.ch/handle/20.500.14299/99923"], InfosciencePerson)
    assert isinstance(restored.orgs["https://infoscience.epfl.ch/handle/20.500.14299/77777"], InfoscienceOrgUnit)
    assert isinstance(restored.repos["https://infoscience.epfl.ch/handle/20.500.14299/182247"], InfoscienceItem)
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_models.py -v --no-cov
```
Expected: `ImportError: cannot import name 'InfoscienceItem'`.

- [ ] **Step 3: Add the three subclasses to `models.py`**

Find the existing Zenodo subclasses (`ZenodoUserModel`, `ZenodoCommunityModel`, `ZenodoRecordModel`). Add the Infoscience subclasses right after them, before the three discriminated-union definitions:

```python
class InfosciencePerson(UserModel):
    """A DSpace-CRIS Person entity — an EPFL researcher profile.

    Distinct from cross-platform identity resolution (still out of scope).
    A Person here is purely an Infoscience platform entity, identified by
    its DSpace UUID and Handle. ORCID / SciPer / Scopus IDs are kept as
    embedded metadata, NOT as cross-platform identity anchors.

    Social fields (``followers`` / ``following`` / ``starred_repositories`` /
    ``watched_repositories``) inherited from ``UserModel`` are unused —
    Infoscience has no social graph.
    """
    subkind: Literal["InfosciencePerson"] = "InfosciencePerson"
    handle: str
    uuid: str
    given_name: str = ""
    family_name: str = ""
    orcid: Optional[str] = None
    sciper_id: Optional[str] = None
    email: str = ""
    scopus_id: Optional[str] = None
    affiliation_name: str = ""
    affiliation_uuid: Optional[str] = None


class InfoscienceOrgUnit(OrgModel):
    """A DSpace-CRIS OrgUnit entity — an EPFL department, school, or lab.

    Forms the canonical EPFL hierarchy: school → faculty → department →
    laboratory. Parent pointer is set when CRIS exposes a parent relation.
    ``members`` (inherited from ``OrgModel``) stays empty by default;
    population is opt-in via ``opts.crawl_members=True`` on the expand
    call to avoid fetching potentially-large member lists.
    """
    subkind: Literal["InfoscienceOrgUnit"] = "InfoscienceOrgUnit"
    handle: str
    uuid: str
    unit_id: Optional[str] = None
    parent_uuid: Optional[str] = None
    parent_url: Optional[str] = None
    unit_type: str = ""


class InfoscienceItem(RepoModel):
    """A DSpace item on Infoscience — publication or resource.

    All artifacts (papers, theses, datasets, software, presentations, …)
    are DSpace ``item`` entities with the same shape. The ``resource_type``
    field carries Dublin Core ``dc.type`` so downstream code can filter
    publications vs datasets without an isinstance switch.

    ``authors`` is a ``list[dict]`` carrying author names + ORCID + the
    CRIS Person authority UUID — embedded, NOT crawled to Person nodes.
    The adapter's ``_expand_item`` emits ``authored_by`` edges per
    authority UUID without forcing eager Person fetches.

    Inherited ``RepoModel.contributors`` / ``forked_from`` / ``is_fork`` /
    ``dependents`` / ``dependencies`` stay at defaults — DSpace has no
    fork or dependency concept.
    """
    subkind: Literal["InfoscienceItem"] = "InfoscienceItem"
    handle: str
    uuid: str
    doi: Optional[str] = None
    resource_type: str = ""
    publication_date: str = ""
    title: str = ""
    abstract: str = ""
    authors: List[Dict[str, Any]] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    language: str = ""
    license: str = ""
    journal: str = ""
    issn: str = ""
    isbn: str = ""
```

Then widen the three discriminated unions (find the existing `UserNode`/`OrgNode`/`RepoNode` definitions):

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

`Any`, `Dict`, `List`, `Literal`, `Optional`, `Union`, `Annotated`, `Field` should already be imported. If not, add them.

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_models.py -v --no-cov
```
Expected: all 4 new Infoscience tests pass; all existing tests still green.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/models.py tests/test_models.py
git commit -m "feat(models): Infoscience subkinds — Person, OrgUnit, Item"
```

---

# Block B — `InfoscienceClient`

## Task 3: `InfoscienceClient` skeleton + Bearer auth + 429 retry

**Files:**
- Create: `src/open_pulse_crawler/platforms/infoscience/__init__.py`
- Create: `src/open_pulse_crawler/platforms/infoscience/client.py`
- Create: `tests/platforms/test_infoscience_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/platforms/test_infoscience_client.py
"""Tests for the Infoscience (DSpace 7) HTTP client wrapper."""
from unittest.mock import MagicMock, patch
import pytest
import httpx

from open_pulse_crawler.platforms.infoscience.client import InfoscienceClient


def test_authenticated_sets_bearer_header():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=["dspace-tok"])
    assert c._session.headers.get("Authorization") == "Bearer dspace-tok"


def test_anonymous_sends_no_auth_header():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    assert "Authorization" not in c._session.headers


def test_base_url_uses_https_with_host():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    assert c.base_url == "https://infoscience.epfl.ch"


def test_rotate_in_anonymous_mode_is_noop():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    c._rotate()  # must not raise
    assert "Authorization" not in c._session.headers


def test_rotate_cycles_token_pool():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=["a", "b"])
    assert c._session.headers["Authorization"] == "Bearer a"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer b"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer a"
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_infoscience_client.py -v --no-cov
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the skeleton**

```python
# src/open_pulse_crawler/platforms/infoscience/__init__.py
"""Infoscience (DSpace 7) platform implementation."""
from .client import InfoscienceClient

__all__ = ["InfoscienceClient"]
```

```python
# src/open_pulse_crawler/platforms/infoscience/client.py
"""Thin httpx wrapper for Infoscience's DSpace 7 REST API.

Mirrors the Zenodo client's shape but speaks HAL+JSON instead of plain
JSON. Pagination follows ``_links.next``. 429 responses honor the
``Retry-After`` header with one automatic retry, then raise.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Anonymous DSpace page-size cap is 100 (no 25 limit like Zenodo).
DEFAULT_PAGE_SIZE = 100

# Cap the Retry-After value the client honors, to avoid hanging on a
# rogue server response. 60 seconds is enough for any reasonable backoff.
MAX_RETRY_AFTER_SECONDS = 60


class InfoscienceClient:
    """HTTP client for Infoscience's DSpace 7 REST endpoints.

    Authentication: ``Authorization: Bearer <token>`` when ``tokens`` is
    non-empty. Anonymous (no header) when ``tokens=[]`` — Infoscience's
    public items, persons, and orgunits are readable without auth.

    Rate-limit handling: on HTTP 429, the client reads the ``Retry-After``
    header (or defaults to 5 seconds), sleeps, and retries the request
    once. A second 429 raises ``httpx.HTTPStatusError`` so callers can
    decide whether to keep trying.
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
            timeout=httpx.Timeout(15.0, connect=8.0),
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        if not self.tokens:
            logger.warning(
                "InfoscienceClient(%s) constructed with no tokens — "
                "anonymous reads only; rate limits will be tight.",
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

    # ---- token rotation -----------------------------------------------------

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
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_infoscience_client.py -v --no-cov
```
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/infoscience/__init__.py src/open_pulse_crawler/platforms/infoscience/client.py tests/platforms/test_infoscience_client.py
git commit -m "feat(infoscience): InfoscienceClient skeleton (Bearer auth + token rotation)"
```

---

## Task 4: Endpoints + 429 retry + HAL+JSON pagination

**Files:**
- Modify: `src/open_pulse_crawler/platforms/infoscience/client.py`
- Modify: `tests/platforms/test_infoscience_client.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_infoscience_client.py`:

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


def test_get_item_by_handle_200_returns_dict():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    payload = {"uuid": "abc", "handle": "20.500.14299/182247", "entityType": "Publication"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_item_by_handle("20.500.14299/182247")
    g.assert_called_once_with("/server/api/handle/20.500.14299/182247")
    assert result == payload


def test_get_item_by_handle_404_returns_none():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_item_by_handle("20.500.14299/missing") is None


def test_get_item_by_uuid_200():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    payload = {"uuid": "abc", "handle": "20.500.14299/1", "entityType": "Person"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_item_by_uuid("abc")
    g.assert_called_once_with("/server/api/core/items/abc")
    assert result == payload


def test_429_retries_after_retry_after_header(monkeypatch):
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    # First call: 429 with Retry-After: 1. Second call: 200.
    responses = [
        _make_response(429, headers={"Retry-After": "1"}),
        _make_response(200, {"uuid": "abc"}),
    ]
    sleep_calls = []
    monkeypatch.setattr("open_pulse_crawler.platforms.infoscience.client.time.sleep",
                       lambda s: sleep_calls.append(s))
    with patch.object(c._session, "get", side_effect=responses):
        result = c.get_item_by_handle("20.500.14299/x")
    assert result == {"uuid": "abc"}
    assert sleep_calls == [1.0]  # honored Retry-After


def test_429_twice_raises(monkeypatch):
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    monkeypatch.setattr("open_pulse_crawler.platforms.infoscience.client.time.sleep", lambda s: None)
    responses = [_make_response(429, headers={"Retry-After": "0"}), _make_response(429)]
    with patch.object(c._session, "get", side_effect=responses):
        with pytest.raises(httpx.HTTPStatusError):
            c.get_item_by_handle("20.500.14299/x")


def test_429_caps_retry_after_at_60s(monkeypatch):
    """A server claiming Retry-After: 9999 should still be honored only up to 60s."""
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    sleep_calls = []
    monkeypatch.setattr("open_pulse_crawler.platforms.infoscience.client.time.sleep",
                       lambda s: sleep_calls.append(s))
    responses = [_make_response(429, headers={"Retry-After": "9999"}), _make_response(200, {"uuid": "x"})]
    with patch.object(c._session, "get", side_effect=responses):
        c.get_item_by_handle("20.500.14299/x")
    assert sleep_calls == [60.0]


def test_iter_person_items_single_page():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    body = {
        "_embedded": {
            "searchResult": {
                "_embedded": {
                    "objects": [
                        {"_embedded": {"indexableObject": {"uuid": "i1", "handle": "h/1"}}},
                        {"_embedded": {"indexableObject": {"uuid": "i2", "handle": "h/2"}}},
                    ]
                },
                "_links": {},
            }
        }
    }
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        items = list(c.iter_person_items("person-uuid"))
    g.assert_called_once_with(
        "/server/api/discover/search/objects",
        params={"dsoType": "item", "query": "author.authority:person-uuid", "size": 100},
    )
    assert [i["uuid"] for i in items] == ["i1", "i2"]


def test_iter_person_items_follows_links_next():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    page1 = {
        "_embedded": {"searchResult": {
            "_embedded": {"objects": [
                {"_embedded": {"indexableObject": {"uuid": "i1", "handle": "h/1"}}},
            ]},
            "_links": {"next": {"href": "https://infoscience.epfl.ch/server/api/discover/search/objects?dsoType=item&page=2"}},
        }},
    }
    page2 = {
        "_embedded": {"searchResult": {
            "_embedded": {"objects": [
                {"_embedded": {"indexableObject": {"uuid": "i2", "handle": "h/2"}}},
            ]},
            "_links": {},
        }},
    }
    with patch.object(c._session, "get") as g:
        g.side_effect = [_make_response(200, page1), _make_response(200, page2)]
        items = list(c.iter_person_items("person-uuid"))
    assert [i["uuid"] for i in items] == ["i1", "i2"]
    assert g.call_count == 2


def test_iter_orgunit_items_query():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    body = {"_embedded": {"searchResult": {"_embedded": {"objects": []}, "_links": {}}}}
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        list(c.iter_orgunit_items("orgunit-uuid"))
    g.assert_called_once_with(
        "/server/api/discover/search/objects",
        params={"dsoType": "item", "query": "author.parent-organization.authority:orgunit-uuid", "size": 100},
    )


def test_iter_orgunit_persons_query():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    body = {"_embedded": {"searchResult": {"_embedded": {"objects": []}, "_links": {}}}}
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        list(c.iter_orgunit_persons("orgunit-uuid"))
    g.assert_called_once_with(
        "/server/api/discover/search/objects",
        params={
            "dsoType": "item",
            "query": "dspace.entity.type:Person AND author.parent-organization.authority:orgunit-uuid",
            "size": 100,
        },
    )
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_infoscience_client.py -v --no-cov
```
Expected: `AttributeError: 'InfoscienceClient' object has no attribute 'get_item_by_handle'`.

- [ ] **Step 3: Implement endpoints + 429 retry + pagination**

Append to `src/open_pulse_crawler/platforms/infoscience/client.py`:

```python
    # ---- single-entity fetches with 429 retry ------------------------------

    def _do_get(self, path: str, params: Optional[Dict[str, Any]] = None,
                _retried: bool = False) -> httpx.Response:
        """GET ``path`` with one automatic retry on HTTP 429.

        Honors the ``Retry-After`` header (capped at ``MAX_RETRY_AFTER_SECONDS``).
        Second 429 raises via the caller's ``raise_for_status``.
        """
        resp = self._session.get(path, params=params) if params is not None \
            else self._session.get(path)
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

    def _request_json(self, path: str, params: Optional[Dict[str, Any]] = None,
                      *, degrade_on_auth: bool = False) -> Optional[Dict[str, Any]]:
        """GET ``path`` and return parsed JSON.

        404 → ``None``. 401/403 → ``None`` when ``degrade_on_auth=True``,
        else raise. Any other non-2xx raises ``httpx.HTTPStatusError``.
        """
        resp = self._do_get(path, params)
        if resp.status_code == 404:
            return None
        if degrade_on_auth and resp.status_code in (401, 403):
            logger.warning(
                "%s on %s returned %s; degrading to None.",
                path, self.host, resp.status_code,
            )
            return None
        if not resp.is_success:
            resp.raise_for_status()
        return resp.json()

    def get_item_by_handle(self, handle: str) -> Optional[Dict[str, Any]]:
        """Return the item JSON for the given handle, or ``None`` on 404."""
        return self._request_json(f"/server/api/handle/{handle}")

    def get_item_by_uuid(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Return the item JSON for the given UUID, or ``None`` on 404."""
        return self._request_json(f"/server/api/core/items/{uuid}")

    # ---- HAL+JSON paginated iteration --------------------------------------

    def _iter_paginated(
        self,
        path: str,
        params: Dict[str, Any],
    ) -> Iterable[Dict[str, Any]]:
        """Yield indexable objects across all pages of a DSpace search.

        DSpace 7 wraps search results in:
            _embedded.searchResult._embedded.objects[]._embedded.indexableObject
        and exposes the next-page URL at
            _embedded.searchResult._links.next.href
        """
        url = path
        current_params = params
        while True:
            resp = self._do_get(url, current_params)
            if not resp.is_success:
                resp.raise_for_status()
            body = resp.json()
            search = body.get("_embedded", {}).get("searchResult", {})
            objects = search.get("_embedded", {}).get("objects", [])
            for obj in objects:
                ix = obj.get("_embedded", {}).get("indexableObject")
                if ix is not None:
                    yield ix
            next_link = search.get("_links", {}).get("next", {})
            next_href = next_link.get("href") if isinstance(next_link, dict) else None
            if not next_href:
                return
            url = next_href
            current_params = None  # next-link has params baked in

    def iter_person_items(self, person_uuid: str) -> Iterable[Dict[str, Any]]:
        """Items where the given Person UUID is an author authority."""
        return self._iter_paginated(
            "/server/api/discover/search/objects",
            {"dsoType": "item", "query": f"author.authority:{person_uuid}", "size": DEFAULT_PAGE_SIZE},
        )

    def iter_orgunit_items(self, orgunit_uuid: str) -> Iterable[Dict[str, Any]]:
        """Items whose authors are affiliated with the given OrgUnit UUID."""
        return self._iter_paginated(
            "/server/api/discover/search/objects",
            {"dsoType": "item",
             "query": f"author.parent-organization.authority:{orgunit_uuid}",
             "size": DEFAULT_PAGE_SIZE},
        )

    def iter_orgunit_persons(self, orgunit_uuid: str) -> Iterable[Dict[str, Any]]:
        """Person entities affiliated with the given OrgUnit UUID."""
        return self._iter_paginated(
            "/server/api/discover/search/objects",
            {"dsoType": "item",
             "query": f"dspace.entity.type:Person AND author.parent-organization.authority:{orgunit_uuid}",
             "size": DEFAULT_PAGE_SIZE},
        )
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_infoscience_client.py -v --no-cov
```
Expected: all 15 tests pass (5 from Task 3 + 10 new).

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/infoscience/client.py tests/platforms/test_infoscience_client.py
git commit -m "feat(infoscience): client endpoints + 429 retry-after + HAL+JSON pagination"
```

---

# Block C — `InfoscienceAdapter`

## Task 5: Adapter classify + normalize_uri

**Files:**
- Create: `src/open_pulse_crawler/platforms/infoscience/adapter.py`
- Create: `tests/platforms/test_infoscience_adapter.py`
- Modify: `src/open_pulse_crawler/platforms/infoscience/__init__.py` (add re-export)

- [ ] **Step 1: Write failing tests**

```python
# tests/platforms/test_infoscience_adapter.py
"""Tests for the Infoscience PlatformAdapter."""
from unittest.mock import MagicMock
import pytest

from open_pulse_crawler.models import (
    InfoscienceItem, InfosciencePerson, InfoscienceOrgUnit,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.infoscience.adapter import InfoscienceAdapter


@pytest.fixture
def adapter():
    client = MagicMock()
    return InfoscienceAdapter(client=client, instance_host="infoscience.epfl.ch")


# --- classify -------------------------------------------------------

def test_classify_handle_url(adapter):
    assert adapter.classify("https://infoscience.epfl.ch/handle/20.500.14299/182247") == NodeKind.USER_OR_ORG


def test_classify_unknown_path(adapter):
    assert adapter.classify("https://infoscience.epfl.ch/about") is None


# --- normalize_uri --------------------------------------------------

def test_normalize_handle_url_strips_trailing_slash(adapter):
    assert adapter.normalize_uri("https://infoscience.epfl.ch/handle/20.500.14299/182247/") == \
        "https://infoscience.epfl.ch/handle/20.500.14299/182247"


def test_normalize_handle_url_lowercases_host(adapter):
    assert adapter.normalize_uri("https://INFOSCIENCE.EPFL.CH/handle/20.500.14299/182247") == \
        "https://infoscience.epfl.ch/handle/20.500.14299/182247"


def test_normalize_uuid_url_resolves_to_handle_via_cache(adapter):
    """A UUID-form URL fetches once to learn the handle, then returns canonical."""
    adapter._client.get_item_by_uuid.return_value = {
        "uuid": "abc-123", "handle": "20.500.14299/182247",
    }
    result = adapter.normalize_uri("https://infoscience.epfl.ch/server/api/core/items/abc-123")
    assert result == "https://infoscience.epfl.ch/handle/20.500.14299/182247"
    # Subsequent lookup hits cache, no second fetch
    adapter._client.get_item_by_uuid.reset_mock()
    second = adapter.normalize_uri("https://infoscience.epfl.ch/server/api/core/items/abc-123")
    assert second == "https://infoscience.epfl.ch/handle/20.500.14299/182247"
    adapter._client.get_item_by_uuid.assert_not_called()


def test_normalize_uuid_url_404_returns_uuid_form_unchanged(adapter):
    """If the UUID can't be resolved, keep the UUID-form URL."""
    adapter._client.get_item_by_uuid.return_value = None
    result = adapter.normalize_uri("https://infoscience.epfl.ch/server/api/core/items/missing")
    assert result == "https://infoscience.epfl.ch/server/api/core/items/missing"
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_infoscience_adapter.py -v --no-cov
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement classify + normalize_uri**

```python
# src/open_pulse_crawler/platforms/infoscience/adapter.py
"""Infoscience PlatformAdapter — classify / fetch / expand / normalize_uri.

See ``docs/superpowers/specs/2026-05-28-infoscience-adapter-design.md``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from ...models import (
    InfoscienceItem, InfosciencePerson, InfoscienceOrgUnit,
)
from ...node_id import NodeKind, canonical_url
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo
from ..datacite import synthesize_target_url
from .client import InfoscienceClient

logger = logging.getLogger(__name__)

_HANDLE_PATH = re.compile(r"^handle/(?P<handle>[^/]+/[^/]+)$")
_UUID_PATH = re.compile(r"^server/api/core/items/(?P<uuid>[0-9a-fA-F-]+)$")


class InfoscienceAdapter(PlatformAdapter):
    platform: ClassVar[str] = "infoscience"

    def __init__(self, client: InfoscienceClient, instance_host: str) -> None:
        self._client = client
        self.instance_host = instance_host
        # uuid → "20.500.14299/<id>" mapping, populated lazily.
        self._uuid_to_handle: Dict[str, str] = {}

    # ---- normalize_uri -----------------------------------------------------

    def normalize_uri(self, raw: str) -> str:
        """Return the canonical Infoscience handle URL for ``raw``.

        Accepts canonical ``/handle/<prefix>/<id>`` URLs and ``/server/api/core/items/<uuid>``
        URLs. UUID-form URLs are resolved via a cached fetch; if the UUID
        doesn't resolve, the UUID-form URL is returned unchanged so the
        BFS engine can still queue it (the eventual ``fetch`` call will
        return ``None`` and the node is skipped).
        """
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"empty or non-string seed: {raw!r}")

        parts = urlparse(raw)
        host = (parts.netloc or self.instance_host).lower()
        path = parts.path or "/"

        # UUID-form? Resolve to handle.
        m = _UUID_PATH.match(path.strip("/"))
        if m:
            uuid = m.group("uuid")
            handle = self._uuid_to_handle.get(uuid)
            if handle is None:
                raw_obj = self._client.get_item_by_uuid(uuid)
                if raw_obj is not None:
                    handle = raw_obj.get("handle")
                    if handle:
                        self._uuid_to_handle[uuid] = handle
            if handle:
                return canonical_url(host, f"/handle/{handle}")
            # Couldn't resolve; return the UUID-form URL unchanged.
            return canonical_url(host, path)

        return canonical_url(host, path)

    # ---- classify ----------------------------------------------------------

    def classify(self, uri: str) -> Optional[NodeKind]:
        """All Infoscience entity types share the ``/handle/`` URL form.

        Returns ``USER_OR_ORG`` for any handle URL; ``fetch`` does the real
        dispatch via the server-side ``entityType`` discriminator.
        """
        path = urlparse(self.normalize_uri(uri)).path.strip("/")
        if _HANDLE_PATH.match(path):
            return NodeKind.USER_OR_ORG
        return None

    # ---- fetch (placeholder — Task 6 implements) ---------------------------

    def fetch(self, uri: str):
        raise NotImplementedError("Task 6 implements fetch()")

    # ---- expand (placeholder — Task 7 implements) --------------------------

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        raise NotImplementedError("Task 7 implements expand()")

    # ---- rate_limit --------------------------------------------------------

    def rate_limit_state(self) -> RateLimitInfo:
        # DSpace doesn't reliably surface rate-limit headers; return
        # conservative placeholders matching the Zenodo adapter pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
```

Update `__init__.py`:

```python
# src/open_pulse_crawler/platforms/infoscience/__init__.py
"""Infoscience (DSpace 7) platform implementation."""
from .client import InfoscienceClient
from .adapter import InfoscienceAdapter

__all__ = ["InfoscienceClient", "InfoscienceAdapter"]
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_infoscience_adapter.py -v --no-cov
```
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/infoscience/adapter.py src/open_pulse_crawler/platforms/infoscience/__init__.py tests/platforms/test_infoscience_adapter.py
git commit -m "feat(infoscience): InfoscienceAdapter classify + normalize_uri with UUID→handle cache"
```

---

## Task 6: Adapter fetch (entityType dispatch + three builders)

**Files:**
- Modify: `src/open_pulse_crawler/platforms/infoscience/adapter.py`
- Modify: `tests/platforms/test_infoscience_adapter.py` (append)

- [ ] **Step 1: Write failing tests**

```python
def test_fetch_item_publication(adapter):
    adapter._client.get_item_by_handle.return_value = {
        "uuid": "80f7da77-dc21-430e-88a2-f07ede2cb194",
        "handle": "20.500.14299/182247",
        "entityType": "Publication",
        "name": "A study of X",
        "metadata": {
            "dc.title": [{"value": "A study of X"}],
            "dc.type": [{"value": "journal article"}],
            "dc.date.issued": [{"value": "2024-06-30"}],
            "dc.contributor.author": [
                {"value": "Doe, J.", "authority": "auth-uuid-1", "confidence": 600},
            ],
            "dc.subject": [{"value": "kw1"}, {"value": "kw2"}],
            "dc.identifier.doi": [{"value": "10.1234/foo"}],
            "datacite.rights": [{"value": "CC-BY-4.0"}],
        },
    }
    node = adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/182247")
    assert isinstance(node, InfoscienceItem)
    assert node.handle == "20.500.14299/182247"
    assert node.uuid == "80f7da77-dc21-430e-88a2-f07ede2cb194"
    assert node.title == "A study of X"
    assert node.resource_type == "journal article"
    assert node.publication_date == "2024-06-30"
    assert node.doi == "10.1234/foo"
    assert node.license == "CC-BY-4.0"
    assert node.keywords == ["kw1", "kw2"]
    assert len(node.authors) == 1
    assert node.authors[0]["name"] == "Doe, J."
    assert node.authors[0]["authority_uuid"] == "auth-uuid-1"


def test_fetch_person(adapter):
    adapter._client.get_item_by_handle.return_value = {
        "uuid": "31b1115e-c04a-445d-b905-18616ef2aacb",
        "handle": "20.500.14299/99923",
        "entityType": "Person",
        "name": "Molyneaux, Nicholas",
        "metadata": {
            "person.givenname": [{"value": "Nicholas"}],
            "person.familyname": [{"value": "Molyneaux"}],
            "person.identifier.orcid": [{"value": "0000-0001-2345-6789"}],
            "epfl.sciperId": [{"value": "123456"}],
            "person.identifier.scopus-author-id": [{"value": "55512345600"}],
            "person.affiliation.name": [{"value": "TRANSP-OR"}],
            "cris.virtual.parent-organization": [
                {"value": "TRANSP-OR", "authority": "ou-uuid-1"},
            ],
        },
    }
    node = adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/99923")
    assert isinstance(node, InfosciencePerson)
    assert node.given_name == "Nicholas"
    assert node.family_name == "Molyneaux"
    assert node.orcid == "0000-0001-2345-6789"
    assert node.sciper_id == "123456"
    assert node.scopus_id == "55512345600"
    assert node.affiliation_name == "TRANSP-OR"
    assert node.affiliation_uuid == "ou-uuid-1"
    assert node.login == "123456"  # SciPer wins as login


def test_fetch_orgunit_with_parent(adapter):
    adapter._client.get_item_by_handle.return_value = {
        "uuid": "aaaa1111-2222-3333-4444-555566667777",
        "handle": "20.500.14299/77777",
        "entityType": "OrgUnit",
        "name": "TRANSP-OR",
        "metadata": {
            "organization.legalName": [{"value": "TRANSPort and mobility Laboratory"}],
            "organization.identifier": [{"value": "TRANSP-OR"}],
            "organization.type": [{"value": "laboratory"}],
            "organization.parentOrganization": [
                {"value": "STI", "authority": "parent-ou-uuid"},
            ],
        },
    }
    node = adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/77777")
    assert isinstance(node, InfoscienceOrgUnit)
    assert node.unit_id == "TRANSP-OR"
    assert node.unit_type == "laboratory"
    assert node.parent_uuid == "parent-ou-uuid"
    assert node.login == "TRANSP-OR"  # unit_id wins as login


def test_fetch_orgunit_root_no_parent(adapter):
    adapter._client.get_item_by_handle.return_value = {
        "uuid": "root-uuid",
        "handle": "20.500.14299/1",
        "entityType": "OrgUnit",
        "name": "EPFL",
        "metadata": {"organization.identifier": [{"value": "EPFL"}]},
    }
    node = adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/1")
    assert isinstance(node, InfoscienceOrgUnit)
    assert node.parent_uuid is None
    assert node.parent_url is None


def test_fetch_404_returns_none(adapter):
    adapter._client.get_item_by_handle.return_value = None
    assert adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/missing") is None


def test_fetch_unknown_entitytype_defaults_to_item(adapter):
    """An item with empty / unknown entityType is treated as a publication."""
    adapter._client.get_item_by_handle.return_value = {
        "uuid": "x", "handle": "20.500.14299/1",
        "entityType": "",
        "metadata": {"dc.title": [{"value": "Untyped"}]},
    }
    node = adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/1")
    assert isinstance(node, InfoscienceItem)
    assert node.title == "Untyped"
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_infoscience_adapter.py -v --no-cov
```
Expected: 6 new tests fail with `NotImplementedError`.

- [ ] **Step 3: Implement `fetch` + three builders**

In `src/open_pulse_crawler/platforms/infoscience/adapter.py`, replace the `fetch` placeholder and add the three builders. Append after the `classify` method:

```python
    # ---- fetch -------------------------------------------------------------

    def fetch(self, uri: str):
        uri = self.normalize_uri(uri)
        path = urlparse(uri).path.strip("/")
        m = _HANDLE_PATH.match(path)
        if not m:
            return None
        handle = m.group("handle")
        raw = self._client.get_item_by_handle(handle)
        if raw is None:
            return None
        # Cache the UUID → handle so later author/orgunit edge resolutions
        # can avoid re-fetching for the same UUID.
        uuid = raw.get("uuid")
        if uuid:
            self._uuid_to_handle[uuid] = handle

        entity_type = (raw.get("entityType") or "").lower()
        if entity_type == "person":
            return self._build_person(uri, raw)
        if entity_type == "orgunit":
            return self._build_orgunit(uri, raw)
        # Publication / Resource / unspecified → InfoscienceItem
        return self._build_item(uri, raw)

    # ---- builders ----------------------------------------------------------

    @staticmethod
    def _meta_first(metadata: Dict[str, Any], key: str, default: str = "") -> str:
        """Return the first value for a Dublin-Core-style metadata key."""
        entries = metadata.get(key) or []
        if not entries:
            return default
        v = entries[0].get("value") if isinstance(entries[0], dict) else None
        return v if isinstance(v, str) and v else default

    @staticmethod
    def _meta_values(metadata: Dict[str, Any], key: str) -> List[str]:
        """Return all string values for a Dublin-Core-style metadata key."""
        out = []
        for e in metadata.get(key) or []:
            if isinstance(e, dict):
                v = e.get("value")
                if isinstance(v, str) and v:
                    out.append(v)
        return out

    @staticmethod
    def _meta_first_authority(metadata: Dict[str, Any], key: str) -> Optional[str]:
        """Return the ``authority`` UUID from the first entry for ``key``."""
        entries = metadata.get(key) or []
        if not entries or not isinstance(entries[0], dict):
            return None
        auth = entries[0].get("authority")
        return auth if isinstance(auth, str) and auth else None

    def _build_item(self, uri: str, raw: Dict[str, Any]) -> InfoscienceItem:
        meta = raw.get("metadata", {}) or {}
        authors = []
        for entry in meta.get("dc.contributor.author") or []:
            if not isinstance(entry, dict):
                continue
            authors.append({
                "name": entry.get("value", ""),
                "authority_uuid": entry.get("authority"),
                "confidence": entry.get("confidence"),
            })
        return InfoscienceItem(
            url=uri,
            full_name=raw.get("handle", ""),
            platform="infoscience",
            name=raw.get("name", "") or "",
            handle=raw.get("handle", ""),
            uuid=raw.get("uuid", ""),
            doi=self._meta_first(meta, "dc.identifier.doi") or None,
            resource_type=self._meta_first(meta, "dc.type"),
            publication_date=self._meta_first(meta, "dc.date.issued"),
            title=self._meta_first(meta, "dc.title"),
            abstract=self._meta_first(meta, "dc.description.abstract"),
            authors=authors,
            keywords=self._meta_values(meta, "dc.subject"),
            language=self._meta_first(meta, "dc.language.iso"),
            license=self._meta_first(meta, "datacite.rights") or self._meta_first(meta, "dc.rights"),
            journal=self._meta_first(meta, "dc.relation.ispartof"),
            issn=self._meta_first(meta, "dc.identifier.issn"),
            isbn=self._meta_first(meta, "dc.identifier.isbn"),
            extras={"_raw_metadata": meta},  # for _expand_item
        )

    def _build_person(self, uri: str, raw: Dict[str, Any]) -> InfosciencePerson:
        meta = raw.get("metadata", {}) or {}
        sciper = self._meta_first(meta, "epfl.sciperId") or None
        # login: SciPer when present, else the handle's last segment
        login = sciper or (raw.get("handle", "").rsplit("/", 1)[-1])
        affiliation_uuid = self._meta_first_authority(meta, "cris.virtual.parent-organization")
        return InfosciencePerson(
            url=uri,
            login=login,
            platform="infoscience",
            name=raw.get("name", "") or "",
            handle=raw.get("handle", ""),
            uuid=raw.get("uuid", ""),
            given_name=self._meta_first(meta, "person.givenname"),
            family_name=self._meta_first(meta, "person.familyname"),
            orcid=self._meta_first(meta, "person.identifier.orcid") or None,
            sciper_id=sciper,
            email=self._meta_first(meta, "person.email"),
            scopus_id=self._meta_first(meta, "person.identifier.scopus-author-id") or None,
            affiliation_name=self._meta_first(meta, "person.affiliation.name"),
            affiliation_uuid=affiliation_uuid,
        )

    def _build_orgunit(self, uri: str, raw: Dict[str, Any]) -> InfoscienceOrgUnit:
        meta = raw.get("metadata", {}) or {}
        unit_id = self._meta_first(meta, "organization.identifier") or None
        login = unit_id or (raw.get("handle", "").rsplit("/", 1)[-1])
        parent_uuid = self._meta_first_authority(meta, "organization.parentOrganization")
        parent_url = None
        if parent_uuid:
            parent_handle = self._uuid_to_handle.get(parent_uuid)
            if parent_handle:
                parent_url = f"https://{self.instance_host}/handle/{parent_handle}"
        return InfoscienceOrgUnit(
            url=uri,
            login=login,
            platform="infoscience",
            name=raw.get("name", "") or "",
            handle=raw.get("handle", ""),
            uuid=raw.get("uuid", ""),
            unit_id=unit_id,
            parent_uuid=parent_uuid,
            parent_url=parent_url,
            unit_type=self._meta_first(meta, "organization.type"),
        )
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_infoscience_adapter.py -v --no-cov
```
Expected: all 12 tests pass (6 from Task 5 + 6 new).

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/infoscience/adapter.py tests/platforms/test_infoscience_adapter.py
git commit -m "feat(infoscience): InfoscienceAdapter.fetch with entityType dispatch + DSpace metadata builders"
```

---

## Task 7: Adapter expand (8 edge kinds)

**Files:**
- Modify: `src/open_pulse_crawler/platforms/infoscience/adapter.py`
- Modify: `tests/platforms/test_infoscience_adapter.py` (append)

- [ ] **Step 1: Write failing tests**

```python
from open_pulse_crawler.platforms.base import ExpandOpts, Edge


# Helper: build an InfoscienceItem with raw metadata stashed in extras.
def _stub_item(adapter, *, raw_meta):
    return InfoscienceItem(
        url="https://infoscience.epfl.ch/handle/20.500.14299/1",
        full_name="20.500.14299/1",
        platform="infoscience",
        handle="20.500.14299/1",
        uuid="item-uuid",
        extras={"_raw_metadata": raw_meta},
    )


def test_expand_item_emits_authored_by_for_authors_with_authority(adapter):
    item = _stub_item(adapter, raw_meta={
        "dc.contributor.author": [
            {"value": "Doe, J.", "authority": "author-uuid-1"},
            {"value": "No, A.", "authority": None},        # skipped — no authority
            {"value": "Smith, K.", "authority": "author-uuid-2"},
        ],
    })
    # _uuid_to_handle is empty: emit UUID-form URLs for unresolved authors.
    edges = [e for e in adapter.expand(item, ExpandOpts()) if e.kind == "authored_by"]
    assert sorted(e.dst for e in edges) == [
        "https://infoscience.epfl.ch/server/api/core/items/author-uuid-1",
        "https://infoscience.epfl.ch/server/api/core/items/author-uuid-2",
    ]


def test_expand_item_uses_cached_handle_when_known(adapter):
    """When _uuid_to_handle has the author's handle, emit the canonical URL."""
    adapter._uuid_to_handle["author-uuid-1"] = "20.500.14299/99923"
    item = _stub_item(adapter, raw_meta={
        "dc.contributor.author": [
            {"value": "Doe, J.", "authority": "author-uuid-1"},
        ],
    })
    edges = [e for e in adapter.expand(item, ExpandOpts()) if e.kind == "authored_by"]
    assert edges[0].dst == "https://infoscience.epfl.ch/handle/20.500.14299/99923"


def test_expand_item_emits_affiliated_with_from_cris_virtual_department(adapter):
    item = _stub_item(adapter, raw_meta={
        "cris.virtual.department": [{"value": "TRANSP-OR", "authority": "ou-uuid-1"}],
    })
    edges = [e for e in adapter.expand(item, ExpandOpts()) if e.kind == "affiliated_with"]
    assert len(edges) == 1
    assert edges[0].dst == "https://infoscience.epfl.ch/server/api/core/items/ou-uuid-1"


def test_expand_item_emits_related_to_via_datacite_synthesizer(adapter):
    item = _stub_item(adapter, raw_meta={
        "dc.relation.uri": [{"value": "https://github.com/foo/bar"}],
        "dc.relation.isversionof": [{"value": "10.5281/zenodo.99"}],
        "dc.relation.issupplementto": [{"value": "arXiv:2401.12345"}],
    })
    edges = [e for e in adapter.expand(item, ExpandOpts()) if e.kind.startswith("related_to.")]
    kinds_dsts = sorted((e.kind, e.dst) for e in edges)
    # Lowercase relation qualifiers normalize to camelCase
    assert kinds_dsts == [
        ("related_to.isSupplementTo", "https://arxiv.org/abs/2401.12345"),
        ("related_to.isVersionOf", "https://zenodo.org/records/99"),
        ("related_to.references", "https://github.com/foo/bar"),
    ]


def test_expand_person_emits_authored_and_member_of(adapter):
    adapter._client.iter_person_items.return_value = iter([
        {"uuid": "i1", "handle": "20.500.14299/1"},
        {"uuid": "i2", "handle": "20.500.14299/2"},
    ])
    person = InfosciencePerson(
        url="https://infoscience.epfl.ch/handle/20.500.14299/99923",
        login="123456", platform="infoscience",
        handle="20.500.14299/99923", uuid="person-uuid",
        affiliation_uuid="ou-uuid-1",
    )
    edges = list(adapter.expand(person, ExpandOpts()))
    authored = [e for e in edges if e.kind == "authored"]
    members = [e for e in edges if e.kind == "member_of"]
    assert sorted(e.dst for e in authored) == [
        "https://infoscience.epfl.ch/handle/20.500.14299/1",
        "https://infoscience.epfl.ch/handle/20.500.14299/2",
    ]
    assert members == [Edge(
        src=person.url, kind="member_of",
        dst="https://infoscience.epfl.ch/server/api/core/items/ou-uuid-1",
    )]


def test_expand_orgunit_emits_has_publication_and_parent_of(adapter):
    adapter._client.iter_orgunit_items.return_value = iter([
        {"uuid": "i1", "handle": "20.500.14299/1"},
    ])
    # No children: filter for entity_type:OrgUnit + parent.authority = us
    adapter._client._iter_paginated = MagicMock(return_value=iter([
        {"uuid": "child-uuid", "handle": "20.500.14299/2"},
    ]))
    ou = InfoscienceOrgUnit(
        url="https://infoscience.epfl.ch/handle/20.500.14299/77777",
        login="TRANSP-OR", platform="infoscience",
        handle="20.500.14299/77777", uuid="ou-uuid-1",
    )
    edges = list(adapter.expand(ou, ExpandOpts()))
    has_pub = [e for e in edges if e.kind == "has_publication"]
    parent_of = [e for e in edges if e.kind == "parent_of"]
    assert has_pub[0].dst == "https://infoscience.epfl.ch/handle/20.500.14299/1"
    assert parent_of[0].dst == "https://infoscience.epfl.ch/handle/20.500.14299/2"


def test_expand_orgunit_members_gated_off_by_default(adapter):
    """crawl_members defaults to False — iter_orgunit_persons must not be called."""
    adapter._client.iter_orgunit_items.return_value = iter([])
    adapter._client._iter_paginated = MagicMock(return_value=iter([]))
    ou = InfoscienceOrgUnit(
        url="https://infoscience.epfl.ch/handle/20.500.14299/77777",
        login="TRANSP-OR", platform="infoscience",
        handle="20.500.14299/77777", uuid="ou-uuid-1",
    )
    list(adapter.expand(ou, ExpandOpts()))
    adapter._client.iter_orgunit_persons.assert_not_called()


def test_expand_orgunit_members_emitted_when_crawl_members_true(adapter):
    adapter._client.iter_orgunit_items.return_value = iter([])
    adapter._client._iter_paginated = MagicMock(return_value=iter([]))
    adapter._client.iter_orgunit_persons.return_value = iter([
        {"uuid": "p1", "handle": "20.500.14299/9001"},
    ])
    ou = InfoscienceOrgUnit(
        url="https://infoscience.epfl.ch/handle/20.500.14299/77777",
        login="TRANSP-OR", platform="infoscience",
        handle="20.500.14299/77777", uuid="ou-uuid-1",
    )
    # ExpandOpts doesn't carry crawl_members yet — we'll pass it via kwargs.
    edges = list(adapter.expand(ou, ExpandOpts(crawl_members=True)))
    has_member = [e for e in edges if e.kind == "has_member"]
    assert has_member[0].dst == "https://infoscience.epfl.ch/handle/20.500.14299/9001"
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_infoscience_adapter.py -v --no-cov
```
Expected: 8 new tests fail (some with `NotImplementedError`, some with `AttributeError: 'ExpandOpts' object has no attribute 'crawl_members'`).

- [ ] **Step 3: Add `crawl_members` to ExpandOpts**

Find `src/open_pulse_crawler/platforms/base.py`. Add to the `ExpandOpts` model:

```python
class ExpandOpts(BaseModel):
    # … existing fields …
    crawl_members: bool = False  # Spec 3 — gate has_member edges for InfoscienceOrgUnit
```

- [ ] **Step 4: Implement `expand`**

Replace the `expand` placeholder in `src/open_pulse_crawler/platforms/infoscience/adapter.py`:

```python
# DataCite RelationType vocabulary normalization. DSpace lowercases the
# qualifier (e.g. dc.relation.isversionof); we normalize to DataCite's
# canonical camelCase form so `related_to.<RelationType>` edge kinds
# match Zenodo's casing across platforms.
_RELATION_CAMELCASE = {
    "isversionof": "isVersionOf",
    "issupplementto": "isSupplementTo",
    "issupplementedby": "isSupplementedBy",
    "iscitedby": "isCitedBy",
    "ispartof": "isPartOf",
    "haspart": "hasPart",
    "isderivedfrom": "isDerivedFrom",
    "iscompiledby": "isCompiledBy",
    "isdocumentedby": "isDocumentedBy",
    "describes": "describes",
    "isdescribedby": "isDescribedBy",
    "requires": "requires",
    "isrequiredby": "isRequiredBy",
    "references": "references",
    "isreferencedby": "isReferencedBy",
    "obsoletes": "obsoletes",
    "isobsoletedby": "isObsoletedBy",
}


def _scheme_from_identifier(ident: str) -> str:
    """Best-effort scheme inference for a DSpace dc.relation.* identifier."""
    if ident.startswith(("http://", "https://")):
        return "url"
    if ident.lower().startswith("arxiv:"):
        return "arxiv"
    if ident.lower().startswith("orcid:"):
        return "orcid"
    if ident.lower().startswith("swh:"):
        return "swh"
    # DataCite DOI pattern: "10.<prefix>/<suffix>"
    if "/" in ident and ident.split("/", 1)[0].startswith("10."):
        return "doi"
    return ""


class InfoscienceAdapter(PlatformAdapter):
    # … existing __init__ / normalize_uri / classify / fetch …

    # ---- expand ------------------------------------------------------------

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        if isinstance(node, InfoscienceItem):
            yield from self._expand_item(node, opts)
        elif isinstance(node, InfosciencePerson):
            yield from self._expand_person(node, opts)
        elif isinstance(node, InfoscienceOrgUnit):
            yield from self._expand_orgunit(node, opts)

    def _person_url_from_uuid(self, uuid: str) -> str:
        """Resolve a Person UUID to its canonical handle URL when known,
        else return the UUID-form URL (the BFS will canonicalize on the
        round-trip fetch)."""
        handle = self._uuid_to_handle.get(uuid)
        if handle:
            return f"https://{self.instance_host}/handle/{handle}"
        return f"https://{self.instance_host}/server/api/core/items/{uuid}"

    def _expand_item(self, node: InfoscienceItem, opts: ExpandOpts) -> Iterable[Edge]:
        raw_meta = node.extras.get("_raw_metadata", {}) if node.extras else {}

        # authored_by
        for entry in raw_meta.get("dc.contributor.author") or []:
            if not isinstance(entry, dict):
                continue
            authority = entry.get("authority")
            if not authority:
                continue
            yield Edge(
                src=node.url,
                kind="authored_by",
                dst=self._person_url_from_uuid(authority),
            )

        # affiliated_with — from CRIS-virtual department authorities
        for entry in raw_meta.get("cris.virtual.department") or []:
            if not isinstance(entry, dict):
                continue
            authority = entry.get("authority")
            if not authority:
                continue
            yield Edge(
                src=node.url,
                kind="affiliated_with",
                dst=self._person_url_from_uuid(authority),
            )

        # related_to.<RelationType> via DataCite synthesizer
        for key, entries in (raw_meta or {}).items():
            if not key.startswith("dc.relation."):
                continue
            relation_lower = key[len("dc.relation."):].lower()
            relation = _RELATION_CAMELCASE.get(relation_lower, "references")
            for e in entries or []:
                if not isinstance(e, dict):
                    continue
                ident = e.get("value", "")
                if not ident:
                    continue
                scheme = _scheme_from_identifier(ident)
                target = synthesize_target_url(scheme, ident)
                if not target:
                    continue
                yield Edge(
                    src=node.url,
                    kind=f"related_to.{relation}",
                    dst=target,
                )

    def _expand_person(self, node: InfosciencePerson, opts: ExpandOpts) -> Iterable[Edge]:
        # authored — items where this person is an author authority
        for item in self._client.iter_person_items(node.uuid):
            handle = item.get("handle")
            if not handle:
                continue
            yield Edge(
                src=node.url,
                kind="authored",
                dst=f"https://{self.instance_host}/handle/{handle}",
            )

        # member_of — affiliation OrgUnit
        if node.affiliation_uuid:
            yield Edge(
                src=node.url,
                kind="member_of",
                dst=self._person_url_from_uuid(node.affiliation_uuid),
            )

    def _expand_orgunit(self, node: InfoscienceOrgUnit, opts: ExpandOpts) -> Iterable[Edge]:
        # has_publication
        for item in self._client.iter_orgunit_items(node.uuid):
            handle = item.get("handle")
            if not handle:
                continue
            yield Edge(
                src=node.url,
                kind="has_publication",
                dst=f"https://{self.instance_host}/handle/{handle}",
            )

        # parent_of — child OrgUnits (parent → child direction)
        children = self._client._iter_paginated(
            "/server/api/discover/search/objects",
            {"dsoType": "item",
             "query": f"dspace.entity.type:OrgUnit AND organization.parentOrganization.authority:{node.uuid}",
             "size": 100},
        )
        for child in children:
            handle = child.get("handle")
            if not handle:
                continue
            yield Edge(
                src=node.url,
                kind="parent_of",
                dst=f"https://{self.instance_host}/handle/{handle}",
            )

        # has_member — gated by opts.crawl_members
        if opts.crawl_members:
            for person in self._client.iter_orgunit_persons(node.uuid):
                handle = person.get("handle")
                if not handle:
                    continue
                yield Edge(
                    src=node.url,
                    kind="has_member",
                    dst=f"https://{self.instance_host}/handle/{handle}",
                )
```

- [ ] **Step 5: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_infoscience_adapter.py -v --no-cov
```
Expected: all 20 tests pass (12 from Tasks 5+6 + 8 new).

- [ ] **Step 6: Commit**

```
git add src/open_pulse_crawler/platforms/infoscience/adapter.py src/open_pulse_crawler/platforms/base.py tests/platforms/test_infoscience_adapter.py
git commit -m "feat(infoscience): adapter expand() — 8 edge kinds, DataCite related_to, crawl_members gate"
```

---

# Block D — CLI + API examples + explore script

## Task 8: CLI registration

**Files:**
- Modify: `src/open_pulse_crawler/cli.py`
- Modify: `tests/test_cli.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cli.py`:

```python
def test_build_registry_registers_infoscience_adapter(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    monkeypatch.delenv("CRAWLER_TOKEN__INFOSCIENCE_EPFL_CH", raising=False)
    registry, github_client, missing = _build_registry(["infoscience.epfl.ch"])
    assert "infoscience.epfl.ch" in missing  # no token → reported
    adapter = registry.adapter_for("https://infoscience.epfl.ch/handle/20.500.14299/1")
    from open_pulse_crawler.platforms.infoscience.adapter import InfoscienceAdapter
    assert isinstance(adapter, InfoscienceAdapter)


def test_build_registry_registers_infoscience_with_token(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    monkeypatch.setenv("CRAWLER_TOKEN__INFOSCIENCE_EPFL_CH", "dspace-tok-test")
    registry, github_client, missing = _build_registry(["infoscience.epfl.ch"])
    assert "infoscience.epfl.ch" not in missing
    adapter = registry.adapter_for("https://infoscience.epfl.ch/handle/20.500.14299/1")
    from open_pulse_crawler.platforms.infoscience.adapter import InfoscienceAdapter
    assert isinstance(adapter, InfoscienceAdapter)
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_cli.py::test_build_registry_registers_infoscience_adapter tests/test_cli.py::test_build_registry_registers_infoscience_with_token -v --no-cov
```
Expected: `KeyError: 'no adapter registered for host'`.

- [ ] **Step 3: Add Infoscience branches in `_build_registry`**

Read the current `_build_registry` function in `src/open_pulse_crawler/cli.py`. Find the existing Zenodo branches (both anonymous and authenticated paths). Add a parallel Infoscience branch in each.

Anonymous path — insert after the Zenodo anonymous branch:

```python
            if host == "infoscience.epfl.ch" or host.endswith(".infoscience.epfl.ch"):
                from .platforms.infoscience.client import InfoscienceClient
                from .platforms.infoscience.adapter import InfoscienceAdapter
                isc = InfoscienceClient(host=host, tokens=[])
                reg.register(InfoscienceAdapter(isc, instance_host=host))
                continue
```

Authenticated path — insert after the Zenodo authenticated branch:

```python
        elif host == "infoscience.epfl.ch" or host.endswith(".infoscience.epfl.ch"):
            from .platforms.infoscience.client import InfoscienceClient
            from .platforms.infoscience.adapter import InfoscienceAdapter
            isc = InfoscienceClient(host=host, tokens=tokens)
            reg.register(InfoscienceAdapter(isc, instance_host=host))
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_cli.py -v --no-cov
```
Expected: all existing CLI tests + 2 new pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/cli.py tests/test_cli.py
git commit -m "feat(cli): register InfoscienceAdapter for infoscience.epfl.ch"
```

---

## Task 9: OpenAPI examples for Infoscience

**Files:**
- Modify: `src/open_pulse_crawler/api/v2.py`
- Modify: `tests/test_api_v2.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_api_v2.py`:

```python
def test_v2_crawl_openapi_examples_include_infoscience():
    from fastapi.testclient import TestClient
    from open_pulse_crawler.api import app
    client = TestClient(app)
    spec = client.get("/api/v1/openapi.json").json()
    crawl_path = spec["paths"]["/api/v2/crawl"]
    body = crawl_path["post"]["requestBody"]["content"]["application/json"]
    examples = body.get("examples", {})
    assert "infoscience_publication_handle" in examples
    assert "infoscience_person_authored_chain" in examples
```

- [ ] **Step 2: Run, verify failure**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_api_v2.py::test_v2_crawl_openapi_examples_include_infoscience -v --no-cov
```
Expected: assertion failure.

- [ ] **Step 3: Add the examples**

Find `_CRAWL_V2_REQUEST_EXAMPLES` in `src/open_pulse_crawler/api/v2.py`. Append two new entries (the spec called for three; the third — `cross_platform_infoscience_github` — needs a real seed picked during implementation by probing. If you can't find a real EPFL publication with a github.com URL in `dc.relation.*`, omit that third example):

```python
    "infoscience_publication_handle": {
        "summary": "Infoscience publication via handle URL",
        "description": (
            "Single EPFL publication. Round 0 fetches the item; round 1 walks "
            "`authored_by` → InfosciencePerson nodes and `affiliated_with` → "
            "InfoscienceOrgUnit nodes via DSpace-CRIS authority fields."
        ),
        "value": {
            "seeds": ["https://infoscience.epfl.ch/handle/20.500.14299/182247"],
            "max_rounds": 2,
        },
    },
    "infoscience_person_authored_chain": {
        "summary": "EPFL researcher → all their publications",
        "description": (
            "Seed an InfosciencePerson; round 1 emits `authored` edges to every "
            "publication attributable to them via author.authority. Pair with "
            "`CRAWLER_PLATFORMS=infoscience.epfl.ch` (anonymous reads suffice)."
        ),
        "value": {
            "seeds": ["https://infoscience.epfl.ch/handle/20.500.14299/99923"],
            "max_rounds": 2,
        },
    },
```

To find a real seed for the optional third example, run a one-off probe before committing:

```bash
VIRTUAL_ENV= uv run python -c "
import httpx
r = httpx.get('https://infoscience.epfl.ch/server/api/discover/search/objects',
              params={'dsoType': 'item', 'query': 'dc.relation.uri:github.com', 'size': 5},
              timeout=15)
import json
print(json.dumps(r.json(), indent=2)[:2000])
" 2>&1 | head -30
```

If the response surfaces a candidate item with a GitHub `dc.relation.uri`, add a third example for it. Otherwise skip — don't ship a placeholder.

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_api_v2.py -v --no-cov
```
Expected: all v2 API tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/api/v2.py tests/test_api_v2.py
git commit -m "feat(api): /api/v2/crawl OpenAPI examples for Infoscience seeds"
```

---

## Task 10: Explore-script extension

**Files:**
- Modify: `tools/scripts/fetch_public_projects.py`

- [ ] **Step 1: Add Infoscience host-dispatch + helper**

In `tools/scripts/fetch_public_projects.py`, find the existing `_fetch_zenodo_urls` helper and the top-level `fetch_public_project_urls` function. Add a parallel Infoscience helper and host-dispatch branch.

Add the helper:

```python
def _fetch_infoscience_urls(host: str, limit: int) -> Iterable[str]:
    """Yield up to ``limit`` Infoscience handle URLs via DSpace's discover/search/objects."""
    import httpx

    tokens = resolve_tokens(host)
    headers = {"Accept": "application/json"}
    if tokens:
        headers["Authorization"] = f"Bearer {tokens[0]}"

    url = f"https://{host}/server/api/discover/search/objects"
    params: Optional[Dict[str, Any]] = {"dsoType": "item", "size": min(100, limit)}
    yielded = 0
    with httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=True) as session:
        while yielded < limit:
            try:
                r = session.get(url, params=params, headers=headers)
                if r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", "5"))
                    sys.stderr.write(f"  ! {host}: 429, sleeping {min(retry_after, 60):.1f}s\n")
                    time.sleep(min(retry_after, 60))
                    continue
                r.raise_for_status()
            except Exception as exc:
                sys.stderr.write(f"  ! {host}: page failed ({type(exc).__name__}: {exc}); stopping.\n")
                return
            body = r.json()
            search = body.get("_embedded", {}).get("searchResult", {})
            objects = search.get("_embedded", {}).get("objects", [])
            if not objects:
                return
            for obj in objects:
                ix = obj.get("_embedded", {}).get("indexableObject", {})
                handle = ix.get("handle")
                if not handle:
                    continue
                yield f"https://{host}/handle/{handle}"
                yielded += 1
                if yielded >= limit:
                    return
            next_link = search.get("_links", {}).get("next", {})
            next_href = next_link.get("href") if isinstance(next_link, dict) else None
            if not next_href:
                return
            url = next_href
            params = None
            time.sleep(0.5)  # be polite — Infoscience rate-limits aggressively
```

Update the host-dispatch in `fetch_public_project_urls`:

```python
def fetch_public_project_urls(host: str, limit: int) -> Iterable[str]:
    if host in ("zenodo.org", "sandbox.zenodo.org") or host.endswith(".zenodo.org"):
        yield from _fetch_zenodo_urls(host, limit)
        return
    if host == "infoscience.epfl.ch" or host.endswith(".infoscience.epfl.ch"):
        yield from _fetch_infoscience_urls(host, limit)
        return
    # … existing GitLab implementation
```

Update `_total_public_projects`:

```python
def _total_public_projects(host: str) -> Optional[int]:
    if host in ("zenodo.org", "sandbox.zenodo.org") or host.endswith(".zenodo.org"):
        return None
    if host == "infoscience.epfl.ch" or host.endswith(".infoscience.epfl.ch"):
        # DSpace exposes totalElements on the search response, but it requires
        # a real fetch. Skip the up-front total probe.
        return None
    # … existing GitLab implementation
```

Add `"infoscience.epfl.ch"` to `DEFAULT_HOSTS`:

```python
    # --- Swiss research institutional repositories ---
    "infoscience.epfl.ch",
```

- [ ] **Step 2: Smoke-test**

```
VIRTUAL_ENV= uv run python tools/scripts/fetch_public_projects.py \
    --hosts infoscience.epfl.ch --per-host-limit 3 --output-dir /tmp/iscience-probe
cat /tmp/iscience-probe/infoscience.epfl.ch.txt
```
Expected: 3 URLs of the form `https://infoscience.epfl.ch/handle/20.500.14299/<id>`.

- [ ] **Step 3: Commit**

```
git add tools/scripts/fetch_public_projects.py
git commit -m "feat(tools): fetch_public_projects supports Infoscience DSpace 7 pagination"
```

---

# Block E — Integration test + docs + verification

## Task 11: Integration test against live `infoscience.epfl.ch`

**Files:**
- Create: `tests/integration/test_infoscience_dryrun.py`

- [ ] **Step 1: Pick a real seed by probing**

The integration test needs a stable, known-public publication. Probe one:

```bash
VIRTUAL_ENV= uv run python -c "
import httpx
r = httpx.get('https://infoscience.epfl.ch/server/api/discover/search/objects',
              params={'dsoType': 'item', 'size': 1},
              timeout=15, headers={'Accept': 'application/json'})
import json
body = r.json()
ix = body['_embedded']['searchResult']['_embedded']['objects'][0]['_embedded']['indexableObject']
print(f'uuid: {ix[\"uuid\"]}')
print(f'handle: {ix[\"handle\"]}')
print(f'name: {ix.get(\"name\",\"\")[:60]!r}')
"
```

Use the returned handle in the test seed. Alternatively, hardcode `20.500.14299/1` (a stable handle prefix root — if invalid, the seed-finding probe will surface a real handle).

- [ ] **Step 2: Write the test**

```python
# tests/integration/test_infoscience_dryrun.py
"""Tiny live dryrun against infoscience.epfl.ch.

Anonymous-friendly — runs whenever ``CRAWLER_SKIP_INTEGRATION`` is unset.
Skipped when explicitly opted out (e.g., offline CI).

Hits the live Infoscience API with a 2-second delay between requests
to stay well under EPFL's rate limit.
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
def test_fetch_real_infoscience_publication() -> None:
    """Fetch one EPFL publication via its handle, anonymous mode.

    Asserts the InfoscienceItem subkind, a non-empty handle/UUID, and at
    least one author with a CRIS authority UUID. The seed handle is read
    from the env var INFOSCIENCE_TEST_HANDLE; defaults to a stable demo
    handle that the test author confirmed exists at integration time.
    """
    from open_pulse_crawler.platforms.infoscience.adapter import InfoscienceAdapter
    from open_pulse_crawler.platforms.infoscience.client import InfoscienceClient

    handle = os.environ.get("INFOSCIENCE_TEST_HANDLE", "20.500.14299/182247")
    client = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    adapter = InfoscienceAdapter(client=client, instance_host="infoscience.epfl.ch")

    time.sleep(2.0)  # stay polite
    node = adapter.fetch(f"https://infoscience.epfl.ch/handle/{handle}")

    if node is None:
        pytest.skip(
            f"Seed handle {handle} returned 404. Set INFOSCIENCE_TEST_HANDLE to "
            "a known-existing handle and re-run."
        )

    assert node.subkind in ("InfoscienceItem", "InfosciencePerson", "InfoscienceOrgUnit")
    assert node.handle == handle
    assert node.uuid  # non-empty
```

- [ ] **Step 3: Run the integration test**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/integration/test_infoscience_dryrun.py -v --no-cov
```
Expected: PASS (anonymous, live API). If the default seed returns 404, the test self-skips with a hint.

- [ ] **Step 4: Confirm opt-out**

```
VIRTUAL_ENV= CRAWLER_SKIP_INTEGRATION=1 uv run pytest -n 0 tests/integration/test_infoscience_dryrun.py -v --no-cov
```
Expected: SKIPPED.

- [ ] **Step 5: Commit**

```
git add tests/integration/test_infoscience_dryrun.py
git commit -m "test(integration): tiny infoscience.epfl.ch dryrun (anonymous, polite)"
```

---

## Task 12: Documentation + CHANGELOG

**Files:**
- Create: `docs/INFOSCIENCE.md`
- Modify: `README.md`
- Modify: `docs/index.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write `docs/INFOSCIENCE.md`**

Create the full guide (one-shot):

```markdown
# Infoscience adapter

Open Pulse Crawler v3.2+ supports **EPFL's Infoscience** (DSpace 7.6.2 +
DSpace-CRIS 2023.02.06) as part of the unified open-science graph.
Anonymous reads work without a token, with aggressive 429 retry handling
built into the client.

## Supported entities

- **`InfoscienceItem`** — publications + resources (papers, theses,
  datasets, software, presentations, …). All are DSpace `item` entities
  with the same shape; the `resource_type` field carries `dc.type` so
  downstream code can filter publications vs datasets without an
  isinstance switch.
- **`InfosciencePerson`** — DSpace-CRIS Person entities (EPFL researcher
  profiles). Carries SciPer / ORCID / Scopus IDs as embedded metadata,
  **not** as cross-platform identity anchors.
- **`InfoscienceOrgUnit`** — DSpace-CRIS OrgUnit entities (EPFL
  departments / schools / labs). Forms the canonical EPFL hierarchy.

## Edge kinds

| Source | Kind | Target |
|---|---|---|
| `InfoscienceItem` | `authored_by` | `InfosciencePerson` |
| `InfoscienceItem` | `affiliated_with` | `InfoscienceOrgUnit` |
| `InfoscienceItem` | `related_to.<RelationType>` | URL on any platform |
| `InfosciencePerson` | `authored` | `InfoscienceItem` |
| `InfosciencePerson` | `member_of` | `InfoscienceOrgUnit` |
| `InfoscienceOrgUnit` | `has_publication` | `InfoscienceItem` |
| `InfoscienceOrgUnit` | `has_member` | `InfosciencePerson` (gated by `--crawl-members`) |
| `InfoscienceOrgUnit` | `parent_of` | `InfoscienceOrgUnit` (parent → child) |

`related_to.<RelationType>` reuses the DataCite RelationType vocabulary
shared with the Zenodo adapter — the URL synthesizer in
`platforms/datacite.py` handles `arxiv`, `orcid`, `pmid`, `pmcid`, `swh`,
`doi`, and `url` schemes uniformly.

## Configuring tokens

Anonymous reads work for the public REST endpoints — no token required
for a discovery crawl. Authenticated mode raises rate limits and unlocks
some hidden metadata:

```bash
CRAWLER_PLATFORMS=infoscience.epfl.ch
CRAWLER_TOKEN__INFOSCIENCE_EPFL_CH=dspace-api-token-here
```

Tokens use the `Authorization: Bearer <token>` header. Contact EPFL IT
to provision a DSpace API token if you need authenticated access.

## Seed forms accepted

- Canonical handle URL: `https://infoscience.epfl.ch/handle/<prefix>/<id>`
  (for items, persons, and orgunits — they all share this form).
- UUID-form URL: `https://infoscience.epfl.ch/server/api/core/items/<uuid>`
  (resolved to the canonical handle on first fetch; cached).

**Not supported as seeds:** EPFL DOI URLs (`https://doi.org/10.5075/...`)
— the `10.5075` prefix covers multiple EPFL services, not just
Infoscience, so the adapter doesn't auto-rewrite them. Resolve manually
via doi.org redirect if needed.

## Manual-test recipes

### Single publication

```bash
opc crawl --platforms infoscience.epfl.ch \
    --default-host infoscience.epfl.ch --rounds 2 \
    https://infoscience.epfl.ch/handle/20.500.14299/182247
```

### Researcher → all their publications

```bash
opc crawl --platforms infoscience.epfl.ch \
    --default-host infoscience.epfl.ch --rounds 2 \
    https://infoscience.epfl.ch/handle/20.500.14299/99923
```

Round 0 fetches the Person; round 1 emits `authored` edges to every
publication attributable to them via the CRIS author.authority field.

### OrgUnit hierarchy + publications

```bash
opc crawl --platforms infoscience.epfl.ch \
    --default-host infoscience.epfl.ch --rounds 2 \
    https://infoscience.epfl.ch/handle/20.500.14299/77777
```

Round 1 emits `has_publication` edges to all items affiliated with the
department + `parent_of` edges to child OrgUnits. `has_member` is
gated behind the `--crawl-members` CLI flag (off by default).

### Cross-platform (Infoscience + GitHub)

```bash
CRAWLER_PLATFORMS=infoscience.epfl.ch,github.com \
CRAWLER_TOKEN__GITHUB_COM=ghp_… \
opc crawl --rounds 2 \
    https://infoscience.epfl.ch/handle/20.500.14299/182247
```

EPFL papers with `dc.relation.uri` / `dc.relation.isversionof` fields
pointing at GitHub repos spawn cross-platform discovery.

## Limitations (v3.2)

- **Community + Collection layers skipped.** The DSpace community/collection
  structure is redundant with OrgUnit on Infoscience (every item is in
  exactly one collection, every collection in one community, and the
  CRIS OrgUnit hierarchy already captures the canonical EPFL departmental
  structure). If you need them, file an issue.
- **DSpace-CRIS Project entities** (grants, funding) are not modeled.
  Would require a new `ProjectModel` base class — out of scope for v3.2.
- **EPFL DOI URLs are not auto-resolved.** The `10.5075` prefix covers
  multiple EPFL services; the adapter doesn't try to special-case them.
- **`has_member` is gated** behind `--crawl-members` (default off) —
  EPFL departments can have hundreds of researchers.
- **Heavy rate-limiting.** Anonymous probes routinely return 429. The
  client honors `Retry-After` with one automatic retry; second 429
  raises. Provision a token if you crawl at scale.
- **No deposit/upload/draft flows.** This is a read-only adapter.
- **ORCID / SciPer / Scopus IDs stay embedded** in metadata — no
  cross-platform identity linking. That layer is downstream of this tool.
```

- [ ] **Step 2: Update `README.md`**

Find the existing "Multi-platform support" section (Specs 1+2). Add an Infoscience bullet right after the Zenodo bullet:

```markdown
- **Infoscience** (`infoscience.epfl.ch`): EPFL's DSpace 7 + DSpace-CRIS
  repository. Publications + Resources + Researchers + Departments,
  with DataCite `related_to.<RelationType>` edges identical to Zenodo's.
  Anonymous reads supported (with built-in 429 retry-after handling).
  See [docs/INFOSCIENCE.md](docs/INFOSCIENCE.md).
```

- [ ] **Step 3: Update `docs/index.md`**

Add a pointer below the existing Zenodo link:

```markdown
- [Infoscience support](INFOSCIENCE.md) — EPFL's DSpace 7 repository
  with CRIS-backed researcher + departmental hierarchy.
```

- [ ] **Step 4: Update `CHANGELOG.md`**

Under `[Unreleased]` (will become `[3.2.0]` on release), add:

```markdown
### Added (v3.2 — Infoscience adapter)
- `InfoscienceAdapter` and `InfoscienceClient` under
  `src/open_pulse_crawler/platforms/infoscience/`. Crawls EPFL's
  Infoscience repository (DSpace 7.6.2 + DSpace-CRIS 2023.02.06).
- Three subkind models: `InfosciencePerson`, `InfoscienceOrgUnit`,
  `InfoscienceItem`. Items carry `resource_type` distinguishing
  publications from datasets/software/etc.
- Eight new edge kinds: `authored_by`, `affiliated_with`,
  `related_to.<RelationType>`, `authored`, `member_of`,
  `has_publication`, `has_member` (gated), `parent_of`.
- Shared `platforms/datacite.py` helper for `arxiv`/`orcid`/`pmid`/
  `pmcid`/`swh`/`doi`/`url` URL synthesis (lifted from
  `ZenodoAdapter._synthesize_target_url`; both adapters now share it).
- HTTP 429 retry-after handling in `InfoscienceClient` (one automatic
  retry honoring `Retry-After` header, capped at 60s; second 429 raises).
- CRIS-CamelCase relation-type normalization (`dc.relation.isversionof`
  → `related_to.isVersionOf` for cross-platform consistency with Zenodo).
- UUID → handle resolution cache on the adapter (`_uuid_to_handle`)
  saves round-trips when the same Person co-authors multiple items.
- `crawl_members` field on `ExpandOpts` (default `False`); gates
  `InfoscienceOrgUnit` → `has_member` edge emission.
- CLI registers `InfoscienceAdapter` for `infoscience.epfl.ch` and
  any `*.infoscience.epfl.ch` host with or without tokens.
- `tools/scripts/fetch_public_projects.py` handles
  `infoscience.epfl.ch` via DSpace's `/server/api/discover/search/objects`
  keyset pagination.
- `POST /api/v2/crawl` OpenAPI examples: `infoscience_publication_handle`,
  `infoscience_person_authored_chain` (plus optional
  `cross_platform_infoscience_github` if a real seed is found at impl).
- Integration test against live `infoscience.epfl.ch`
  (`tests/integration/test_infoscience_dryrun.py`).
- `docs/INFOSCIENCE.md`.

### Refactored (v3.2)
- `ZenodoAdapter._synthesize_target_url` lifted to module-level
  `synthesize_target_url` in `platforms/datacite.py`. Zenodo behavior
  unchanged; tests for the synthesizer moved to `tests/platforms/test_datacite.py`.

### Out of scope (v3.2)
- DSpace community/collection structural layer (OrgUnit covers the
  canonical EPFL hierarchy).
- DSpace-CRIS Project entities (grants, funding).
- Cross-platform identity resolution (still downstream of this tool).
- Other Swiss DSpace instances (UZH ZORA, UNIBE BORIS) — refactor
  `InfoscienceAdapter` into a parameterized `DSpaceAdapter` when a
  second instance lands.
```

- [ ] **Step 5: Commit**

```
git add docs/INFOSCIENCE.md README.md docs/index.md CHANGELOG.md
git commit -m "docs: Infoscience adapter (v3.2.0) — INFOSCIENCE.md, README, CHANGELOG"
```

---

## Task 13: Final lint + test verification

- [ ] **Step 1: Lint**

```
VIRTUAL_ENV= uv run ruff check src/
```
Expected: no NEW errors beyond the develop baseline (~22 pre-existing).

- [ ] **Step 2: Full unit suite (serial)**

```
VIRTUAL_ENV= uv run pytest -n 0 -m "not integration" --ignore=tests/test_api.py --ignore=tests/test_auth.py -q --no-cov
```
Expected: all tests pass (~340+; the exact count depends on prior tasks).

- [ ] **Step 3: Diff stat**

```
git diff --stat origin/develop...HEAD | tail -20
```
Eyeball: each new module is ~150-300 LoC, plus the datacite refactor + tests.

- [ ] **Step 4: Stop and report ready for review**

Do NOT push. Report:
- Total new/modified files.
- Total tests added.
- Pass count.
- Any deferred concerns (e.g., did the integration test pass against live Infoscience? did the cross-platform GitHub example get a real seed or was it dropped?).

---

## Self-Review

**Spec coverage:**

| Spec section | Tasks |
|---|---|
| §0 Starting point | n/a (informational) |
| §1 Architecture & module layout | 1, 3, 5, 8 |
| §2 Data model (3 subkinds) | 2 |
| §3.1 normalize_uri (handle + UUID + cache) | 5 |
| §3.2 classify | 5 |
| §3.3 fetch (entityType dispatch) | 6 |
| §3.4 expand (8 edge kinds + crawl_members gate) | 7 |
| §3.5 InfoscienceClient API | 3, 4 |
| §3.6 DSpace 7 endpoint reference | 4 |
| §4.1 Configuration (CLI) | 8 |
| §4.2 Caching + 429 retry-after | 3, 4 |
| §4.3 Testing | every task + 11 (integration) |
| §4.4 Explore-script extension | 10 |
| §4.5 OpenAPI examples | 9 |
| §4.6 Effort estimate | n/a |
| §6 Open questions | resolved during execution (defaults stated in spec) |
| §7 Deferred items | n/a (explicitly out of scope) |

Pre-flight refactor (Task 1) — lift `_synthesize_target_url` to `platforms/datacite.py` — is also covered.

**Placeholder scan:** No "TBD" / "TODO" / "implement later" / vague phrases. Each task has the actual code + commands the executor needs. The one `<TBD>` referenced in Task 9 is for the optional `cross_platform_infoscience_github` example seed — Task 9 explicitly says drop the example if no real candidate is found rather than ship a placeholder.

**Type consistency:** `InfoscienceItem` / `InfosciencePerson` / `InfoscienceOrgUnit` spelled identically across Tasks 2, 5, 6, 7, 8. `InfoscienceClient` method names (`get_item_by_handle`, `get_item_by_uuid`, `iter_person_items`, `iter_orgunit_items`, `iter_orgunit_persons`) match across Tasks 3, 4, 6, 7. Edge kinds (`authored_by`, `affiliated_with`, `related_to.<RelationType>`, `authored`, `member_of`, `has_publication`, `has_member`, `parent_of`) consistent across spec, tests, and adapter code. `synthesize_target_url` (module-level) spelled identically in Tasks 1 and 7.

**One open question carried into the executor's notebook:** Task 9's optional third OpenAPI example (`cross_platform_infoscience_github`) needs a real Infoscience handle whose item carries a `dc.relation.uri` pointing at github.com. The probe in Task 9 Step 3 surfaces a candidate if one exists; if not, drop the example rather than ship a placeholder.
