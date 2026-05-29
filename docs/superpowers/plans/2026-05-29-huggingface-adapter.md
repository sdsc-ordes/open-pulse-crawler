# HuggingFace Adapter Implementation Plan (Spec 5, targets v3.4.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `HuggingFaceAdapter` so HuggingFace (users / organizations / models / datasets / spaces / papers / collections) crawls alongside the GitHub / GitLab / Zenodo / Infoscience / DataCite adapters, with cross-platform `related_to.IsIdenticalTo` (arxiv) and `related_to.IsSupplementedBy` (github) edges from `HuggingFacePaper` nodes reusing the shared `synthesize_target_url`.

**Architecture:** Single `HuggingFaceAdapter` registered against `huggingface.co`. Plain `httpx` `HuggingFaceHTTPClient` mirroring the DataCite pattern. Five subkinds: `HuggingFaceUser`, `HuggingFaceOrg`, `HuggingFaceRepo` (models/datasets/spaces unified via `repo_type: Literal["model","dataset","space"]`), `HuggingFacePaper`, `HuggingFaceCollection`. User/Org URL forms are identical (`huggingface.co/<name>`) — `classify` returns `USER_OR_ORG` and `fetch` disambiguates by probing `/api/users/<name>/overview` first, falling through to `/api/organizations/<name>/overview` on 404. Anonymous-friendly; optional Bearer token raises rate limits.

**Tech Stack:** Python 3.10+, Pydantic v2 (discriminated unions), `httpx`, pytest, uv. No new third-party libraries.

**Spec:** `docs/superpowers/specs/2026-05-29-huggingface-adapter-design.md` (committed `ff8deac`).

**Branch / worktree:** Same branch as Specs 1+2+3+4 — `feat/multi-platform-gitlab` at `.worktrees/feat-multi-platform-gitlab/`.

**Test invocation note:** pytest with default `-n auto` hangs in this sandbox. Run with `-n 0`:
```
VIRTUAL_ENV= uv run pytest -n 0 <args>
```

---

## What Specs 1+2+3+4 already give us — skipped

- `PlatformAdapter` ABC + `PlatformRegistry` (host-keyed dispatch).
- `PlatformRegistry.register_hosts` (multi-host registration — unused here, single-host adapter).
- `subkind` discriminator + discriminated-union dict types in `models.py`.
- `NodeKind.USER`, `NodeKind.ORG`, `NodeKind.REPO`, `NodeKind.USER_OR_ORG` enum.
- `node_id.canonical_url` URL helper.
- `config.resolve_tokens(host)` host-keyed env-var resolution.
- `cli._token_host_for(host)` (returns `huggingface.co` unchanged — no API-host divergence).
- `cli._auth_required(host)` (returns False — anonymous-friendly).
- CLI `--platforms` / `--default-host` / `crawler doctor` (tri-state OK/ANONYMOUS/MISSING).
- `/api/v2` REST surface with OpenAPI examples dropdown.
- `platforms/datacite.py` `synthesize_target_url` shared helper (handles arxiv URL synthesis for papers).
- Dual-path crawler dispatch (`urlparse(uri).netloc.lower() != "github.com"` → adapter path).

---

## File map

**New files:**
- `src/open_pulse_crawler/platforms/huggingface/__init__.py`
- `src/open_pulse_crawler/platforms/huggingface/client.py` — `HuggingFaceHTTPClient`
- `src/open_pulse_crawler/platforms/huggingface/adapter.py` — `HuggingFaceAdapter`
- `tests/platforms/test_huggingface_client.py`
- `tests/platforms/test_huggingface_adapter.py`
- `tests/integration/test_huggingface_dryrun.py`
- `docs/HUGGINGFACE.md`

**Modified files:**
- `src/open_pulse_crawler/models.py` — 5 new subclasses, widen 3 discriminated unions.
- `src/open_pulse_crawler/cli.py` — `_build_registry` `huggingface.co` branch (anonymous + authenticated).
- `src/open_pulse_crawler/api/v2.py` — 3 new openapi_examples entries.
- `tests/test_models.py` — append 6 HuggingFace subkind tests.
- `tests/test_cli.py` — append `huggingface.co` registration tests.
- `tests/test_api_v2.py` — append HuggingFace examples assertion.
- `CHANGELOG.md`, `README.md`, `docs/index.md`.

---

## Task ordering

Block A (Task 1): Five HuggingFace subkinds in `models.py`.
Block B (Tasks 2–4): `HuggingFaceHTTPClient` skeleton, single-entity endpoints + 429 retry, list endpoints with cursor pagination.
Block C (Tasks 5–7): `HuggingFaceAdapter` classify+normalize_uri, fetch with 5 builders, expand with 12 edge kinds.
Block D (Tasks 8–9): CLI registration, `/api/v2` examples.
Block E (Tasks 10–12): Integration test, docs/CHANGELOG, final verification.

Every task is TDD-shaped: failing test → minimal implementation → passing test → commit. Conventional Commits per `AGENTS.md`. NEVER pass `--no-verify`, `-c commit.gpgsign=false`, or `--no-gpg-sign`.

---

# Block A — Foundations (models)

## Task 1: Five HuggingFace subkinds in `models.py`

**Files:**
- Modify: `src/open_pulse_crawler/models.py`
- Modify: `tests/test_models.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_models.py`:

```python
# --- HuggingFace subkinds (Spec 5) -------------------------------
from open_pulse_crawler.models import (
    HuggingFaceUser, HuggingFaceOrg, HuggingFaceRepo,
    HuggingFacePaper, HuggingFaceCollection,
)


def test_huggingface_user_subkind_and_fields():
    user = HuggingFaceUser(
        url="https://huggingface.co/karpathy",
        login="karpathy",
        platform="huggingface",
        username="karpathy",
        fullname="Andrej Karpathy",
        is_pro=False,
        avatar_url="https://cdn.huggingface.co/avatars/karpathy.png",
        num_models=30,
        num_datasets=5,
        num_spaces=2,
        num_papers=12,
        num_followers=80000,
        member_orgs=["nanoGPT"],
    )
    assert user.subkind == "HuggingFaceUser"
    assert user.username == "karpathy"
    assert user.num_models == 30
    assert user.followers == []  # inherited from UserModel; HF has own counter


def test_huggingface_org_subkind_and_fields():
    org = HuggingFaceOrg(
        url="https://huggingface.co/meta-llama",
        login="meta-llama",
        platform="huggingface",
        org_name="meta-llama",
        fullname="Meta Llama",
        is_verified=True,
        plan="enterprise",
        num_models=80,
        num_datasets=10,
        num_followers=5000,
    )
    assert org.subkind == "HuggingFaceOrg"
    assert org.is_verified is True
    assert org.members == []  # inherited; populated via expand if has_member ever ships


def test_huggingface_repo_model_subkind_and_fields():
    repo = HuggingFaceRepo(
        url="https://huggingface.co/meta-llama/Llama-3.2-1B",
        full_name="meta-llama/Llama-3.2-1B",
        platform="huggingface",
        repo_type="model",
        repo_id="meta-llama/Llama-3.2-1B",
        owner="meta-llama",
        repo_name="Llama-3.2-1B",
        sha="abc123",
        tags=["transformers", "llama-3", "text-generation"],
        downloads=2222053,
        likes=2412,
        license="llama3.2",
        language=["en"],
        gated=True,
        pipeline_tag="text-generation",
        library_name="transformers",
    )
    assert repo.subkind == "HuggingFaceRepo"
    assert repo.repo_type == "model"
    assert repo.pipeline_tag == "text-generation"
    # Space-only and dataset-only fields stay empty for models
    assert repo.sdk == ""
    assert repo.used_models == []
    assert repo.paperswithcode_id == ""


def test_huggingface_repo_dataset_subkind_and_fields():
    repo = HuggingFaceRepo(
        url="https://huggingface.co/datasets/openai/gsm8k",
        full_name="datasets/openai/gsm8k",
        platform="huggingface",
        repo_type="dataset",
        repo_id="openai/gsm8k",
        owner="openai",
        repo_name="gsm8k",
        paperswithcode_id="gsm8k",
    )
    assert repo.repo_type == "dataset"
    assert repo.paperswithcode_id == "gsm8k"
    assert repo.pipeline_tag == ""


def test_huggingface_repo_space_subkind_and_fields():
    repo = HuggingFaceRepo(
        url="https://huggingface.co/spaces/black-forest-labs/FLUX.1-schnell",
        full_name="spaces/black-forest-labs/FLUX.1-schnell",
        platform="huggingface",
        repo_type="space",
        repo_id="black-forest-labs/FLUX.1-schnell",
        owner="black-forest-labs",
        repo_name="FLUX.1-schnell",
        sdk="gradio",
        runtime_stage="RUNNING",
        used_models=["black-forest-labs/FLUX.1-schnell"],
        likes=5067,
    )
    assert repo.repo_type == "space"
    assert repo.sdk == "gradio"
    assert repo.runtime_stage == "RUNNING"
    assert "black-forest-labs/FLUX.1-schnell" in repo.used_models


def test_huggingface_paper_subkind_and_fields():
    paper = HuggingFacePaper(
        url="https://huggingface.co/papers/2307.09288",
        full_name="papers/2307.09288",
        platform="huggingface",
        arxiv_id="2307.09288",
        arxiv_url="https://arxiv.org/abs/2307.09288",
        title="Llama 2: Open Foundation and Fine-Tuned Chat Models",
        summary="Long-form abstract.",
        ai_summary="Short AI-generated TL;DR.",
        ai_keywords=["llm", "fine-tuning", "instruction-tuning"],
        authors=[{"name": "Hugo Touvron"}, {"name": "Louis Martin"}],
        upvotes=252,
        published_at="2023-07-18T00:00:00Z",
        github_repo="facebookresearch/llama",
        num_linked_models=8,
        num_linked_datasets=2,
        num_linked_spaces=14,
    )
    assert paper.subkind == "HuggingFacePaper"
    assert paper.arxiv_id == "2307.09288"
    assert paper.github_repo == "facebookresearch/llama"
    assert paper.num_linked_models == 8


def test_huggingface_collection_subkind_and_fields():
    coll = HuggingFaceCollection(
        url="https://huggingface.co/collections/meta-llama/metas-llama-32-language-models-and-evals-675bfd70e574a62dd0e40586",
        login="meta-llama/metas-llama-32-language-models-and-evals-675bfd70e574a62dd0e40586",
        platform="huggingface",
        slug="meta-llama/metas-llama-32-language-models-and-evals-675bfd70e574a62dd0e40586",
        owner="meta-llama",
        title="Meta's Llama 3.2 language models & evals",
        description="The Llama 3.2 release.",
        upvotes=120,
        last_updated="2024-12-01T00:00:00Z",
    )
    assert coll.subkind == "HuggingFaceCollection"
    assert coll.owner == "meta-llama"
    assert coll.title.startswith("Meta's Llama 3.2")


def test_graphdata_accepts_huggingface_subclasses_in_existing_dicts():
    from open_pulse_crawler.models import GraphData
    g = GraphData()
    g.users["https://huggingface.co/karpathy"] = HuggingFaceUser(
        url="https://huggingface.co/karpathy",
        login="karpathy", platform="huggingface",
        username="karpathy",
    )
    g.orgs["https://huggingface.co/meta-llama"] = HuggingFaceOrg(
        url="https://huggingface.co/meta-llama",
        login="meta-llama", platform="huggingface",
        org_name="meta-llama",
    )
    g.orgs["https://huggingface.co/collections/meta-llama/llama-32-...x"] = HuggingFaceCollection(
        url="https://huggingface.co/collections/meta-llama/llama-32-...x",
        login="meta-llama/llama-32-...x", platform="huggingface",
        slug="meta-llama/llama-32-...x",
        owner="meta-llama",
    )
    g.repos["https://huggingface.co/meta-llama/Llama-3.2-1B"] = HuggingFaceRepo(
        url="https://huggingface.co/meta-llama/Llama-3.2-1B",
        full_name="meta-llama/Llama-3.2-1B", platform="huggingface",
        repo_type="model",
        repo_id="meta-llama/Llama-3.2-1B",
        owner="meta-llama",
        repo_name="Llama-3.2-1B",
    )
    g.repos["https://huggingface.co/papers/2307.09288"] = HuggingFacePaper(
        url="https://huggingface.co/papers/2307.09288",
        full_name="papers/2307.09288", platform="huggingface",
        arxiv_id="2307.09288",
        arxiv_url="https://arxiv.org/abs/2307.09288",
    )
    restored = GraphData.model_validate_json(g.model_dump_json())
    assert isinstance(restored.users["https://huggingface.co/karpathy"], HuggingFaceUser)
    assert isinstance(restored.orgs["https://huggingface.co/meta-llama"], HuggingFaceOrg)
    assert isinstance(restored.orgs["https://huggingface.co/collections/meta-llama/llama-32-...x"], HuggingFaceCollection)
    assert isinstance(restored.repos["https://huggingface.co/meta-llama/Llama-3.2-1B"], HuggingFaceRepo)
    assert isinstance(restored.repos["https://huggingface.co/papers/2307.09288"], HuggingFacePaper)
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_models.py -v --no-cov
```
Expected: `ImportError: cannot import name 'HuggingFaceUser'`.

- [ ] **Step 3: Add the five subclasses to `src/open_pulse_crawler/models.py`**

Find the existing DataCite subclasses (`DataCitePerson`, `DataCiteOrganization`, `DataCiteClient`, `DataCiteWork`). Add the HuggingFace subclasses RIGHT AFTER them — BEFORE the three discriminated-union definitions:

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
    of typed fields differ. Mirrors the ``resource_type`` precedent across
    ZenodoRecord / InfoscienceItem / DataCiteWork.
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

    Authors are bare `{name}` strings without ORCID — no `authored_by` edges
    to ORCID URLs (unlike DataCite). Cross-platform identity stays downstream.
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
    """A user-curated grouping ("bucket"): the owner picks N models /
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

Then widen the three discriminated unions (find the existing definitions and append HF entries at the end of each Union list):

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

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_models.py -v --no-cov
```
Expected: all 8 new HuggingFace tests pass; all existing tests still green.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/models.py tests/test_models.py
git commit -m "feat(models): HuggingFace subkinds — User, Org, Repo, Paper, Collection"
```

---

# Block B — `HuggingFaceHTTPClient`

## Task 2: `HuggingFaceHTTPClient` skeleton + Bearer auth + token rotation

**Files:**
- Create: `src/open_pulse_crawler/platforms/huggingface/__init__.py`
- Create: `src/open_pulse_crawler/platforms/huggingface/client.py`
- Create: `tests/platforms/test_huggingface_client.py`

- [ ] **Step 1: Write failing tests**

Create `tests/platforms/test_huggingface_client.py`:

```python
"""Tests for the HuggingFace HTTP client wrapper."""
from unittest.mock import MagicMock, patch
import pytest
import httpx

from open_pulse_crawler.platforms.huggingface.client import HuggingFaceHTTPClient


def test_authenticated_sets_bearer_header():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=["hf_tok"])
    assert c._session.headers.get("Authorization") == "Bearer hf_tok"


def test_anonymous_sends_no_auth_header():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    assert "Authorization" not in c._session.headers


def test_base_url_uses_https_with_host():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    assert c.base_url == "https://huggingface.co"


def test_session_accepts_json_content_type():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    assert c._session.headers["Accept"] == "application/json"


def test_rotate_in_anonymous_mode_is_noop():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    c._rotate()
    assert "Authorization" not in c._session.headers


def test_rotate_cycles_token_pool():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=["a", "b"])
    assert c._session.headers["Authorization"] == "Bearer a"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer b"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer a"
```

- [ ] **Step 2: Run failing tests**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_client.py -v --no-cov
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the skeleton**

`src/open_pulse_crawler/platforms/huggingface/__init__.py`:

```python
"""HuggingFace platform implementation."""
from .client import HuggingFaceHTTPClient

__all__ = ["HuggingFaceHTTPClient"]
```

`src/open_pulse_crawler/platforms/huggingface/client.py`:

```python
"""Thin httpx wrapper for huggingface.co's REST API.

Mirrors the DataCite client's shape. Pagination follows the ``Link`` header
with ``cursor=<opaque>`` query parameter (same shape as GitLab keyset). 429
responses honor the ``Retry-After`` header with one automatic retry, then
raise.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

# HuggingFace's default page size is 100 (max for list endpoints).
DEFAULT_PAGE_SIZE = 100

# Cap Retry-After honored — avoid hanging on a rogue server response.
MAX_RETRY_AFTER_SECONDS = 60

# Regex for extracting the next-page URL from a Link header value:
#   <https://huggingface.co/api/models?cursor=...&limit=100>; rel="next"
_LINK_NEXT_RE = re.compile(r'<(?P<url>[^>]+)>;\s*rel="next"')


class HuggingFaceHTTPClient:
    """HTTP client for ``huggingface.co`` API endpoints.

    Authentication: ``Authorization: Bearer hf_...`` when ``tokens`` is
    non-empty. Anonymous (no header) when ``tokens=[]`` — HuggingFace's
    public reads return 200 for all entity endpoints we crawl.

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
            headers={"Accept": "application/json"},
        )
        if not self.tokens:
            logger.warning(
                "HuggingFaceHTTPClient(%s) constructed with no tokens — "
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
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_client.py -v --no-cov
```
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/huggingface/__init__.py src/open_pulse_crawler/platforms/huggingface/client.py tests/platforms/test_huggingface_client.py
git commit -m "feat(huggingface): HuggingFaceHTTPClient skeleton (Bearer auth + token rotation)"
```

---

## Task 3: Single-entity endpoints + 429 retry

**Files:**
- Modify: `src/open_pulse_crawler/platforms/huggingface/client.py`
- Modify: `tests/platforms/test_huggingface_client.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_huggingface_client.py`:

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


def test_get_model_200_returns_dict():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"id": "meta-llama/Llama-3.2-1B", "author": "meta-llama", "downloads": 1000}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_model("meta-llama/Llama-3.2-1B")
    g.assert_called_once_with("/api/models/meta-llama/Llama-3.2-1B")
    assert result == payload


def test_get_model_404_returns_none():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_model("missing/repo") is None


def test_get_dataset_200_returns_dict():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"id": "openai/gsm8k", "downloads": 100}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_dataset("openai/gsm8k")
    g.assert_called_once_with("/api/datasets/openai/gsm8k")
    assert result == payload


def test_get_space_200_returns_dict():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"id": "black-forest-labs/FLUX.1-schnell", "sdk": "gradio"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_space("black-forest-labs/FLUX.1-schnell")
    g.assert_called_once_with("/api/spaces/black-forest-labs/FLUX.1-schnell")
    assert result == payload


def test_get_paper_200_takes_first_when_list_returned():
    """HuggingFace's /api/papers/<arxiv-id> sometimes returns a list of dicts
    (one per submission); take the first."""
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = [{"id": "2307.09288", "title": "Llama 2"}]
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_paper("2307.09288")
    g.assert_called_once_with("/api/papers/2307.09288")
    assert result == {"id": "2307.09288", "title": "Llama 2"}


def test_get_paper_200_dict_passes_through():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"id": "2307.09288", "title": "Llama 2"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)):
        result = c.get_paper("2307.09288")
    assert result == payload


def test_get_collection_200_returns_dict():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"slug": "meta-llama/llama-32-...x", "title": "Llama 3.2 evals"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_collection("meta-llama/llama-32-...x")
    g.assert_called_once_with("/api/collections/meta-llama/llama-32-...x")
    assert result == payload


def test_get_user_overview_200():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"user": "karpathy", "type": "user", "fullname": "Andrej Karpathy"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_user_overview("karpathy")
    g.assert_called_once_with("/api/users/karpathy/overview")
    assert result == payload


def test_get_user_overview_404():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_user_overview("nobody") is None


def test_get_org_overview_200():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"name": "meta-llama", "fullname": "Meta Llama"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_org_overview("meta-llama")
    g.assert_called_once_with("/api/organizations/meta-llama/overview")
    assert result == payload


def test_429_retries_after_retry_after_header(monkeypatch):
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    responses = [
        _make_response(429, headers={"Retry-After": "1"}),
        _make_response(200, {"id": "x"}),
    ]
    sleeps = []
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.huggingface.client.time.sleep",
        lambda s: sleeps.append(s),
    )
    with patch.object(c._session, "get", side_effect=responses):
        result = c.get_model("x")
    assert result == {"id": "x"}
    assert sleeps == [1.0]


def test_429_caps_retry_after_at_60s(monkeypatch):
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    sleeps = []
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.huggingface.client.time.sleep",
        lambda s: sleeps.append(s),
    )
    responses = [_make_response(429, headers={"Retry-After": "9999"}),
                 _make_response(200, {"id": "x"})]
    with patch.object(c._session, "get", side_effect=responses):
        c.get_model("x")
    assert sleeps == [60.0]


def test_429_twice_raises(monkeypatch):
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.huggingface.client.time.sleep",
        lambda s: None,
    )
    responses = [_make_response(429, headers={"Retry-After": "0"}),
                 _make_response(429)]
    with patch.object(c._session, "get", side_effect=responses):
        with pytest.raises(httpx.HTTPStatusError):
            c.get_model("x")
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_client.py -v --no-cov
```
Expected: `AttributeError: 'HuggingFaceHTTPClient' object has no attribute 'get_model'`.

- [ ] **Step 3: Append endpoints + 429 retry to `client.py`**

Append after the `_rotate` method (inside the same `HuggingFaceHTTPClient` class):

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

    def _request_json(
        self, path: str, params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """GET ``path`` and return parsed JSON body.

        404 → ``None``. Any other non-2xx raises ``httpx.HTTPStatusError``.
        """
        resp = self._do_get(path, params)
        if resp.status_code == 404:
            return None
        if not resp.is_success:
            resp.raise_for_status()
        return resp.json()

    def get_model(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """Return the model JSON for the given ``<owner>/<name>``, or None on 404."""
        return self._request_json(f"/api/models/{repo_id}")

    def get_dataset(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """Return the dataset JSON for the given ``<owner>/<name>``, or None on 404."""
        return self._request_json(f"/api/datasets/{repo_id}")

    def get_space(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """Return the space JSON for the given ``<owner>/<name>``, or None on 404."""
        return self._request_json(f"/api/spaces/{repo_id}")

    def get_paper(self, arxiv_id: str) -> Optional[Dict[str, Any]]:
        """Return the paper JSON for the given arxiv ID, or None on 404.

        HuggingFace's /api/papers/<arxiv-id> sometimes returns a list of
        submissions (one per HF re-post); take the first dict.
        """
        data = self._request_json(f"/api/papers/{arxiv_id}")
        if isinstance(data, list):
            return data[0] if data else None
        return data

    def get_collection(self, slug: str) -> Optional[Dict[str, Any]]:
        """Return the collection JSON for ``<owner>/<slug-with-id>``, or None on 404."""
        return self._request_json(f"/api/collections/{slug}")

    def get_user_overview(self, username: str) -> Optional[Dict[str, Any]]:
        """Return the user overview JSON for ``<username>``, or None on 404."""
        return self._request_json(f"/api/users/{username}/overview")

    def get_org_overview(self, org_name: str) -> Optional[Dict[str, Any]]:
        """Return the org overview JSON for ``<org_name>``, or None on 404."""
        return self._request_json(f"/api/organizations/{org_name}/overview")
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_client.py -v --no-cov
```
Expected: 19 tests pass (6 from Task 2 + 13 new).

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/huggingface/client.py tests/platforms/test_huggingface_client.py
git commit -m "feat(huggingface): client single-entity endpoints + 429 retry-after"
```

---

## Task 4: List endpoints with cursor pagination

**Files:**
- Modify: `src/open_pulse_crawler/platforms/huggingface/client.py`
- Modify: `tests/platforms/test_huggingface_client.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_huggingface_client.py`:

```python
def test_iter_models_by_author_single_page():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    body = [
        {"id": "karpathy/tinyllamas", "downloads": 0},
        {"id": "karpathy/gpt2", "downloads": 1000},
    ]
    headers = {}  # no Link header → single page
    with patch.object(c._session, "get", return_value=_make_response(200, body, headers=headers)) as g:
        items = list(c.iter_models_by_author("karpathy"))
    g.assert_called_once_with(
        "/api/models",
        params={"author": "karpathy", "limit": 100},
    )
    assert [m["id"] for m in items] == ["karpathy/tinyllamas", "karpathy/gpt2"]


def test_iter_models_by_author_follows_link_next():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    page1 = [{"id": "karpathy/a"}]
    page2 = [{"id": "karpathy/b"}]
    headers1 = {"Link": '<https://huggingface.co/api/models?author=karpathy&cursor=ABC&limit=100>; rel="next"'}
    headers2 = {}
    with patch.object(c._session, "get") as g:
        g.side_effect = [
            _make_response(200, page1, headers=headers1),
            _make_response(200, page2, headers=headers2),
        ]
        items = list(c.iter_models_by_author("karpathy"))
    assert [m["id"] for m in items] == ["karpathy/a", "karpathy/b"]
    assert g.call_count == 2


def test_iter_datasets_by_author_query_shape():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(200, [], headers={})) as g:
        list(c.iter_datasets_by_author("openai"))
    g.assert_called_once_with(
        "/api/datasets",
        params={"author": "openai", "limit": 100},
    )


def test_iter_spaces_by_author_query_shape():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(200, [], headers={})) as g:
        list(c.iter_spaces_by_author("black-forest-labs"))
    g.assert_called_once_with(
        "/api/spaces",
        params={"author": "black-forest-labs", "limit": 100},
    )


def test_iter_link_header_no_next_terminates_cleanly():
    """A Link header with only `rel="first"` / `rel="prev"` (no `rel="next"`)
    must NOT trigger another request — the iteration terminates."""
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    headers = {"Link": '<https://huggingface.co/api/models?author=karpathy>; rel="first"'}
    with patch.object(c._session, "get", return_value=_make_response(200, [], headers=headers)) as g:
        list(c.iter_models_by_author("karpathy"))
    assert g.call_count == 1
```

- [ ] **Step 2: Run, verify failures**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_client.py -v --no-cov
```
Expected: `AttributeError: 'HuggingFaceHTTPClient' object has no attribute 'iter_models_by_author'`.

- [ ] **Step 3: Append list endpoints + Link-header pagination**

Append to `src/open_pulse_crawler/platforms/huggingface/client.py`:

```python
    # ---- list endpoints with Link-header cursor pagination -----------------

    def _iter_with_link(
        self, path: str, params: Dict[str, Any],
    ) -> Iterable[Dict[str, Any]]:
        """Yield items across all pages, following the ``Link: rel="next"`` header.

        HuggingFace's list endpoints (``/api/models``, ``/api/datasets``,
        ``/api/spaces``) return a JSON array. Pagination is signaled via the
        ``Link`` response header; the next-page URL is absolute and has the
        opaque ``cursor`` query parameter baked in.
        """
        current_path: str = path
        current_params: Optional[Dict[str, Any]] = params
        while True:
            resp = self._do_get(current_path, current_params)
            if not resp.is_success:
                resp.raise_for_status()
            body = resp.json()
            if isinstance(body, list):
                for item in body:
                    yield item
            link = resp.headers.get("Link") or ""
            m = _LINK_NEXT_RE.search(link)
            if not m:
                return
            current_path = m.group("url")
            current_params = None  # next-URL has all params baked in

    def iter_models_by_author(self, author: str) -> Iterable[Dict[str, Any]]:
        """Yield model records authored by ``<author>`` (user or org name)."""
        return self._iter_with_link(
            "/api/models",
            {"author": author, "limit": DEFAULT_PAGE_SIZE},
        )

    def iter_datasets_by_author(self, author: str) -> Iterable[Dict[str, Any]]:
        """Yield dataset records authored by ``<author>`` (user or org name)."""
        return self._iter_with_link(
            "/api/datasets",
            {"author": author, "limit": DEFAULT_PAGE_SIZE},
        )

    def iter_spaces_by_author(self, author: str) -> Iterable[Dict[str, Any]]:
        """Yield space records authored by ``<author>`` (user or org name)."""
        return self._iter_with_link(
            "/api/spaces",
            {"author": author, "limit": DEFAULT_PAGE_SIZE},
        )
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_client.py -v --no-cov
```
Expected: 24 tests pass (19 from Task 3 + 5 new).

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/huggingface/client.py tests/platforms/test_huggingface_client.py
git commit -m "feat(huggingface): client list endpoints + Link-header cursor pagination"
```

---

# Block C — `HuggingFaceAdapter`

## Task 5: Adapter classify + normalize_uri

**Files:**
- Create: `src/open_pulse_crawler/platforms/huggingface/adapter.py`
- Create: `tests/platforms/test_huggingface_adapter.py`
- Modify: `src/open_pulse_crawler/platforms/huggingface/__init__.py` (add re-export)

- [ ] **Step 1: Write failing tests**

Create `tests/platforms/test_huggingface_adapter.py`:

```python
"""Tests for the HuggingFace PlatformAdapter."""
from unittest.mock import MagicMock
import pytest

from open_pulse_crawler.models import (
    HuggingFaceUser, HuggingFaceOrg, HuggingFaceRepo,
    HuggingFacePaper, HuggingFaceCollection,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.huggingface.adapter import HuggingFaceAdapter


@pytest.fixture
def adapter():
    client = MagicMock()
    return HuggingFaceAdapter(client=client, instance_host="huggingface.co")


# --- classify -------------------------------------------------------

def test_classify_model_url(adapter):
    assert adapter.classify("https://huggingface.co/meta-llama/Llama-3.2-1B") == NodeKind.REPO


def test_classify_dataset_url(adapter):
    assert adapter.classify("https://huggingface.co/datasets/openai/gsm8k") == NodeKind.REPO


def test_classify_space_url(adapter):
    assert adapter.classify("https://huggingface.co/spaces/black-forest-labs/FLUX.1-schnell") == NodeKind.REPO


def test_classify_paper_url(adapter):
    assert adapter.classify("https://huggingface.co/papers/2307.09288") == NodeKind.REPO


def test_classify_collection_url(adapter):
    assert adapter.classify(
        "https://huggingface.co/collections/meta-llama/llama-32-language-models-and-evals-675bfd70e574a62dd0e40586"
    ) == NodeKind.ORG


def test_classify_user_or_org_url(adapter):
    assert adapter.classify("https://huggingface.co/karpathy") == NodeKind.USER_OR_ORG


def test_classify_reserved_index_pages_return_none(adapter):
    """The bare reserved prefixes are index pages, not entities."""
    assert adapter.classify("https://huggingface.co/datasets") is None
    assert adapter.classify("https://huggingface.co/spaces") is None
    assert adapter.classify("https://huggingface.co/papers") is None
    assert adapter.classify("https://huggingface.co/collections") is None


def test_classify_unknown_path_returns_none(adapter):
    assert adapter.classify("https://huggingface.co/blog/some-post") is None


def test_classify_legacy_arxiv_id_returns_none(adapter):
    """Legacy arxiv IDs (cond-mat/0303517 form) are not supported."""
    assert adapter.classify("https://huggingface.co/papers/cond-mat/0303517") is None


# --- normalize_uri --------------------------------------------------

def test_normalize_strips_trailing_slash(adapter):
    assert adapter.normalize_uri("https://huggingface.co/meta-llama/Llama-3.2-1B/") == \
        "https://huggingface.co/meta-llama/Llama-3.2-1B"


def test_normalize_lowercases_host(adapter):
    assert adapter.normalize_uri("https://HUGGINGFACE.CO/meta-llama/Llama-3.2-1B") == \
        "https://huggingface.co/meta-llama/Llama-3.2-1B"


def test_normalize_strips_query_and_fragment(adapter):
    assert adapter.normalize_uri("https://huggingface.co/karpathy?tab=models#header") == \
        "https://huggingface.co/karpathy"


def test_normalize_paper_with_version_suffix_preserved(adapter):
    assert adapter.normalize_uri("https://huggingface.co/papers/2307.09288v2") == \
        "https://huggingface.co/papers/2307.09288v2"


def test_normalize_dataset_url(adapter):
    assert adapter.normalize_uri("https://huggingface.co/datasets/openai/gsm8k") == \
        "https://huggingface.co/datasets/openai/gsm8k"


def test_normalize_collection_url(adapter):
    coll_url = "https://huggingface.co/collections/meta-llama/llama-32-x-675bfd70"
    assert adapter.normalize_uri(coll_url) == coll_url


def test_normalize_empty_raises(adapter):
    with pytest.raises(ValueError):
        adapter.normalize_uri("")
```

- [ ] **Step 2: Run failing tests**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_adapter.py -v --no-cov
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create adapter.py + update __init__.py**

`src/open_pulse_crawler/platforms/huggingface/adapter.py`:

```python
"""HuggingFace PlatformAdapter — classify / fetch / expand / normalize_uri.

See ``docs/superpowers/specs/2026-05-29-huggingface-adapter-design.md``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from ...models import (
    HuggingFaceUser, HuggingFaceOrg, HuggingFaceRepo,
    HuggingFacePaper, HuggingFaceCollection,
)
from ...node_id import NodeKind, canonical_url
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo
from ..datacite import synthesize_target_url
from .client import HuggingFaceHTTPClient

logger = logging.getLogger(__name__)

# Reserved first-segment words that aren't usernames/org names.
_RESERVED_FIRST_SEGMENTS = {"datasets", "spaces", "papers", "collections"}

# Path patterns (match against the URL path with leading slash stripped + trailing slash stripped).
_PAPER_PATH      = re.compile(r"^papers/(?P<arxiv>\d{4}\.\d+(?:v\d+)?)$")
_DATASET_PATH    = re.compile(r"^datasets/(?P<owner>[^/]+)/(?P<name>[^/]+)$")
_SPACE_PATH      = re.compile(r"^spaces/(?P<owner>[^/]+)/(?P<name>[^/]+)$")
_COLLECTION_PATH = re.compile(r"^collections/(?P<owner>[^/]+)/(?P<slug>[^/]+)$")
_MODEL_PATH      = re.compile(r"^(?P<owner>[^/]+)/(?P<name>[^/]+)$")
_USER_OR_ORG_PATH = re.compile(r"^(?P<name>[^/]+)$")


class HuggingFaceAdapter(PlatformAdapter):
    platform: ClassVar[str] = "huggingface"

    def __init__(self, client: HuggingFaceHTTPClient, instance_host: str) -> None:
        self._client = client
        self.instance_host = instance_host

    # ---- normalize_uri -----------------------------------------------------

    def normalize_uri(self, raw: str) -> str:
        """Return the canonical HuggingFace URL for ``raw``.

        Lowercases the host, strips query/fragment, collapses trailing
        slashes. Does NOT validate that the path matches a known entity
        shape — that's classify's job.
        """
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"empty or non-string seed: {raw!r}")

        parts = urlsplit(raw)
        host = (parts.netloc or self.instance_host).lower()
        path = (parts.path or "/").rstrip("/")
        if not path:
            path = "/"
        return canonical_url(host, path)

    # ---- classify ----------------------------------------------------------

    def classify(self, uri: str) -> Optional[NodeKind]:
        """Return the NodeKind for ``uri``, based on URL path shape.

        Path-prefix order matters: reserved prefixes (datasets, spaces,
        papers, collections) are checked before the bare-`<owner>/<name>`
        model pattern. A bare single segment that isn't reserved is
        USER_OR_ORG (disambiguated by fetch).
        """
        normed = self.normalize_uri(uri)
        parts = urlsplit(normed)
        host = parts.netloc.lower()
        if host != self.instance_host.lower():
            return None
        path_inner = (parts.path or "/").lstrip("/").rstrip("/")
        if not path_inner:
            return None

        # Reserved prefixes first.
        m = _PAPER_PATH.match(path_inner)
        if m:
            return NodeKind.REPO
        m = _DATASET_PATH.match(path_inner)
        if m:
            return NodeKind.REPO
        m = _SPACE_PATH.match(path_inner)
        if m:
            return NodeKind.REPO
        m = _COLLECTION_PATH.match(path_inner)
        if m:
            return NodeKind.ORG

        # Bare-`owner/name` model: first segment must not be reserved.
        m = _MODEL_PATH.match(path_inner)
        if m and m.group("owner") not in _RESERVED_FIRST_SEGMENTS:
            return NodeKind.REPO

        # Single non-reserved segment → user or org.
        m = _USER_OR_ORG_PATH.match(path_inner)
        if m and m.group("name") not in _RESERVED_FIRST_SEGMENTS:
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
        # HuggingFace doesn't reliably surface rate-limit headers;
        # conservative placeholders matching the Infoscience/DataCite pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
```

Update `__init__.py`:

```python
"""HuggingFace platform implementation."""
from .client import HuggingFaceHTTPClient
from .adapter import HuggingFaceAdapter

__all__ = ["HuggingFaceHTTPClient", "HuggingFaceAdapter"]
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_adapter.py -v --no-cov
```
Expected: 16 tests pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/huggingface/adapter.py src/open_pulse_crawler/platforms/huggingface/__init__.py tests/platforms/test_huggingface_adapter.py
git commit -m "feat(huggingface): HuggingFaceAdapter classify + normalize_uri (reserved-word disambiguation)"
```

---

## Task 6: Adapter fetch + 5 builders (with USER_OR_ORG fallthrough)

**Files:**
- Modify: `src/open_pulse_crawler/platforms/huggingface/adapter.py`
- Modify: `tests/platforms/test_huggingface_adapter.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_huggingface_adapter.py`:

```python
def test_fetch_user_returns_huggingface_user(adapter):
    adapter._client.get_user_overview.return_value = {
        "user": "karpathy",
        "type": "user",
        "fullname": "Andrej Karpathy",
        "isPro": False,
        "avatarUrl": "https://cdn.hf.co/karpathy.png",
        "numModels": 30,
        "numDatasets": 5,
        "numSpaces": 2,
        "numPapers": 12,
        "numFollowers": 80000,
        "orgs": [{"name": "nanoGPT"}],
    }
    node = adapter.fetch("https://huggingface.co/karpathy")
    assert isinstance(node, HuggingFaceUser)
    assert node.username == "karpathy"
    assert node.fullname == "Andrej Karpathy"
    assert node.num_models == 30
    assert node.member_orgs == ["nanoGPT"]
    # Should NOT call org endpoint when user lookup succeeded
    adapter._client.get_org_overview.assert_not_called()


def test_fetch_org_falls_through_when_user_404(adapter):
    """If /api/users/<x>/overview returns None, fall through to /api/organizations."""
    adapter._client.get_user_overview.return_value = None
    adapter._client.get_org_overview.return_value = {
        "name": "meta-llama",
        "fullname": "Meta Llama",
        "isVerified": True,
        "plan": "enterprise",
        "numModels": 80,
        "numFollowers": 5000,
    }
    node = adapter.fetch("https://huggingface.co/meta-llama")
    assert isinstance(node, HuggingFaceOrg)
    assert node.org_name == "meta-llama"
    assert node.is_verified is True
    adapter._client.get_user_overview.assert_called_once_with("meta-llama")
    adapter._client.get_org_overview.assert_called_once_with("meta-llama")


def test_fetch_user_or_org_both_404_returns_none(adapter):
    adapter._client.get_user_overview.return_value = None
    adapter._client.get_org_overview.return_value = None
    assert adapter.fetch("https://huggingface.co/nobody-anywhere") is None


def test_fetch_model_returns_repo(adapter):
    adapter._client.get_model.return_value = {
        "id": "meta-llama/Llama-3.2-1B",
        "author": "meta-llama",
        "sha": "abc123",
        "tags": ["transformers", "llama-3"],
        "downloads": 2222053,
        "likes": 2412,
        "gated": True,
        "pipeline_tag": "text-generation",
        "library_name": "transformers",
        "cardData": {"license": "llama3.2", "language": ["en"]},
    }
    node = adapter.fetch("https://huggingface.co/meta-llama/Llama-3.2-1B")
    assert isinstance(node, HuggingFaceRepo)
    assert node.repo_type == "model"
    assert node.repo_id == "meta-llama/Llama-3.2-1B"
    assert node.owner == "meta-llama"
    assert node.repo_name == "Llama-3.2-1B"
    assert node.pipeline_tag == "text-generation"
    assert node.library_name == "transformers"
    assert node.downloads == 2222053
    assert node.license == "llama3.2"
    assert node.language == ["en"]
    assert node.gated is True


def test_fetch_dataset_returns_repo(adapter):
    adapter._client.get_dataset.return_value = {
        "id": "openai/gsm8k",
        "author": "openai",
        "downloads": 5000,
        "likes": 100,
        "paperswithcode_id": "gsm8k",
        "cardData": {"license": "mit"},
    }
    node = adapter.fetch("https://huggingface.co/datasets/openai/gsm8k")
    assert isinstance(node, HuggingFaceRepo)
    assert node.repo_type == "dataset"
    assert node.paperswithcode_id == "gsm8k"


def test_fetch_space_returns_repo_with_runtime(adapter):
    adapter._client.get_space.return_value = {
        "id": "black-forest-labs/FLUX.1-schnell",
        "author": "black-forest-labs",
        "likes": 5067,
        "sdk": "gradio",
        "runtime": {"stage": "RUNNING"},
        "models": ["black-forest-labs/FLUX.1-schnell"],
    }
    node = adapter.fetch("https://huggingface.co/spaces/black-forest-labs/FLUX.1-schnell")
    assert isinstance(node, HuggingFaceRepo)
    assert node.repo_type == "space"
    assert node.sdk == "gradio"
    assert node.runtime_stage == "RUNNING"
    assert node.used_models == ["black-forest-labs/FLUX.1-schnell"]


def test_fetch_paper_returns_huggingface_paper(adapter):
    adapter._client.get_paper.return_value = {
        "id": "2307.09288",
        "title": "Llama 2: Open Foundation and Fine-Tuned Chat Models",
        "summary": "We develop Llama 2…",
        "ai_summary": "Llama 2 is open-weight.",
        "ai_keywords": ["llm", "fine-tuning"],
        "authors": [{"name": "Hugo Touvron"}, {"name": "Louis Martin"}],
        "upvotes": 252,
        "publishedAt": "2023-07-18T00:00:00Z",
        "githubRepo": "facebookresearch/llama",
        "linkedModels": [{"id": "meta-llama/Llama-2-7b"}, {"id": "meta-llama/Llama-2-13b"}],
        "linkedDatasets": [{"id": "some/dataset"}],
        "linkedSpaces": [],
        "numTotalModels": 8,
        "numTotalDatasets": 2,
        "numTotalSpaces": 14,
    }
    node = adapter.fetch("https://huggingface.co/papers/2307.09288")
    assert isinstance(node, HuggingFacePaper)
    assert node.arxiv_id == "2307.09288"
    assert node.arxiv_url == "https://arxiv.org/abs/2307.09288"
    assert node.title.startswith("Llama 2")
    assert node.github_repo == "facebookresearch/llama"
    assert node.num_linked_models == 8
    assert len(node.authors) == 2


def test_fetch_paper_404_returns_none(adapter):
    adapter._client.get_paper.return_value = None
    assert adapter.fetch("https://huggingface.co/papers/9999.99999") is None


def test_fetch_collection_returns_collection(adapter):
    adapter._client.get_collection.return_value = {
        "slug": "meta-llama/llama-32-x-675bfd70",
        "owner": {"name": "meta-llama"},
        "title": "Llama 3.2 evals",
        "description": "Release bundle.",
        "upvotes": 120,
        "lastUpdated": "2024-12-01T00:00:00Z",
    }
    node = adapter.fetch(
        "https://huggingface.co/collections/meta-llama/llama-32-x-675bfd70"
    )
    assert isinstance(node, HuggingFaceCollection)
    assert node.slug == "meta-llama/llama-32-x-675bfd70"
    assert node.owner == "meta-llama"
    assert node.title == "Llama 3.2 evals"


def test_fetch_unknown_url_form_returns_none(adapter):
    assert adapter.fetch("https://huggingface.co/blog/some-post") is None
```

- [ ] **Step 2: Run failing tests**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_adapter.py -v --no-cov
```
Expected: 10 new tests fail with `NotImplementedError`.

- [ ] **Step 3: Implement fetch + five builders**

In `src/open_pulse_crawler/platforms/huggingface/adapter.py`, replace the `fetch` placeholder and add five builders. Append after the `classify` method:

```python
    # ---- fetch -------------------------------------------------------------

    def fetch(self, uri: str):
        uri = self.normalize_uri(uri)
        parts = urlsplit(uri)
        path_inner = (parts.path or "/").lstrip("/").rstrip("/")
        if not path_inner:
            return None

        m = _PAPER_PATH.match(path_inner)
        if m:
            arxiv_id = m.group("arxiv")
            raw = self._client.get_paper(arxiv_id)
            if raw is None:
                return None
            return self._build_paper(uri, arxiv_id, raw)

        m = _DATASET_PATH.match(path_inner)
        if m:
            repo_id = f"{m.group('owner')}/{m.group('name')}"
            raw = self._client.get_dataset(repo_id)
            if raw is None:
                return None
            return self._build_repo(uri, raw, repo_type="dataset")

        m = _SPACE_PATH.match(path_inner)
        if m:
            repo_id = f"{m.group('owner')}/{m.group('name')}"
            raw = self._client.get_space(repo_id)
            if raw is None:
                return None
            return self._build_repo(uri, raw, repo_type="space")

        m = _COLLECTION_PATH.match(path_inner)
        if m:
            slug = f"{m.group('owner')}/{m.group('slug')}"
            raw = self._client.get_collection(slug)
            if raw is None:
                return None
            return self._build_collection(uri, raw)

        m = _MODEL_PATH.match(path_inner)
        if m and m.group("owner") not in _RESERVED_FIRST_SEGMENTS:
            repo_id = f"{m.group('owner')}/{m.group('name')}"
            raw = self._client.get_model(repo_id)
            if raw is None:
                return None
            return self._build_repo(uri, raw, repo_type="model")

        m = _USER_OR_ORG_PATH.match(path_inner)
        if m and m.group("name") not in _RESERVED_FIRST_SEGMENTS:
            name = m.group("name")
            # Try user first, fall through to org on 404.
            raw_user = self._client.get_user_overview(name)
            if raw_user is not None:
                return self._build_user(uri, name, raw_user)
            raw_org = self._client.get_org_overview(name)
            if raw_org is not None:
                return self._build_org(uri, name, raw_org)
            return None

        return None

    # ---- builders ----------------------------------------------------------

    def _build_user(self, uri: str, username: str, raw: Dict[str, Any]) -> HuggingFaceUser:
        orgs_raw = raw.get("orgs") or []
        member_orgs = []
        for o in orgs_raw:
            if isinstance(o, dict) and o.get("name"):
                member_orgs.append(o["name"])
            elif isinstance(o, str):
                member_orgs.append(o)
        return HuggingFaceUser(
            url=uri,
            login=username,
            platform="huggingface",
            name=raw.get("fullname", "") or "",
            username=username,
            fullname=raw.get("fullname", "") or "",
            is_pro=bool(raw.get("isPro", False)),
            avatar_url=raw.get("avatarUrl", "") or "",
            num_models=int(raw.get("numModels") or 0),
            num_datasets=int(raw.get("numDatasets") or 0),
            num_spaces=int(raw.get("numSpaces") or 0),
            num_papers=int(raw.get("numPapers") or 0),
            num_followers=int(raw.get("numFollowers") or 0),
            member_orgs=member_orgs,
        )

    def _build_org(self, uri: str, org_name: str, raw: Dict[str, Any]) -> HuggingFaceOrg:
        return HuggingFaceOrg(
            url=uri,
            login=org_name,
            platform="huggingface",
            name=raw.get("fullname", "") or "",
            org_name=org_name,
            fullname=raw.get("fullname", "") or "",
            is_verified=bool(raw.get("isVerified", False)),
            plan=raw.get("plan", "") or "",
            avatar_url=raw.get("avatarUrl", "") or "",
            num_models=int(raw.get("numModels") or 0),
            num_datasets=int(raw.get("numDatasets") or 0),
            num_spaces=int(raw.get("numSpaces") or 0),
            num_papers=int(raw.get("numPapers") or 0),
            num_users=int(raw.get("numUsers") or 0),
            num_followers=int(raw.get("numFollowers") or 0),
        )

    def _build_repo(
        self, uri: str, raw: Dict[str, Any], *, repo_type: str,
    ) -> HuggingFaceRepo:
        repo_id = raw.get("id", "") or ""
        owner = raw.get("author", "") or ""
        # repo_id is "<owner>/<name>"; split off the name half.
        if "/" in repo_id:
            _, _, repo_name = repo_id.partition("/")
        else:
            repo_name = repo_id

        cd = raw.get("cardData") or {}
        license_val = cd.get("license", "") or ""
        lang_val = cd.get("language") or []
        if isinstance(lang_val, str):
            language = [lang_val]
        elif isinstance(lang_val, list):
            language = [str(l) for l in lang_val if l]
        else:
            language = []

        # Space-specific fields
        sdk = ""
        runtime_stage = ""
        used_models: List[str] = []
        if repo_type == "space":
            sdk = raw.get("sdk", "") or ""
            runtime = raw.get("runtime") or {}
            if isinstance(runtime, dict):
                runtime_stage = runtime.get("stage", "") or ""
            models_field = raw.get("models") or []
            if isinstance(models_field, list):
                used_models = [str(m) for m in models_field if isinstance(m, str) and m]

        # Dataset-specific
        paperswithcode_id = ""
        if repo_type == "dataset":
            paperswithcode_id = raw.get("paperswithcode_id", "") or ""

        return HuggingFaceRepo(
            url=uri,
            full_name=repo_id,
            platform="huggingface",
            name=repo_name,
            repo_type=repo_type,  # type: ignore[arg-type]
            repo_id=repo_id,
            owner=owner,
            repo_name=repo_name,
            sha=raw.get("sha", "") or "",
            tags=[t for t in (raw.get("tags") or []) if isinstance(t, str)],
            downloads=int(raw.get("downloads") or 0),
            likes=int(raw.get("likes") or 0),
            license=license_val,
            language=language,
            gated=bool(raw.get("gated", False)),
            pipeline_tag=raw.get("pipeline_tag", "") or "" if repo_type == "model" else "",
            library_name=raw.get("library_name", "") or "" if repo_type == "model" else "",
            sdk=sdk,
            runtime_stage=runtime_stage,
            used_models=used_models,
            paperswithcode_id=paperswithcode_id,
        )

    def _build_paper(
        self, uri: str, arxiv_id: str, raw: Dict[str, Any],
    ) -> HuggingFacePaper:
        # Strip version suffix from arxiv_id for the canonical arxiv URL
        # (so v2 / v3 / etc. all point at the same arxiv abstract page).
        arxiv_base = re.sub(r"v\d+$", "", arxiv_id)
        authors_raw = raw.get("authors") or []
        authors = [a for a in authors_raw if isinstance(a, dict)]
        return HuggingFacePaper(
            url=uri,
            full_name=f"papers/{arxiv_id}",
            platform="huggingface",
            name=raw.get("title", "") or "",
            arxiv_id=arxiv_id,
            arxiv_url=f"https://arxiv.org/abs/{arxiv_base}",
            title=raw.get("title", "") or "",
            summary=raw.get("summary", "") or "",
            ai_summary=raw.get("ai_summary", "") or "",
            ai_keywords=[k for k in (raw.get("ai_keywords") or []) if isinstance(k, str)],
            authors=authors,
            upvotes=int(raw.get("upvotes") or 0),
            published_at=raw.get("publishedAt", "") or "",
            github_repo=raw.get("githubRepo", "") or "",
            num_linked_models=int(raw.get("numTotalModels") or 0),
            num_linked_datasets=int(raw.get("numTotalDatasets") or 0),
            num_linked_spaces=int(raw.get("numTotalSpaces") or 0),
        )

    def _build_collection(
        self, uri: str, raw: Dict[str, Any],
    ) -> HuggingFaceCollection:
        owner_raw = raw.get("owner") or {}
        if isinstance(owner_raw, dict):
            owner_name = owner_raw.get("name", "") or ""
        elif isinstance(owner_raw, str):
            owner_name = owner_raw
        else:
            owner_name = ""
        slug = raw.get("slug", "") or ""
        return HuggingFaceCollection(
            url=uri,
            login=slug,
            platform="huggingface",
            name=raw.get("title", "") or "",
            slug=slug,
            owner=owner_name,
            title=raw.get("title", "") or "",
            description=raw.get("description", "") or "",
            upvotes=int(raw.get("upvotes") or 0),
            last_updated=raw.get("lastUpdated", "") or "",
        )
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_adapter.py -v --no-cov
```
Expected: 26 tests pass (16 from Task 5 + 10 new).

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/huggingface/adapter.py tests/platforms/test_huggingface_adapter.py
git commit -m "feat(huggingface): adapter fetch + 5 builders (USER_OR_ORG fallthrough)"
```

---

## Task 7: Adapter expand — 12 edge kinds

**Files:**
- Modify: `src/open_pulse_crawler/platforms/huggingface/adapter.py`
- Modify: `tests/platforms/test_huggingface_adapter.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/platforms/test_huggingface_adapter.py`:

```python
from open_pulse_crawler.platforms.base import ExpandOpts, Edge


# --- expand: HuggingFaceUser ----------------------------------------

def test_expand_user_emits_owns_and_member_of(adapter):
    adapter._client.iter_models_by_author.return_value = iter([
        {"id": "karpathy/tinyllamas"}, {"id": "karpathy/gpt2"},
    ])
    adapter._client.iter_datasets_by_author.return_value = iter([
        {"id": "karpathy/lecun-mnist"},
    ])
    adapter._client.iter_spaces_by_author.return_value = iter([])
    user = HuggingFaceUser(
        url="https://huggingface.co/karpathy",
        login="karpathy", platform="huggingface",
        username="karpathy",
        member_orgs=["nanoGPT", "deeplearningorg"],
    )
    edges = list(adapter.expand(user, ExpandOpts()))
    owns_dsts = sorted(e.dst for e in edges if e.kind == "owns")
    member_dsts = sorted(e.dst for e in edges if e.kind == "member_of")
    assert owns_dsts == [
        "https://huggingface.co/datasets/karpathy/lecun-mnist",
        "https://huggingface.co/karpathy/gpt2",
        "https://huggingface.co/karpathy/tinyllamas",
    ]
    assert member_dsts == [
        "https://huggingface.co/deeplearningorg",
        "https://huggingface.co/nanoGPT",
    ]


# --- expand: HuggingFaceOrg -----------------------------------------

def test_expand_org_emits_owns(adapter):
    adapter._client.iter_models_by_author.return_value = iter([
        {"id": "meta-llama/Llama-3.2-1B"},
    ])
    adapter._client.iter_datasets_by_author.return_value = iter([])
    adapter._client.iter_spaces_by_author.return_value = iter([
        {"id": "meta-llama/space-demo"},
    ])
    org = HuggingFaceOrg(
        url="https://huggingface.co/meta-llama",
        login="meta-llama", platform="huggingface",
        org_name="meta-llama",
    )
    edges = [e for e in adapter.expand(org, ExpandOpts()) if e.kind == "owns"]
    assert sorted(e.dst for e in edges) == [
        "https://huggingface.co/meta-llama/Llama-3.2-1B",
        "https://huggingface.co/spaces/meta-llama/space-demo",
    ]


# --- expand: HuggingFaceRepo ----------------------------------------

def test_expand_repo_emits_owned_by(adapter):
    repo = HuggingFaceRepo(
        url="https://huggingface.co/meta-llama/Llama-3.2-1B",
        full_name="meta-llama/Llama-3.2-1B", platform="huggingface",
        repo_type="model",
        repo_id="meta-llama/Llama-3.2-1B",
        owner="meta-llama",
        repo_name="Llama-3.2-1B",
    )
    edges = [e for e in adapter.expand(repo, ExpandOpts()) if e.kind == "owned_by"]
    assert edges == [Edge(
        src=repo.url, kind="owned_by",
        dst="https://huggingface.co/meta-llama",
    )]


def test_expand_repo_space_emits_uses_model(adapter):
    """Spaces emit uses_model edges to each entry in `used_models`."""
    space = HuggingFaceRepo(
        url="https://huggingface.co/spaces/foo/bar",
        full_name="spaces/foo/bar", platform="huggingface",
        repo_type="space",
        repo_id="foo/bar",
        owner="foo",
        repo_name="bar",
        used_models=["meta-llama/Llama-3.2-1B", "openai/whisper-base"],
    )
    edges = [e for e in adapter.expand(space, ExpandOpts()) if e.kind == "uses_model"]
    assert sorted(e.dst for e in edges) == [
        "https://huggingface.co/meta-llama/Llama-3.2-1B",
        "https://huggingface.co/openai/whisper-base",
    ]


def test_expand_repo_model_emits_no_uses_model(adapter):
    """Only Spaces emit uses_model — Models with empty `used_models` emit no edges."""
    model = HuggingFaceRepo(
        url="https://huggingface.co/meta-llama/Llama-3.2-1B",
        full_name="meta-llama/Llama-3.2-1B", platform="huggingface",
        repo_type="model",
        repo_id="meta-llama/Llama-3.2-1B",
        owner="meta-llama",
        repo_name="Llama-3.2-1B",
    )
    edges = [e for e in adapter.expand(model, ExpandOpts()) if e.kind == "uses_model"]
    assert edges == []


# --- expand: HuggingFacePaper ---------------------------------------

def test_expand_paper_emits_arxiv_and_github_and_linked_repos(adapter):
    adapter._client.get_paper.return_value = {
        "id": "2307.09288",
        "linkedModels": [
            {"id": "meta-llama/Llama-2-7b"},
            {"id": "meta-llama/Llama-2-13b"},
        ],
        "linkedDatasets": [{"id": "some/dataset"}],
        "linkedSpaces": [{"id": "demo/llama-chat"}],
    }
    paper = HuggingFacePaper(
        url="https://huggingface.co/papers/2307.09288",
        full_name="papers/2307.09288", platform="huggingface",
        arxiv_id="2307.09288",
        arxiv_url="https://arxiv.org/abs/2307.09288",
        github_repo="facebookresearch/llama",
    )
    edges = list(adapter.expand(paper, ExpandOpts()))
    by_kind: Dict[str, List[str]] = {}
    for e in edges:
        by_kind.setdefault(e.kind, []).append(e.dst)
    assert by_kind["related_to.IsIdenticalTo"] == [
        "https://arxiv.org/abs/2307.09288",
    ]
    assert by_kind["related_to.IsSupplementedBy"] == [
        "https://github.com/facebookresearch/llama",
    ]
    assert sorted(by_kind["references_model"]) == [
        "https://huggingface.co/meta-llama/Llama-2-13b",
        "https://huggingface.co/meta-llama/Llama-2-7b",
    ]
    assert by_kind["references_dataset"] == [
        "https://huggingface.co/datasets/some/dataset",
    ]
    assert by_kind["references_space"] == [
        "https://huggingface.co/spaces/demo/llama-chat",
    ]


def test_expand_paper_skips_github_when_field_empty(adapter):
    adapter._client.get_paper.return_value = {"id": "2307.09288"}
    paper = HuggingFacePaper(
        url="https://huggingface.co/papers/2307.09288",
        full_name="papers/2307.09288", platform="huggingface",
        arxiv_id="2307.09288",
        arxiv_url="https://arxiv.org/abs/2307.09288",
        github_repo="",
    )
    edges = [e for e in adapter.expand(paper, ExpandOpts())
             if e.kind == "related_to.IsSupplementedBy"]
    assert edges == []


# --- expand: HuggingFaceCollection ----------------------------------

def test_expand_collection_emits_owned_by_and_contains(adapter):
    adapter._client.get_collection.return_value = {
        "slug": "meta-llama/llama-32-x-675bfd70",
        "owner": {"name": "meta-llama"},
        "items": [
            {"type": "model", "id": "meta-llama/Llama-3.2-1B"},
            {"type": "dataset", "id": "openai/gsm8k"},
            {"type": "space", "id": "demo/llama-chat"},
            {"type": "paper", "id": "2307.09288"},
        ],
    }
    coll = HuggingFaceCollection(
        url="https://huggingface.co/collections/meta-llama/llama-32-x-675bfd70",
        login="meta-llama/llama-32-x-675bfd70", platform="huggingface",
        slug="meta-llama/llama-32-x-675bfd70",
        owner="meta-llama",
    )
    edges = list(adapter.expand(coll, ExpandOpts()))
    by_kind: Dict[str, List[str]] = {}
    for e in edges:
        by_kind.setdefault(e.kind, []).append(e.dst)
    assert by_kind["owned_by"] == ["https://huggingface.co/meta-llama"]
    assert sorted(by_kind["contains"]) == [
        "https://huggingface.co/datasets/openai/gsm8k",
        "https://huggingface.co/meta-llama/Llama-3.2-1B",
        "https://huggingface.co/papers/2307.09288",
        "https://huggingface.co/spaces/demo/llama-chat",
    ]


def test_expand_collection_skips_items_with_unknown_type(adapter):
    """Items with an unrecognized `type` field should be skipped, not crash."""
    adapter._client.get_collection.return_value = {
        "slug": "x/y-1",
        "owner": {"name": "x"},
        "items": [
            {"type": "model", "id": "meta-llama/Llama-3.2-1B"},
            {"type": "unknown-future-type", "id": "x/y"},
        ],
    }
    coll = HuggingFaceCollection(
        url="https://huggingface.co/collections/x/y-1",
        login="x/y-1", platform="huggingface",
        slug="x/y-1", owner="x",
    )
    edges = [e for e in adapter.expand(coll, ExpandOpts()) if e.kind == "contains"]
    assert len(edges) == 1
    assert edges[0].dst == "https://huggingface.co/meta-llama/Llama-3.2-1B"
```

- [ ] **Step 2: Run failing tests**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_adapter.py -v --no-cov
```
Expected: 9 new tests fail with `NotImplementedError`.

- [ ] **Step 3: Implement `expand` + helpers**

In `src/open_pulse_crawler/platforms/huggingface/adapter.py`, replace the `expand` placeholder body. Append after the builders:

```python
    # ---- expand ------------------------------------------------------------

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        if isinstance(node, HuggingFaceUser):
            yield from self._expand_user(node, opts)
        elif isinstance(node, HuggingFaceOrg):
            yield from self._expand_org(node, opts)
        elif isinstance(node, HuggingFaceRepo):
            yield from self._expand_repo(node, opts)
        elif isinstance(node, HuggingFacePaper):
            yield from self._expand_paper(node, opts)
        elif isinstance(node, HuggingFaceCollection):
            yield from self._expand_collection(node, opts)

    def _repo_url(self, repo_id: str, repo_type: str) -> str:
        """Build the canonical HF URL for a repo of the given type.

        Model:   huggingface.co/<owner>/<name>
        Dataset: huggingface.co/datasets/<owner>/<name>
        Space:   huggingface.co/spaces/<owner>/<name>
        """
        if repo_type == "model":
            return f"https://{self.instance_host}/{repo_id}"
        return f"https://{self.instance_host}/{repo_type}s/{repo_id}"

    def _expand_user(self, node: HuggingFaceUser, opts: ExpandOpts) -> Iterable[Edge]:
        # owns — models / datasets / spaces by this user
        for item in self._client.iter_models_by_author(node.username):
            rid = item.get("id") if isinstance(item, dict) else None
            if rid:
                yield Edge(src=node.url, kind="owns", dst=self._repo_url(rid, "model"))
        for item in self._client.iter_datasets_by_author(node.username):
            rid = item.get("id") if isinstance(item, dict) else None
            if rid:
                yield Edge(src=node.url, kind="owns", dst=self._repo_url(rid, "dataset"))
        for item in self._client.iter_spaces_by_author(node.username):
            rid = item.get("id") if isinstance(item, dict) else None
            if rid:
                yield Edge(src=node.url, kind="owns", dst=self._repo_url(rid, "space"))
        # member_of — each org name
        for org_name in node.member_orgs or []:
            if not org_name:
                continue
            yield Edge(
                src=node.url,
                kind="member_of",
                dst=f"https://{self.instance_host}/{org_name}",
            )

    def _expand_org(self, node: HuggingFaceOrg, opts: ExpandOpts) -> Iterable[Edge]:
        for item in self._client.iter_models_by_author(node.org_name):
            rid = item.get("id") if isinstance(item, dict) else None
            if rid:
                yield Edge(src=node.url, kind="owns", dst=self._repo_url(rid, "model"))
        for item in self._client.iter_datasets_by_author(node.org_name):
            rid = item.get("id") if isinstance(item, dict) else None
            if rid:
                yield Edge(src=node.url, kind="owns", dst=self._repo_url(rid, "dataset"))
        for item in self._client.iter_spaces_by_author(node.org_name):
            rid = item.get("id") if isinstance(item, dict) else None
            if rid:
                yield Edge(src=node.url, kind="owns", dst=self._repo_url(rid, "space"))

    def _expand_repo(self, node: HuggingFaceRepo, opts: ExpandOpts) -> Iterable[Edge]:
        # owned_by — let the BFS resolve user-vs-org via classify+fetch.
        if node.owner:
            yield Edge(
                src=node.url,
                kind="owned_by",
                dst=f"https://{self.instance_host}/{node.owner}",
            )
        # uses_model — Spaces only.
        if node.repo_type == "space":
            for mid in node.used_models or []:
                if not mid:
                    continue
                yield Edge(
                    src=node.url,
                    kind="uses_model",
                    dst=self._repo_url(mid, "model"),
                )

    def _expand_paper(self, node: HuggingFacePaper, opts: ExpandOpts) -> Iterable[Edge]:
        # related_to.IsIdenticalTo — arxiv URL via shared synthesizer
        target = synthesize_target_url("arxiv", node.arxiv_id)
        if target:
            yield Edge(
                src=node.url,
                kind="related_to.IsIdenticalTo",
                dst=target,
            )
        # related_to.IsSupplementedBy — github repo URL (when populated)
        if node.github_repo:
            yield Edge(
                src=node.url,
                kind="related_to.IsSupplementedBy",
                dst=f"https://github.com/{node.github_repo}",
            )
        # references_model / dataset / space — re-fetch the paper to get
        # the linkedX[] lists (the stored node doesn't carry them).
        raw = self._client.get_paper(node.arxiv_id)
        if raw is None:
            return
        for m in raw.get("linkedModels") or []:
            rid = m.get("id") if isinstance(m, dict) else None
            if rid:
                yield Edge(src=node.url, kind="references_model",
                           dst=self._repo_url(rid, "model"))
        for d in raw.get("linkedDatasets") or []:
            rid = d.get("id") if isinstance(d, dict) else None
            if rid:
                yield Edge(src=node.url, kind="references_dataset",
                           dst=self._repo_url(rid, "dataset"))
        for s in raw.get("linkedSpaces") or []:
            rid = s.get("id") if isinstance(s, dict) else None
            if rid:
                yield Edge(src=node.url, kind="references_space",
                           dst=self._repo_url(rid, "space"))

    def _expand_collection(
        self, node: HuggingFaceCollection, opts: ExpandOpts,
    ) -> Iterable[Edge]:
        # owned_by — let BFS resolve user vs org
        if node.owner:
            yield Edge(
                src=node.url,
                kind="owned_by",
                dst=f"https://{self.instance_host}/{node.owner}",
            )
        # contains — re-fetch to get items[]
        raw = self._client.get_collection(node.slug)
        if raw is None:
            return
        for item in raw.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            item_id = item.get("id", "")
            if not item_id:
                continue
            if item_type == "model":
                dst = self._repo_url(item_id, "model")
            elif item_type == "dataset":
                dst = self._repo_url(item_id, "dataset")
            elif item_type == "space":
                dst = self._repo_url(item_id, "space")
            elif item_type == "paper":
                dst = f"https://{self.instance_host}/papers/{item_id}"
            else:
                logger.warning(
                    "Skipping collection item with unknown type %r in %s",
                    item_type, node.url,
                )
                continue
            yield Edge(src=node.url, kind="contains", dst=dst)
```

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/platforms/test_huggingface_adapter.py -v --no-cov
```
Expected: 35 tests pass (26 from Tasks 5+6 + 9 new).

Also confirm the full suite remains green:

```
VIRTUAL_ENV= uv run pytest -n 0 -m "not integration" --ignore=tests/test_api.py --ignore=tests/test_auth.py --no-cov 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/platforms/huggingface/adapter.py tests/platforms/test_huggingface_adapter.py
git commit -m "feat(huggingface): adapter expand — 12 edge kinds (cross-platform paper bridge)"
```

---

# Block D — CLI + API

## Task 8: CLI registration for `huggingface.co`

**Files:**
- Modify: `src/open_pulse_crawler/cli.py`
- Modify: `tests/test_cli.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cli.py`:

```python
def test_build_registry_registers_huggingface_anonymous(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    from open_pulse_crawler.platforms.huggingface.adapter import HuggingFaceAdapter
    monkeypatch.delenv("CRAWLER_TOKEN__HUGGINGFACE_CO", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__HUGGINGFACE_CO", raising=False)
    registry, _gh, missing = _build_registry(["huggingface.co"])
    assert "huggingface.co" in missing
    a = registry.adapter_for("https://huggingface.co/karpathy")
    assert isinstance(a, HuggingFaceAdapter)


def test_build_registry_registers_huggingface_with_token(monkeypatch):
    from open_pulse_crawler.cli import _build_registry
    from open_pulse_crawler.platforms.huggingface.adapter import HuggingFaceAdapter
    monkeypatch.setenv("CRAWLER_TOKEN__HUGGINGFACE_CO", "hf_test")
    registry, _gh, missing = _build_registry(["huggingface.co"])
    assert "huggingface.co" not in missing
    a = registry.adapter_for("https://huggingface.co/karpathy")
    assert isinstance(a, HuggingFaceAdapter)


def test_doctor_huggingface_without_token_reports_anonymous(monkeypatch):
    """HuggingFace is anonymous-friendly; doctor must show ANONYMOUS, not MISSING."""
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "huggingface.co")
    monkeypatch.delenv("CRAWLER_TOKEN__HUGGINGFACE_CO", raising=False)
    r = runner.invoke(app, ["doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    row = next((p for p in payload if p["host"] == "huggingface.co"), None)
    assert row is not None
    assert row["tokens"] == 0
    assert row["auth_required"] is False
    assert row["ok"] is True
```

- [ ] **Step 2: Run failing tests**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_cli.py::test_build_registry_registers_huggingface_anonymous tests/test_cli.py::test_build_registry_registers_huggingface_with_token tests/test_cli.py::test_doctor_huggingface_without_token_reports_anonymous -v --no-cov
```
Expected: `KeyError: 'no adapter registered for host'`.

- [ ] **Step 3: Add the `huggingface.co` branches in `_build_registry`**

Find the existing DataCite branches in `_build_registry`. Add parallel HuggingFace branches in each path.

Anonymous path — insert after the DataCite anonymous branch (still inside the `if not tokens:` block):

```python
            if host == "huggingface.co":
                # Anonymous HuggingFace: public reads work for all entity
                # endpoints. ``missing`` still surfaces the gap via doctor.
                from .platforms.huggingface.client import HuggingFaceHTTPClient
                from .platforms.huggingface.adapter import HuggingFaceAdapter
                hf = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
                reg.register(HuggingFaceAdapter(client=hf, instance_host="huggingface.co"))
                continue
```

Authenticated path — parallel branch after the DataCite authenticated branch:

```python
        elif host == "huggingface.co":
            from .platforms.huggingface.client import HuggingFaceHTTPClient
            from .platforms.huggingface.adapter import HuggingFaceAdapter
            hf = HuggingFaceHTTPClient(host="huggingface.co", tokens=tokens)
            reg.register(HuggingFaceAdapter(client=hf, instance_host="huggingface.co"))
```

No `register_hosts` is needed — HuggingFace owns a single host.

The existing `_token_host_for("huggingface.co")` returns "huggingface.co" unchanged (it only diverges for "datacite.org"), and `_auth_required("huggingface.co")` returns False (matches Zenodo/Infoscience/DataCite), so doctor's tri-state reports ANONYMOUS correctly without further changes.

- [ ] **Step 4: Run, verify pass**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_cli.py -v --no-cov
```
Expected: all existing CLI tests + 3 new pass.

- [ ] **Step 5: Commit**

```
git add src/open_pulse_crawler/cli.py tests/test_cli.py
git commit -m "feat(cli): register HuggingFaceAdapter for huggingface.co"
```

---

## Task 9: OpenAPI examples for HuggingFace

**Files:**
- Modify: `src/open_pulse_crawler/api/v2.py`
- Modify: `tests/test_api_v2.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_api_v2.py`:

```python
def test_v2_crawl_openapi_examples_include_huggingface():
    from fastapi.testclient import TestClient
    from open_pulse_crawler.api import app
    client = TestClient(app)
    spec = client.get("/api/v1/openapi.json").json()
    crawl_path = spec["paths"]["/api/v2/crawl"]
    body = crawl_path["post"]["requestBody"]["content"]["application/json"]
    examples = body.get("examples", {})
    assert "huggingface_paper_llama2" in examples
    assert "huggingface_model_llama" in examples
    assert "huggingface_user_karpathy" in examples
```

- [ ] **Step 2: Run failing test**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/test_api_v2.py::test_v2_crawl_openapi_examples_include_huggingface -v --no-cov
```
Expected: assertion failure.

- [ ] **Step 3: Add three examples**

Find `_CRAWL_V2_REQUEST_EXAMPLES` in `src/open_pulse_crawler/api/v2.py`. Append at the end of the existing dict:

```python
    "huggingface_paper_llama2": {
        "summary": "Cross-platform paper bridge (HF paper → arxiv + github + linked repos)",
        "description": (
            "Seed an HF paper. Round 1 emits cross-platform "
            "`related_to.IsIdenticalTo` (arxiv.org URL), "
            "`related_to.IsSupplementedBy` (github.com URL), plus typed "
            "`references_model` / `references_dataset` / `references_space` "
            "edges into the HF graph. Pair with `huggingface.co,github.com` "
            "to follow the GitHub edge into the source repository."
        ),
        "value": {
            "seeds": ["https://huggingface.co/papers/2307.09288"],
            "max_rounds": 2,
        },
    },
    "huggingface_model_llama": {
        "summary": "Single HF model (with owner fan-out in round 2)",
        "description": (
            "Seed a model. Round 1 emits `owned_by` → the owner "
            "(user or org — fetch disambiguates). Round 2 walks the "
            "owner's `owns` edges to every model / dataset / space they "
            "publish on HuggingFace."
        ),
        "value": {
            "seeds": ["https://huggingface.co/meta-llama/Llama-3.2-1B"],
            "max_rounds": 2,
        },
    },
    "huggingface_user_karpathy": {
        "summary": "Seed a HuggingFace user → walk their models/datasets/spaces + orgs",
        "description": (
            "Round 0 fetches the User (`/api/users/<name>/overview`). "
            "Round 1 walks `owns` edges to every model / dataset / space "
            "authored by them, plus `member_of` to each org they belong to."
        ),
        "value": {
            "seeds": ["https://huggingface.co/karpathy"],
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
git commit -m "feat(api): /api/v2/crawl OpenAPI examples for HuggingFace seeds"
```

---

# Block E — Integration test + docs + verification

## Task 10: Integration test against live `huggingface.co`

**Files:**
- Create: `tests/integration/test_huggingface_dryrun.py`

- [ ] **Step 1: Write the test**

Create `tests/integration/test_huggingface_dryrun.py`:

```python
"""Tiny live dryrun against huggingface.co.

Anonymous-friendly — runs whenever ``CRAWLER_SKIP_INTEGRATION`` is unset.
Skipped when explicitly opted out (e.g., offline CI).

Hits the live HuggingFace API with a 2-second delay between requests to
stay well under the public rate limit. Verifies the cross-platform
paper bridge end-to-end: arxiv URL + github URL + at least one linked HF repo.
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
def test_fetch_real_huggingface_paper_llama2() -> None:
    """Walk the Llama 2 paper to its arxiv / github / linked HF repos.

    The Llama 2 paper (arxiv:2307.09288) is a stable seed:
      - Has githubRepo populated (facebookresearch/llama)
      - Has multiple linkedModels (meta-llama/Llama-2-7b, …)
      - Public anonymous read.
    """
    from open_pulse_crawler.platforms.huggingface.adapter import HuggingFaceAdapter
    from open_pulse_crawler.platforms.huggingface.client import HuggingFaceHTTPClient
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import HuggingFacePaper

    client = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    adapter = HuggingFaceAdapter(client=client, instance_host="huggingface.co")

    seed = "https://huggingface.co/papers/2307.09288"

    # 1. Fetch the paper.
    paper = adapter.fetch(seed)
    if paper is None:
        pytest.skip("Llama 2 paper returned 404 — DNS/network issue or HF API change.")
    assert isinstance(paper, HuggingFacePaper)
    assert paper.arxiv_id == "2307.09288"
    assert paper.arxiv_url == "https://arxiv.org/abs/2307.09288"

    # 2. Walk one round of edges.
    time.sleep(2.0)
    edges = list(adapter.expand(paper, ExpandOpts()))

    by_kind = {}
    for e in edges:
        by_kind.setdefault(e.kind, []).append(e.dst)

    # arxiv edge MUST be present (synthesize_target_url is deterministic).
    assert "related_to.IsIdenticalTo" in by_kind
    assert by_kind["related_to.IsIdenticalTo"] == ["https://arxiv.org/abs/2307.09288"]

    # github_repo MAY be empty on a future API revision; fall through if so.
    if paper.github_repo:
        assert "related_to.IsSupplementedBy" in by_kind
        assert by_kind["related_to.IsSupplementedBy"][0].startswith("https://github.com/")

    # At least one linked HF repo edge should exist (the paper has 8+ linkedModels at writing time).
    repo_kinds = ("references_model", "references_dataset", "references_space")
    total_linked = sum(len(by_kind.get(k, [])) for k in repo_kinds)
    if total_linked == 0:
        pytest.skip(
            "Llama 2 paper unexpectedly returned 0 linkedModels/Datasets/Spaces — "
            "HF API may have changed shape; investigate."
        )
    assert total_linked >= 1
```

- [ ] **Step 2: Run the integration test (live)**

```
VIRTUAL_ENV= uv run pytest -n 0 tests/integration/test_huggingface_dryrun.py -v --no-cov
```
Expected: PASS. The test self-adapts if any single field is empty.

- [ ] **Step 3: Confirm opt-out**

```
VIRTUAL_ENV= CRAWLER_SKIP_INTEGRATION=1 uv run pytest -n 0 tests/integration/test_huggingface_dryrun.py -v --no-cov
```
Expected: SKIPPED.

- [ ] **Step 4: Commit**

```
git add tests/integration/test_huggingface_dryrun.py
git commit -m "test(integration): tiny huggingface.co dryrun via Llama 2 paper seed (anonymous, polite)"
```

---

## Task 11: Documentation + CHANGELOG

**Files:**
- Create: `docs/HUGGINGFACE.md`
- Modify: `README.md`
- Modify: `docs/index.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Create `docs/HUGGINGFACE.md`**

Create the file with this exact content:

```markdown
# HuggingFace adapter

Open Pulse Crawler v3.4+ supports **HuggingFace** —
users, organizations, models, datasets, spaces, papers, and collections.
Anonymous reads work without a token.

## Supported entities

- **`HuggingFaceUser`** — a user account. Distinguished from a
  `HuggingFaceOrg` only by which API endpoint returned 200
  (`/users/<x>/overview` vs `/organizations/<x>/overview`) — URL form is
  identical (`huggingface.co/<name>`).
- **`HuggingFaceOrg`** — an organization (e.g. `meta-llama`, `openai`,
  `BigScience`).
- **`HuggingFaceRepo`** — a model, dataset, or space. Discriminated by
  the `repo_type` field (`"model" | "dataset" | "space"`). All three
  share git-repo plumbing (owner/name, sha, files, tags, downloads,
  likes, cardData); only the URL path prefix and a handful of typed
  fields differ.
- **`HuggingFacePaper`** — a paper on HuggingFace, keyed by arxiv ID
  (`huggingface.co/papers/2307.09288`). HF aggregates arxiv metadata
  plus HF-specific cross-references (`linkedModels` / `linkedDatasets` /
  `linkedSpaces` / `githubRepo`) that make papers the strongest
  cross-platform pivot in the graph.
- **`HuggingFaceCollection`** — a user-curated grouping ("bucket"): the
  owner picks N models / datasets / spaces / papers and gives the
  bundle a title + description.

## Edge kinds

| Source | Kind | Target |
|---|---|---|
| `HuggingFaceUser` | `owns` | `HuggingFaceRepo` (model/dataset/space) |
| `HuggingFaceUser` | `member_of` | `HuggingFaceOrg` |
| `HuggingFaceOrg` | `owns` | `HuggingFaceRepo` (model/dataset/space) |
| `HuggingFaceRepo` | `owned_by` | `HuggingFaceUser` OR `HuggingFaceOrg` |
| `HuggingFaceRepo` *(spaces only)* | `uses_model` | `HuggingFaceRepo` (model) |
| `HuggingFacePaper` | `related_to.IsIdenticalTo` | URL on any platform (arxiv.org) |
| `HuggingFacePaper` | `related_to.IsSupplementedBy` | URL on any platform (github.com) |
| `HuggingFacePaper` | `references_model` | `HuggingFaceRepo` (model) |
| `HuggingFacePaper` | `references_dataset` | `HuggingFaceRepo` (dataset) |
| `HuggingFacePaper` | `references_space` | `HuggingFaceRepo` (space) |
| `HuggingFaceCollection` | `owned_by` | `HuggingFaceUser` OR `HuggingFaceOrg` |
| `HuggingFaceCollection` | `contains` | `HuggingFaceRepo` OR `HuggingFacePaper` |

`related_to.IsIdenticalTo` and `related_to.IsSupplementedBy` reuse the
shared DataCite RelationType vocabulary: the URL synthesizer in
`platforms/datacite.py` handles the `arxiv` scheme for paper bridges,
producing the same canonical `https://arxiv.org/abs/<id>` URL that
Zenodo / Infoscience / DataCite records produce when they reference the
same arxiv ID. **This is what makes HF papers the cross-platform pivot.**

## Configuring tokens

Anonymous reads work for all entity endpoints. Tokens raise rate limits
and unlock gated content (gated models, private spaces if you have
access):

```bash
CRAWLER_PLATFORMS=huggingface.co
CRAWLER_TOKEN__HUGGINGFACE_CO=hf_<token>
# Or rotation pool:
CRAWLER_TOKEN_POOL__HUGGINGFACE_CO=hf_a,hf_b
```

Tokens are provisioned at <https://huggingface.co/settings/tokens>.
Read-only tokens suffice for the crawl-only workload.

## Seed forms accepted

- `https://huggingface.co/<owner>/<name>` — model
- `https://huggingface.co/datasets/<owner>/<name>` — dataset
- `https://huggingface.co/spaces/<owner>/<name>` — space
- `https://huggingface.co/papers/<arxiv-id>` — paper (modern arxiv IDs only)
- `https://huggingface.co/collections/<owner>/<slug>` — collection
- `https://huggingface.co/<username>` — user or org (disambiguated at fetch time)

**Not supported as seeds:**
- Legacy arxiv IDs (`papers/cond-mat/0303517` form) — HuggingFace only
  indexes modern arxiv IDs (`YYMM.NNNNN`).
- Static HF pages (`/blog`, `/learn`, `/pricing`, …).
- UI filter URLs (`/models?author=...`).

## Manual-test recipes

### Cross-platform paper bridge

```bash
opc crawl --platforms huggingface.co,github.com --rounds 2 \
    https://huggingface.co/papers/2307.09288
```

Round 0 fetches the Llama 2 paper. Round 1 emits:
- `related_to.IsIdenticalTo` → `https://arxiv.org/abs/2307.09288`
- `related_to.IsSupplementedBy` → `https://github.com/facebookresearch/llama`
- `references_model` → 8 Llama 2 models on HF
- `references_dataset` / `references_space` → linked items

Round 2 follows the GitHub edge into the source repository.

### Single model with owner fan-out

```bash
opc crawl --platforms huggingface.co --rounds 2 \
    https://huggingface.co/meta-llama/Llama-3.2-1B
```

Round 1 emits `owned_by` → `huggingface.co/meta-llama`. Round 2 fetches
the meta-llama org and walks every model / dataset / space it owns.

### Author corpus

```bash
opc crawl --platforms huggingface.co --rounds 2 \
    https://huggingface.co/karpathy
```

Walks every model / dataset / space by Andrej Karpathy, plus `member_of`
edges to each of his orgs.

### Collection walk

```bash
opc crawl --platforms huggingface.co --rounds 2 \
    https://huggingface.co/collections/meta-llama/metas-llama-32-language-models-and-evals-675bfd70e574a62dd0e40586
```

Walks every model / dataset / space / paper in the collection's `items[]`.

## Limitations (v3.4)

- **No `has_member` (Org → User) edges.** The
  `/api/organizations/<name>/members` endpoint is auth-gated. Same
  situation as Infoscience's removed `has_member` flow.
- **Paper authors are bare names without ORCID.** No `authored_by`
  edges to ORCID URLs (unlike DataCite). Cross-platform identity
  resolution stays downstream.
- **Model/dataset card README parsing not implemented.**
  `cardData.tags` sometimes contains `arxiv:<id>` references but card
  formats vary. Defer to v3.4.1 if useful.
- **Discussion / community / dataset-viewer / model-leaderboard data
  not crawled.** Read-only metadata only.
- **Crossref-issued DOIs in cards not resolved** — stays with the
  future Crossref adapter.
- **Legacy arxiv IDs not accepted** as paper seeds.
- **No deposit/upload/draft flows.** Read-only adapter.
```

- [ ] **Step 2: Update `README.md`**

Find the existing "Multi-platform support" section. Add a HuggingFace bullet right after the DataCite bullet:

```markdown
- **HuggingFace** (`huggingface.co`): models, datasets, spaces, papers,
  users, organizations, and collections. Papers carry `linkedModels` /
  `linkedDatasets` / `linkedSpaces` + `githubRepo` + arxiv ID — the
  strongest cross-platform pivot in the graph. Anonymous reads
  supported. See [docs/HUGGINGFACE.md](docs/HUGGINGFACE.md).
```

- [ ] **Step 3: Update `docs/index.md`**

Add a pointer below the existing DataCite link:

```markdown
- [HuggingFace support](HUGGINGFACE.md) — models / datasets / spaces /
  papers / collections, with the strongest cross-platform paper bridge
  (arxiv + github + linked HF repos).
```

- [ ] **Step 4: Update `CHANGELOG.md`**

Under `[Unreleased]`, add a new v3.4 section block (after the existing v3.3 DataCite block):

```markdown
### Added (v3.4 — HuggingFace adapter)
- `HuggingFaceAdapter` and `HuggingFaceHTTPClient` under
  `src/open_pulse_crawler/platforms/huggingface/`. Crawls HuggingFace —
  users, organizations, models, datasets, spaces, papers, and collections.
- Five subkind models: `HuggingFaceUser`, `HuggingFaceOrg`,
  `HuggingFaceRepo` (models/datasets/spaces unified via `repo_type:
  Literal["model","dataset","space"]`), `HuggingFacePaper`,
  `HuggingFaceCollection`.
- Twelve new edge kinds: `owns`, `member_of`, `owned_by`, `uses_model`,
  `related_to.IsIdenticalTo` (paper → arxiv), `related_to.IsSupplementedBy`
  (paper → github), `references_model`, `references_dataset`,
  `references_space`, `contains`.
- Cross-platform paper bridge: a HuggingFace paper's arxiv ID emits the
  same canonical `arxiv.org/abs/<id>` URL that Zenodo, Infoscience, and
  DataCite records produce when they reference the same paper. The
  shared `synthesize_target_url` helper handles the URL synthesis with
  no HF-specific routing code.
- USER_OR_ORG disambiguation pattern (matches GitHub): `huggingface.co/<name>`
  classifies as `USER_OR_ORG`; `fetch` probes `/api/users/<name>/overview`
  first and falls through to `/api/organizations/<name>/overview` on 404.
- Reserved first-segment words (`datasets`, `spaces`, `papers`,
  `collections`) are recognized by `classify` and `normalize_uri` to
  prevent the bare-`<owner>/<name>` model regex from misclaiming them.
- HTTP 429 retry-after handling in `HuggingFaceHTTPClient` (one
  automatic retry honoring `Retry-After`, capped at 60s).
- Link-header cursor pagination on list endpoints
  (`/api/models?author=<x>`, `/api/datasets?author=<x>`, `/api/spaces?author=<x>`).
- CLI registers `HuggingFaceAdapter` against `huggingface.co`
  (anonymous-friendly; tokens optional).
- `POST /api/v2/crawl` OpenAPI examples: `huggingface_paper_llama2`,
  `huggingface_model_llama`, `huggingface_user_karpathy`.
- Integration test against live `huggingface.co`
  (`tests/integration/test_huggingface_dryrun.py`) — verifies the
  cross-platform paper bridge end-to-end with the Llama 2 paper seed.
- `docs/HUGGINGFACE.md`.

### Out of scope (v3.4)
- `has_member` (Org → User) edges — auth-gated endpoint, same as
  Infoscience.
- Paper authors → ORCID linkage — paper authors are bare `{name}`
  strings without ORCID. Cross-platform identity stays downstream.
- Model/dataset card README parsing (`cardData.tags` arxiv extraction).
- Discussion / community / dataset-viewer / model-leaderboard data.
- Legacy arxiv IDs (`papers/cond-mat/0303517` form).
- `tools/scripts/fetch_public_projects.py` extension for HF — no
  natural "browse all HF entities" use case.
```

- [ ] **Step 5: Commit**

```
git add docs/HUGGINGFACE.md README.md docs/index.md CHANGELOG.md
git commit -m "docs: HuggingFace adapter (v3.4.0) — HUGGINGFACE.md, README, CHANGELOG"
```

---

## Task 12: Final lint + test verification

- [ ] **Step 1: Lint**

```
VIRTUAL_ENV= uv run ruff check src/
```
Expected: no NEW errors beyond the develop baseline (~22-25 pre-existing).

- [ ] **Step 2: Full unit suite (serial)**

```
VIRTUAL_ENV= uv run pytest -n 0 -m "not integration" --ignore=tests/test_api.py --ignore=tests/test_auth.py -q --no-cov
```
Expected: all tests pass (~485+; ~40-45 new tests from Spec 5 plus all pre-existing).

- [ ] **Step 3: Diff stat**

```
git diff --stat origin/develop...HEAD | tail -20
```
Eyeball: HF adapter package ~700 LoC; tests ~850 LoC; docs ~200 LoC. Total ~1,750-2,000 LoC added by Spec 5.

- [ ] **Step 4: Stop and report ready for review**

Do NOT push. Report:
- Total new/modified files.
- Total tests added (delta).
- Pass count.
- Any deferred concerns (e.g., did the integration test pass against live HF? did any 429 retries fire during the live probe?).

---

## Self-Review

**Spec coverage:**

| Spec section | Tasks |
|---|---|
| §0 Starting point | n/a (informational) |
| §1 Architecture & module layout | 1, 2, 5, 8 |
| §2 Data model (5 subkinds) | 1 |
| §3.1 Hosts owned | 5, 8 |
| §3.2 normalize_uri | 5 |
| §3.3 classify | 5 |
| §3.4 fetch (5 URL shapes + USER_OR_ORG fallthrough) | 6 |
| §3.5 expand (12 edge kinds) | 7 |
| §3.6 rate_limit_state | 5 |
| §4.1 Configuration | 8, 11 |
| §4.2 Caching | inherited from Task 2 (`_cache_dir` parameter) |
| §4.3 Cross-platform integration recap | 10 (integration test verifies it), 11 (docs) |
| §4.4 Testing | every task + 10 (integration) |
| §5 Effort estimate | n/a (informational) |
| §6 Open questions | 1 (collapse vs split deferred during implementation), 7 (no fetch_public_projects extension) |

**Placeholder scan:** No "TBD" / "TODO" / "implement later" / vague phrases. Each task has actual code + commands the executor needs.

**Type consistency:** `HuggingFaceUser` / `HuggingFaceOrg` / `HuggingFaceRepo` / `HuggingFacePaper` / `HuggingFaceCollection` spelled identically across Tasks 1, 5, 6, 7. `HuggingFaceHTTPClient` method names (`get_model`, `get_dataset`, `get_space`, `get_paper`, `get_collection`, `get_user_overview`, `get_org_overview`, `iter_models_by_author`, `iter_datasets_by_author`, `iter_spaces_by_author`) match across Tasks 3, 4, 6, 7. Edge kinds (`owns`, `member_of`, `owned_by`, `uses_model`, `related_to.IsIdenticalTo`, `related_to.IsSupplementedBy`, `references_model`, `references_dataset`, `references_space`, `contains`) consistent across spec §3.5, Task 7 code + tests, docs Task 11. `_RESERVED_FIRST_SEGMENTS = {"datasets","spaces","papers","collections"}` consistent across Tasks 5 + 6. `repo_type: Literal["model","dataset","space"]` consistent everywhere.

**One known shape question** carried into the executor's notebook: Task 6's `_build_repo` populates `pipeline_tag` / `library_name` only when `repo_type == "model"`. The conditional logic on the Pydantic constructor call is a bit awkward (`pipeline_tag=raw.get(...) or "" if repo_type == "model" else ""`); if the executor finds a cleaner shape (e.g., compute the value in a local variable first), prefer that. The behavior — empty string for non-models — is what matters.
