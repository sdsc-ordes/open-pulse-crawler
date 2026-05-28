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

    # ---- rate_limit --------------------------------------------------------

    def rate_limit_state(self) -> RateLimitInfo:
        # DSpace doesn't reliably surface rate-limit headers; return
        # conservative placeholders matching the Zenodo adapter pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
