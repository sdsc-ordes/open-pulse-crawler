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
# These are HuggingFace platform routes, not owner accounts.
_RESERVED_FIRST_SEGMENTS = {
    "datasets", "spaces", "papers", "collections",
    "blog", "docs", "tasks", "learn", "pricing",
    "enterprise", "inference-endpoints",
}

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

        # Model-specific
        pipeline_tag = ""
        library_name = ""
        if repo_type == "model":
            pipeline_tag = raw.get("pipeline_tag", "") or ""
            library_name = raw.get("library_name", "") or ""

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
            pipeline_tag=pipeline_tag,
            library_name=library_name,
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

    # ---- expand (placeholder — Task 7 implements) --------------------------

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        raise NotImplementedError("Task 7 implements expand()")

    # ---- rate_limit --------------------------------------------------------

    def rate_limit_state(self) -> RateLimitInfo:
        # HuggingFace doesn't reliably surface rate-limit headers;
        # conservative placeholders matching the Infoscience/DataCite pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
