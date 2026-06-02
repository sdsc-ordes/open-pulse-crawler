"""OpenAlex PlatformAdapter — classify / fetch / normalize_uri + builders.

OpenAlex (``api.openalex.org``) catalogs scholarly works, authors,
institutions, sources (venues), and funders. This adapter accepts the
external-identifier host forms (doi.org / orcid.org / ror.org) and the
native ``openalex.org`` / ``api.openalex.org`` forms, canonicalizes them,
classifies the resulting node kind, and fetches the entity from a client.

Works and Authors and Institutions that miss in OpenAlex delegate to an
optional ``fallback_adapter`` (DataCite), letting the BFS still resolve a
node that OpenAlex doesn't index. Sources and Funders have no DataCite
equivalent, so a miss returns ``None`` (no fallback).

The ``expand`` method is a placeholder here — edge traversal is a later task.
"""
from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from ...models import (
    ExternalIdentifier,
    OpenAlexAuthor,
    OpenAlexFunder,
    OpenAlexInstitution,
    OpenAlexSource,
    OpenAlexWork,
)
from ...node_id import NodeKind, canonical_url
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo

logger = logging.getLogger(__name__)

# Crossref Funder Registry DOI prefix — disambiguates a doi.org URL between
# an OpenAlex Work (any other prefix) and a Funder (this prefix).
_FUNDER_DOI_PREFIX = "10.13039"

_DOI_PATH = re.compile(r"^(?P<doi>10\.[^/]+/.+)$")
_ORCID_PATH = re.compile(r"^(?P<id>\d{4}-\d{4}-\d{4}-\d{3}[\dX])/?$")
_ROR_PATH = re.compile(r"^(?P<id>[a-z0-9]+)/?$")
_OPENALEX_PATH = re.compile(r"^(?P<id>[WAISF]\d+)/?$")
_API_PATH = re.compile(
    r"^(?P<kind>works|authors|institutions|sources|funders)/(?P<id>[^/]+)/?$"
)

_OPENALEX_URL_PREFIX = "https://openalex.org/"
_ID_URL_PREFIXES = (
    "https://openalex.org/",
    "https://orcid.org/",
    "http://orcid.org/",
    "https://ror.org/",
    "http://ror.org/",
    "https://doi.org/",
    "http://doi.org/",
)


def _bare(value: str) -> str:
    """Strip any known identifier URL prefix, returning the bare id.

    Handles openalex.org / orcid.org / ror.org / doi.org (http + https).
    """
    if not value:
        return ""
    for prefix in _ID_URL_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _parse_year(raw: Any) -> Optional[int]:
    """Coerce a publication year to int, tolerating None/str/garbage."""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class OpenAlexAdapter(PlatformAdapter):
    platform: ClassVar[str] = "openalex"

    def __init__(self, client, instance_host="api.openalex.org", fallback_adapter=None):
        self._client = client
        self.instance_host = instance_host
        self.fallback_adapter = fallback_adapter

    # ---- normalize_uri -----------------------------------------------------

    def normalize_uri(self, raw: str) -> str:
        """Return the canonical OpenAlex-adapter key URL for ``raw``.

        doi.org → Funder canonical (10.13039 prefix) or Work canonical
        (lowercased DOI); orcid.org / ror.org pass through; openalex.org and
        api.openalex.org forms map to ``https://openalex.org/<ID>``.
        Unknown forms are canonicalized so the BFS can drop them.
        """
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"empty or non-string seed: {raw!r}")

        parts = urlsplit(raw)
        host = (parts.netloc or self.instance_host).lower()
        path = (parts.path or "/").rstrip("/")
        path_inner = path.lstrip("/")

        # --- doi.org → Work or Funder ---
        if host == "doi.org":
            m = _DOI_PATH.match(path_inner)
            if m:
                doi = m.group("doi")
                if doi.split("/", 1)[0] == _FUNDER_DOI_PREFIX:
                    # Funder DOI: keep case (Crossref Funder DOIs are numeric).
                    return f"https://doi.org/{doi}"
                # Work DOI: lowercase per OpenAlex canonicalization.
                return f"https://doi.org/{doi.lower()}"

        # --- orcid.org → Author ---
        if host == "orcid.org":
            m = _ORCID_PATH.match(path_inner)
            if m:
                return f"https://orcid.org/{m.group('id')}"

        # --- ror.org → Institution ---
        if host == "ror.org":
            m = _ROR_PATH.match(path_inner)
            if m:
                return f"https://ror.org/{m.group('id')}"

        # --- openalex.org/<W|A|I|S|F id> ---
        if host == "openalex.org":
            m = _OPENALEX_PATH.match(path_inner)
            if m:
                return f"https://openalex.org/{m.group('id')}"

        # --- api.openalex.org/<kind>/<id> ---
        if host == "api.openalex.org":
            m = _API_PATH.match(path_inner)
            if m:
                return f"https://openalex.org/{_bare(m.group('id'))}"

        # Fallback: canonicalize so the BFS can drop unknowns.
        return canonical_url(host, path or "/")

    # ---- classify ----------------------------------------------------------

    def classify(self, uri: str) -> Optional[NodeKind]:
        """Return the NodeKind for ``uri`` from its canonical host/shape."""
        normed = self.normalize_uri(uri)
        parts = urlsplit(normed)
        host = parts.netloc.lower()
        path_inner = (parts.path or "/").lstrip("/").rstrip("/")

        if host == "doi.org":
            m = _DOI_PATH.match(path_inner)
            if m:
                if m.group("doi").split("/", 1)[0] == _FUNDER_DOI_PREFIX:
                    return NodeKind.ORG          # Funder
                return NodeKind.REPO             # Work
        if host == "orcid.org" and _ORCID_PATH.match(path_inner):
            return NodeKind.USER                 # Author
        if host == "ror.org" and _ROR_PATH.match(path_inner):
            return NodeKind.ORG                  # Institution
        if host == "openalex.org":
            m = _OPENALEX_PATH.match(path_inner)
            if m:
                first = m.group("id")[0]
                if first == "W":
                    return NodeKind.REPO         # Work
                if first == "A":
                    return NodeKind.USER         # Author
                # I (institution), S (source), F (funder) → ORG
                return NodeKind.ORG
        return None

    # ---- fetch -------------------------------------------------------------

    def fetch(self, uri: str):
        """Fetch the OpenAlex entity, delegating misses per the kind rules."""
        normed = self.normalize_uri(uri)
        parts = urlsplit(normed)
        host = parts.netloc.lower()
        path_inner = (parts.path or "/").lstrip("/").rstrip("/")

        # doi.org → Work (default) or Funder (10.13039 prefix)
        if host == "doi.org":
            m = _DOI_PATH.match(path_inner)
            if m:
                doi = m.group("doi")
                if doi.split("/", 1)[0] == _FUNDER_DOI_PREFIX:
                    raw = self._client.get_funder(doi)
                    if raw is None:
                        return None              # no DataCite funder equivalent
                    return self._build_funder(normed, raw)
                raw = self._client.get_work(doi)
                if raw is None:
                    return self._fallback(normed)
                return self._build_work(normed, raw)

        # orcid.org → Author
        if host == "orcid.org":
            m = _ORCID_PATH.match(path_inner)
            if m:
                raw = self._client.get_author(m.group("id"))
                if raw is None:
                    return self._fallback(normed)
                return self._build_author(normed, raw)

        # ror.org → Institution
        if host == "ror.org":
            m = _ROR_PATH.match(path_inner)
            if m:
                raw = self._client.get_institution(m.group("id"))
                if raw is None:
                    return self._fallback(normed)
                return self._build_institution(normed, raw)

        # openalex.org/<ID> → dispatch by id prefix
        if host == "openalex.org":
            m = _OPENALEX_PATH.match(path_inner)
            if m:
                oid = m.group("id")
                first = oid[0]
                if first == "W":
                    raw = self._client.get_work(oid)
                    if raw is None:
                        return self._fallback(normed)
                    return self._build_work(normed, raw)
                if first == "A":
                    raw = self._client.get_author(oid)
                    if raw is None:
                        return self._fallback(normed)
                    return self._build_author(normed, raw)
                if first == "I":
                    raw = self._client.get_institution(oid)
                    if raw is None:
                        return self._fallback(normed)
                    return self._build_institution(normed, raw)
                if first == "S":
                    raw = self._client.get_source(oid)
                    if raw is None:
                        return None              # no source fallback
                    return self._build_source(normed, raw)
                if first == "F":
                    raw = self._client.get_funder(oid)
                    if raw is None:
                        return None              # no funder fallback
                    return self._build_funder(normed, raw)

        return None

    def _fallback(self, uri: str):
        """Delegate to the configured fallback adapter, else return None."""
        if self.fallback_adapter is not None:
            return self.fallback_adapter.fetch(uri)
        return None

    # ---- builders ----------------------------------------------------------

    def _external_ids(self, raw: Dict[str, Any]) -> List[ExternalIdentifier]:
        """One ExternalIdentifier per key in the API ``ids`` block."""
        ids = raw.get("ids", {}) or {}
        out: List[ExternalIdentifier] = []
        if isinstance(ids, dict):
            for k, v in ids.items():
                if not v:
                    continue
                out.append(ExternalIdentifier(scheme=str(k), value=str(v)))
        return out

    def _build_work(self, uri: str, raw: Dict[str, Any]) -> OpenAlexWork:
        bare_doi = _bare(raw.get("doi", "") or "").lower()

        openalex_id = _bare(raw.get("id", "") or "")

        creators: List[Dict[str, Any]] = []
        for authorship in raw.get("authorships", []) or []:
            if not isinstance(authorship, dict):
                continue
            author = authorship.get("author", {}) or {}
            institutions: List[str] = []
            for inst in authorship.get("institutions", []) or []:
                if not isinstance(inst, dict):
                    continue
                ror = _bare(inst.get("ror", "") or "")
                if ror:
                    institutions.append(ror)
            creators.append({
                "name": author.get("display_name", "") or "",
                "orcid": _bare(author.get("orcid", "") or ""),
                "institutions": institutions,
            })

        # references kept as RAW W-urls for expand to resolve later.
        references: List[str] = [
            r for r in (raw.get("referenced_works", []) or []) if r
        ]

        published_in = ""
        primary = raw.get("primary_location", {}) or {}
        if isinstance(primary, dict):
            source = primary.get("source", {}) or {}
            if isinstance(source, dict):
                published_in = source.get("id", "") or ""

        open_access = raw.get("open_access", {}) or {}
        is_oa = open_access.get("is_oa") if isinstance(open_access, dict) else None

        cited_by_count_raw = raw.get("cited_by_count")
        try:
            cited_by_count = (
                int(cited_by_count_raw) if cited_by_count_raw is not None else None
            )
        except (TypeError, ValueError):
            cited_by_count = None

        full_name = bare_doi or openalex_id

        return OpenAlexWork(
            url=uri,
            full_name=full_name,
            platform="openalex",
            doi=bare_doi,
            openalex_id=openalex_id,
            title=raw.get("title", "") or "",
            publication_year=_parse_year(raw.get("publication_year")),
            work_type=raw.get("type", "") or "",
            cited_by_count=cited_by_count,
            is_oa=is_oa,
            references=references,
            published_in=published_in,
            creators=creators,
            external_identifiers=self._external_ids(raw),
        )

    def _build_author(self, uri: str, raw: Dict[str, Any]) -> OpenAlexAuthor:
        orcid = _bare(raw.get("orcid", "") or "")
        openalex_id = _bare(raw.get("id", "") or "")
        login = orcid or openalex_id

        affiliations: List[str] = []
        # OpenAlex exposes both ``last_known_institutions`` and ``affiliations``.
        for key in ("last_known_institutions", "affiliations"):
            for entry in raw.get(key, []) or []:
                inst = entry
                if isinstance(entry, dict) and "institution" in entry:
                    inst = entry.get("institution", {}) or {}
                if not isinstance(inst, dict):
                    continue
                ror = inst.get("ror", "") or ""
                if ror and ror not in affiliations:
                    affiliations.append(ror)

        return OpenAlexAuthor(
            url=uri,
            login=login,
            platform="openalex",
            name=raw.get("display_name", "") or "",
            orcid=orcid,
            openalex_id=openalex_id,
            affiliations=affiliations,
            external_identifiers=self._external_ids(raw),
        )

    def _build_institution(self, uri: str, raw: Dict[str, Any]) -> OpenAlexInstitution:
        ror = _bare(raw.get("ror", "") or "")
        openalex_id = _bare(raw.get("id", "") or "")
        login = ror or openalex_id
        return OpenAlexInstitution(
            url=uri,
            login=login,
            platform="openalex",
            name=raw.get("display_name", "") or "",
            ror_id=ror,
            openalex_id=openalex_id,
            country_code=raw.get("country_code", "") or "",
            institution_type=raw.get("type", "") or "",
            external_identifiers=self._external_ids(raw),
        )

    def _build_source(self, uri: str, raw: Dict[str, Any]) -> OpenAlexSource:
        openalex_id = _bare(raw.get("id", "") or "")
        issns_raw = raw.get("issn", []) or []
        issns = [str(i) for i in issns_raw if i] if isinstance(issns_raw, list) else []
        return OpenAlexSource(
            url=uri,
            login=openalex_id,
            platform="openalex",
            name=raw.get("display_name", "") or "",
            openalex_id=openalex_id,
            issn_l=raw.get("issn_l", "") or "",
            issns=issns,
            host_organization=raw.get("host_organization_name", "") or "",
            is_oa=raw.get("is_oa"),
            external_identifiers=self._external_ids(raw),
        )

    def _build_funder(self, uri: str, raw: Dict[str, Any]) -> OpenAlexFunder:
        openalex_id = _bare(raw.get("id", "") or "")
        ids = raw.get("ids", {}) or {}
        funder_doi = ""
        if isinstance(ids, dict):
            funder_doi = _bare(ids.get("doi", "") or "")
        login = funder_doi or openalex_id
        return OpenAlexFunder(
            url=uri,
            login=login,
            platform="openalex",
            name=raw.get("display_name", "") or "",
            openalex_id=openalex_id,
            funder_doi=funder_doi,
            country_code=raw.get("country_code", "") or "",
            external_identifiers=self._external_ids(raw),
        )

    # ---- expand ------------------------------------------------------------

    @staticmethod
    def _canonical_doi_url(doi: str) -> str:
        """Strip a doi.org URL prefix (http/https) then return a lowercased
        ``https://doi.org/<bare doi>`` (mirrors the client's logic)."""
        rest = doi
        for prefix in ("https://doi.org/", "http://doi.org/"):
            if rest.startswith(prefix):
                rest = rest[len(prefix):]
                break
        return f"https://doi.org/{rest.lower()}"

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        if isinstance(node, OpenAlexWork):
            yield from self._expand_work(node, opts)
        elif isinstance(node, OpenAlexAuthor):
            yield from self._expand_entity(
                node, opts, filter_key="author.id", kind="authored",
            )
        elif isinstance(node, OpenAlexInstitution):
            yield from self._expand_entity(
                node, opts, filter_key="institutions.id", kind="affiliated_work",
            )
        # OpenAlexSource / OpenAlexFunder are passive — no edges.
        else:
            yield from ()

    def _expand_work(self, node: OpenAlexWork, opts: ExpandOpts) -> Iterable[Edge]:
        # references (outbound) — resolve raw W-urls to canonical edge urls.
        if node.references:
            resolved = self._client.resolve_ids_to_canonical(node.references)
            for w_url in node.references:
                yield Edge(src=node.url, kind="references",
                           dst=resolved.get(w_url, w_url))

        # cited_by (inbound, capped) — needs a stable openalex_id anchor.
        if node.openalex_id:
            for c in self._client.iter_citing_works(
                node.openalex_id, cap=opts.max_citations_per_work,
            ):
                doi = c.get("doi")
                dst = self._canonical_doi_url(doi) if doi else c["id"]
                yield Edge(src=node.url, kind="cited_by", dst=dst)
            if (opts.max_citations_per_work is not None
                    and node.cited_by_count is not None
                    and node.cited_by_count > opts.max_citations_per_work):
                logger.info(
                    "OpenAlex cited_by truncated for %s: %d of %d",
                    node.url, opts.max_citations_per_work, node.cited_by_count,
                )

        # authored_by + affiliated_with — only for creators with an ORCID
        # (no stable author node to anchor otherwise).
        for creator in node.creators or []:
            if not isinstance(creator, dict):
                continue
            orcid = creator.get("orcid", "") or ""
            if not orcid:
                continue
            author_url = f"https://orcid.org/{orcid}"
            yield Edge(src=node.url, kind="authored_by", dst=author_url)
            for ror in creator.get("institutions", []) or []:
                if not ror:
                    continue
                yield Edge(src=author_url, kind="affiliated_with",
                           dst=f"https://ror.org/{ror}")

        # published_in — source venue url
        if node.published_in:
            yield Edge(src=node.url, kind="published_in", dst=node.published_in)

        # funded_by — funder urls (may be empty)
        for url in node.funded_by or []:
            if url:
                yield Edge(src=node.url, kind="funded_by", dst=url)

    def _expand_entity(
        self, node, opts: ExpandOpts, *, filter_key: str, kind: str,
    ) -> Iterable[Edge]:
        """Seed-expansion for Author / Institution: works by that entity (capped)."""
        if not node.openalex_id:
            return
        cap = opts.max_works_per_entity
        emitted = 0
        for w in self._client.iter_works_by_entity(
            filter_key, node.openalex_id, cap=cap,
        ):
            doi = w.get("doi")
            dst = self._canonical_doi_url(doi) if doi else w["id"]
            yield Edge(src=node.url, kind=kind, dst=dst)
            emitted += 1
        # Best-effort "possibly truncated" note: no total count available, so
        # only log when the iterator returned exactly ``cap`` items.
        if cap is not None and emitted == cap:
            logger.info(
                "OpenAlex %s possibly truncated for %s: emitted %d (cap)",
                kind, node.url, cap,
            )

    # ---- rate_limit --------------------------------------------------------

    def rate_limit_state(self) -> RateLimitInfo:
        # OpenAlex doesn't surface rate-limit headers reliably; conservative
        # placeholders matching the DataCite adapter pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
