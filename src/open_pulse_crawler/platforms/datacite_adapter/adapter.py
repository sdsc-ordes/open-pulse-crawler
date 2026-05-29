"""DataCite Commons PlatformAdapter — classify / fetch / expand / normalize_uri.

See ``docs/superpowers/specs/2026-05-29-datacite-adapter-design.md``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

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

    # ---- rate_limit --------------------------------------------------------

    def rate_limit_state(self) -> RateLimitInfo:
        # DataCite doesn't surface rate-limit headers reliably; conservative
        # placeholders matching the Infoscience adapter pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
