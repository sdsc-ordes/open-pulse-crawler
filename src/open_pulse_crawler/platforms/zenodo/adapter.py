"""Zenodo PlatformAdapter — classify / fetch / expand / normalize_uri.

See ``docs/superpowers/specs/2026-05-28-zenodo-adapter-design.md``.

Task 6 ships ``classify``, ``normalize_uri``, ``fetch``, and
``rate_limit_state``. ``expand`` raises ``NotImplementedError`` until
Task 7 lands.
"""
from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Dict, Iterable, Optional
from urllib.parse import urlparse

from ...models import (
    ZenodoCommunityModel,
    ZenodoRecordModel,
    ZenodoUserModel,
)
from ...node_id import (
    NodeKind,
    canonical_url,
    is_zenodo_doi_url,
    rewrite_zenodo_doi_url,
)
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo
from .client import ZenodoClient

logger = logging.getLogger(__name__)

# Recognize the supported Zenodo path tails. Paths are matched after
# normalization, so the leading slash is already stripped.
_RECORD_PATH = re.compile(r"^records/(?P<id>\d+)$")
_LEGACY_RECORD_PATH = re.compile(r"^/record/(?P<id>\d+)/?$")
_COMMUNITY_PATH = re.compile(r"^communities/(?P<slug>[^/]+)$")
_USER_PATH = re.compile(r"^users/(?P<id>\d+)$")


class ZenodoAdapter(PlatformAdapter):
    """``PlatformAdapter`` over a single :class:`ZenodoClient`.

    One instance per configured host (``zenodo.org`` and
    ``sandbox.zenodo.org`` are separate adapters with separate clients).
    """

    platform: ClassVar[str] = "zenodo"

    def __init__(self, client: ZenodoClient, instance_host: str) -> None:
        self._client = client
        self.instance_host = instance_host
        # Cache: version-record URI -> concept URL. Populated on the first
        # fetch of a version URL so subsequent visits skip the extra API hop.
        self._concept_cache: Dict[str, str] = {}

    # ---- normalize_uri -----------------------------------------------------

    def normalize_uri(self, raw: str) -> str:
        """Return the canonical Zenodo URL for ``raw``.

        Accepts canonical Zenodo URLs, the legacy ``/record/<id>`` singular
        form, and DOI URLs (``https://doi.org/10.5281/zenodo.<id>`` for
        prod, ``10.5072/zenodo.<id>`` for sandbox).

        Raises ``ValueError`` on non-Zenodo DOI URLs so the seed parser
        surfaces them as user error.
        """
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"empty or non-string seed: {raw!r}")

        # DOI URL?
        if is_zenodo_doi_url(raw):
            rewritten = rewrite_zenodo_doi_url(raw)
            assert rewritten is not None  # is_zenodo_doi_url guarantees this
            return rewritten

        if raw.startswith("https://doi.org/") or raw.startswith("http://doi.org/"):
            # A non-Zenodo DOI URL -- explicitly out of scope for this adapter.
            raise ValueError(
                f"non-Zenodo DOI URL not supported by ZenodoAdapter: {raw!r}"
            )

        parts = urlparse(raw)
        host = (parts.netloc or self.instance_host).lower()
        path = parts.path or "/"
        # Legacy /record/<id> -> /records/<id>
        m = _LEGACY_RECORD_PATH.match(path)
        if m:
            path = f"/records/{m.group('id')}"
        return canonical_url(host, path)

    # ---- classify ----------------------------------------------------------

    def classify(self, uri: str) -> Optional[NodeKind]:
        """Map ``uri`` onto the cross-platform :class:`NodeKind` enum.

        Communities and users both flatten to ``USER_OR_ORG`` to match the
        enum's vocabulary; the internal kind is recovered from the path
        shape at ``fetch`` time.
        """
        path = urlparse(self.normalize_uri(uri)).path.strip("/")
        if _RECORD_PATH.match(path):
            return NodeKind.REPO
        if _COMMUNITY_PATH.match(path) or _USER_PATH.match(path):
            return NodeKind.USER_OR_ORG
        return None

    # ---- fetch -------------------------------------------------------------

    def fetch(self, uri: str):
        """Fetch the platform entity at ``uri``.

        Returns one of ``ZenodoRecordModel`` / ``ZenodoCommunityModel`` /
        ``ZenodoUserModel``, or ``None`` for unknown paths or 404s.
        """
        uri = self.normalize_uri(uri)
        path = urlparse(uri).path.strip("/")

        m = _RECORD_PATH.match(path)
        if m:
            return self._build_record(uri, m.group("id"))

        m = _COMMUNITY_PATH.match(path)
        if m:
            return self._build_community(uri, m.group("slug"))

        m = _USER_PATH.match(path)
        if m:
            return self._build_user(uri, m.group("id"))

        return None

    def _build_community(self, uri: str, slug: str) -> Optional[ZenodoCommunityModel]:
        raw = self._client.get_community(slug)
        if raw is None:
            return None
        meta = raw.get("metadata", {}) or {}
        type_blob = meta.get("type", {}) or {}
        return ZenodoCommunityModel(
            url=uri,
            login=slug,
            platform="zenodo",
            id=int(raw["id"]) if str(raw.get("id", "")).isdigit() else 0,
            name=meta.get("title", "") or "",
            description=meta.get("description", "") or "",
            community_type=type_blob.get("id", "") if isinstance(type_blob, dict) else "",
        )

    def _build_user(self, uri: str, user_id: str) -> Optional[ZenodoUserModel]:
        raw = self._client.get_user(user_id)
        if raw is None:
            return None
        username = raw.get("username") or str(user_id)
        profile = raw.get("profile", {}) or {}
        orcid: Optional[str] = None
        for ident in profile.get("identifiers", []) or []:
            if isinstance(ident, dict) and ident.get("scheme") == "orcid":
                orcid = ident.get("identifier")
                break
        return ZenodoUserModel(
            url=uri,
            login=username,
            platform="zenodo",
            id=int(raw["id"]) if str(raw.get("id", "")).isdigit() else 0,
            name=profile.get("full_name", "") or "",
            orcid=orcid,
            affiliation=profile.get("affiliations", "") or "",
        )

    def _build_record(self, uri: str, record_id: str) -> Optional[ZenodoRecordModel]:
        raw = self._client.get_record(record_id)
        if raw is None:
            return None

        concept_recid = self._concept_recid(raw)

        # If this is a version (concept_recid != own id), refetch the concept.
        if concept_recid and str(concept_recid) != str(record_id):
            concept_url = f"https://{self.instance_host}/records/{concept_recid}"
            self._concept_cache[uri] = concept_url
            concept_raw = self._client.get_record(concept_recid)
            if concept_raw is None:
                # Concept fetch failed -- fall back to using this record as
                # its own concept so we still land *something* in the graph.
                return self._record_from(uri, raw, fallback_concept_recid=record_id)
            return self._record_from(concept_url, concept_raw)

        # This record IS the concept (or has no versioning).
        return self._record_from(uri, raw)

    def _concept_recid(self, raw: Dict[str, Any]) -> Optional[str]:
        """Pull ``conceptrecid`` from a record payload.

        Zenodo exposes it at the top level (``conceptrecid``) and inside
        ``metadata.relations.version[0].parent.pid_value`` -- check both
        because the two forms appear on different endpoints.
        """
        v = raw.get("conceptrecid")
        if v:
            return str(v)
        try:
            rel = raw["metadata"]["relations"]["version"][0]["parent"]["pid_value"]
            if rel:
                return str(rel)
        except (KeyError, IndexError, TypeError):
            pass
        return None

    def _record_from(
        self,
        uri: str,
        raw: Dict[str, Any],
        *,
        fallback_concept_recid: Optional[str] = None,
    ) -> ZenodoRecordModel:
        meta = raw.get("metadata", {}) or {}
        doi = raw.get("doi") or meta.get("doi") or ""
        # Self-reference invariant: when the record isn't versioned, the
        # concept DOI equals the record's own DOI.
        concept_doi = raw.get("conceptdoi") or doi

        # Version chain -- Zenodo includes every version in the concept
        # record's ``metadata.relations.version`` list.
        versions = []
        latest = {"version": "", "doi": "", "url": ""}
        for v in (meta.get("relations", {}) or {}).get("version", []):
            if not isinstance(v, dict):
                continue
            v_recid = v.get("parent", {}).get("pid_value")
            v_version_blob = v.get("version", {})
            v_version = (
                v_version_blob.get("value", "")
                if isinstance(v_version_blob, dict) else str(v_version_blob or "")
            )
            v_doi = v.get("doi", "")
            v_pubdate = v.get("publication_date", "")
            v_url = f"https://{self.instance_host}/records/{v_recid}" if v_recid else ""
            versions.append({
                "doi": v_doi,
                "url": v_url,
                "version": v_version,
                "publication_date": v_pubdate,
                "record_id": str(v_recid or ""),
            })
            if v.get("is_last"):
                latest = {"version": v_version, "doi": v_doi, "url": v_url}

        resource_type_blob = meta.get("resource_type", {}) or {}
        resource_type = (
            resource_type_blob.get("id", "")
            if isinstance(resource_type_blob, dict) else ""
        )
        license_blob = meta.get("license", {}) or {}
        license_id = (
            license_blob.get("id", "")
            if isinstance(license_blob, dict) else str(license_blob)
        )

        # Slim raw-metadata payload stashed in extras for Task 7's
        # ``_expand_record`` to read without re-fetching.
        raw_for_expand = {
            "communities": meta.get("communities", []) or [],
            "owners": raw.get("owners", []) or meta.get("owners", []) or [],
            "related_identifiers": meta.get("related_identifiers", []) or [],
        }

        return ZenodoRecordModel(
            url=uri,
            full_name=concept_doi,
            platform="zenodo",
            id=int(raw["id"]) if str(raw.get("id", "")).isdigit() else 0,
            name=meta.get("title", "") or "",
            doi=concept_doi,                 # concept node carries concept DOI
            concept_doi=concept_doi,         # self-reference invariant
            latest_version=latest["version"] or "",
            latest_version_doi=latest["doi"] or "",
            latest_version_url=latest["url"] or "",
            versions=versions,
            resource_type=resource_type,
            publication_date=meta.get("publication_date", "") or "",
            title=meta.get("title", "") or "",
            creators=meta.get("creators", []) or [],
            keywords=meta.get("keywords", []) or [],
            license=license_id,
            access_right=meta.get("access_right", "open") or "open",
            extras={"_raw_metadata": raw_for_expand},
        )

    # ---- expand (placeholder -- Task 7 implements) -------------------------

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        raise NotImplementedError("Task 7 implements expand()")

    # ---- rate_limit --------------------------------------------------------

    def rate_limit_state(self) -> RateLimitInfo:
        # Zenodo's rate-limit headers aren't reliably surfaced; return
        # conservative placeholders, matching the GitLab adapter pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
