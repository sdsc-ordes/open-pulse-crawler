"""Pydantic models for graph entities.

Nodes (user, org, repo, team) are keyed by their canonical public URL
in :class:`GraphData` — see :mod:`open_pulse_crawler.node_id`. Each
model keeps the platform-native shorthand (``login`` for user/org,
``full_name`` for repo/team) alongside the canonical ``url`` so display
layers, API callers, and the crawler can still pull the bare identifier
when they need it.

Edge-list fields (``followers``, ``contributors``, ``dependencies``,
``members``, ...) keep storing the bare platform shorthand (logins or
``owner/repo`` strings) rather than URLs — this is a pragmatic choice
to keep the internal representation compact. The boundary that produces
the public CSV / JSON-LD output converts these to URLs at write time so
consumers join on a uniform key.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from .node_id import (
    extract_full_name,
    extract_login,
    extract_team_parts,
    repo_url as _repo_url,
    team_url as _team_url,
    user_url as _user_url,
)


class GitHubItemType(str, Enum):
    """Types of GitHub entities."""

    USER = "User"
    ORGANIZATION = "Organization"
    REPOSITORY = "Repository"
    TEAM = "Team"
    BOT = "Bot"
    UNKNOWN = "Unknown"


class ExternalIdentifier(BaseModel):
    """A typed external identifier (e.g. ORCID, ROR) attached to a node."""

    scheme: str
    value: str


class BaseEntityModel(BaseModel):
    """Base model for all graph entities."""

    is_explored: bool = False
    exploration_timestamp: Optional[str] = None
    # Canonical platform identifier. Defaults to "github" until the
    # GitLab adapter lands; per-host dispatch flips this on construction.
    platform: str = "github"
    # Typed cross-references to other identifier systems (ORCID, ROR, ...).
    external_identifiers: List[ExternalIdentifier] = Field(default_factory=list)
    # Free-form bag for platform-specific or experimental fields that don't
    # warrant a first-class column yet.
    extras: Dict[str, Any] = Field(default_factory=dict)


class UserModel(BaseEntityModel):
    """A user on the source platform."""

    subkind: Literal["GitHubUser"] = "GitHubUser"
    # Canonical URL — primary key in GraphData.users.
    url: str
    # Platform-native login, kept alongside the URL for display + API calls.
    login: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.USER

    # Repos the user owns (full_name strings).
    authored_repositories: List[str] = Field(default_factory=list)
    # Repos the user has forked (full_name strings).
    forked_repositories: List[str] = Field(default_factory=list)

    # Users who follow this user (login strings).
    followers: List[str] = Field(default_factory=list)
    # Users this user follows (login strings).
    following: List[str] = Field(default_factory=list)

    # Repos the user has starred (full_name strings).
    starred_repositories: List[str] = Field(default_factory=list)
    # Repos the user is subscribed to (full_name strings).
    watched_repositories: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _fill_url_and_login(cls, data):
        """Allow callers to provide login XOR url; fill the other in.

        Constructing a UserModel with only ``login`` (the legacy shape) is
        still supported — ``url`` is derived. Constructing with only
        ``url`` derives the login by parsing the URL. If both are given
        we trust the caller and do not cross-check.
        """
        if not isinstance(data, dict):
            return data
        has_url = bool(data.get("url"))
        has_login = bool(data.get("login"))
        if has_url and not has_login:
            data["login"] = extract_login(data["url"])
        elif has_login and not has_url:
            data["url"] = _user_url(data["login"])
        return data


class OrgModel(BaseEntityModel):
    """An organization on the source platform."""

    subkind: Literal["GitHubOrganization"] = "GitHubOrganization"
    url: str
    login: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.ORGANIZATION
    members: List[str] = Field(default_factory=list)  # login strings

    # Org-owned repos that are original (full_name strings).
    authored_repositories: List[str] = Field(default_factory=list)
    # Org-owned repos that are forks (full_name strings).
    forked_repositories: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _fill_url_and_login(cls, data):
        if not isinstance(data, dict):
            return data
        has_url = bool(data.get("url"))
        has_login = bool(data.get("login"))
        if has_url and not has_login:
            data["login"] = extract_login(data["url"])
        elif has_login and not has_url:
            data["url"] = _user_url(data["login"])
        return data


class RepoModel(BaseEntityModel):
    """A repository on the source platform."""

    subkind: Literal["GitHubRepository"] = "GitHubRepository"
    url: str
    full_name: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.REPOSITORY
    contributors: List[str] = Field(default_factory=list)  # login strings
    # Owner login (kept for display + API calls). The owner's URL is the
    # parent of ``self.url``; callers can build it via node_id.user_url
    # if they need it.
    owner: str = ""

    # Fork information
    is_fork: bool = False
    # ``owner/repo`` of the upstream repo this is a fork of; None for non-forks.
    forked_from: Optional[str] = None

    # Dependency information (full_name strings).
    dependents: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)

    # Issue / PR activity edges (login strings).
    issue_authors: List[str] = Field(default_factory=list)
    pr_authors: List[str] = Field(default_factory=list)
    # Conversation commenters across issues and PRs (both fetched via the issues API).
    commenters: List[str] = Field(default_factory=list)
    # Formal review submitters on PRs.
    pr_reviewers: List[str] = Field(default_factory=list)

    # Total contributor count reported by GitHub (metadata). Captured the
    # first time the repo is fetched and persisted in the cache. ``None``
    # means we don't have a count yet. ``contributors`` above holds them
    # all by default; it is shorter than this only when ``max_contributors``
    # caps it.
    contributor_count: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _fill_url_and_full_name(cls, data):
        if not isinstance(data, dict):
            return data
        has_url = bool(data.get("url"))
        has_full = bool(data.get("full_name"))
        if has_url and not has_full:
            data["full_name"] = extract_full_name(data["url"])
        elif has_full and not has_url:
            data["url"] = _repo_url(data["full_name"])
        # Owner is convenient and cheap to derive from full_name.
        if not data.get("owner") and data.get("full_name"):
            data["owner"] = data["full_name"].split("/", 1)[0]
        return data


class TeamModel(BaseEntityModel):
    """A team within an organization on the source platform."""

    subkind: Literal["GitHubTeam"] = "GitHubTeam"
    url: str
    # full_name is "org_login/team_slug" — kept for display / log lines.
    full_name: str
    slug: str = ""
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.TEAM
    # Org login (kept for display). The org's URL can be built via
    # node_id.user_url(self.org) when needed.
    org: str = ""
    description: str = ""
    privacy: str = ""

    # ``org/slug`` of the parent team when this is a child team.
    parent: Optional[str] = None

    members: List[str] = Field(default_factory=list)  # login strings
    repositories: List[str] = Field(default_factory=list)  # full_name strings

    @model_validator(mode="before")
    @classmethod
    def _fill_url_and_full_name(cls, data):
        if not isinstance(data, dict):
            return data
        has_url = bool(data.get("url"))
        has_full = bool(data.get("full_name"))
        has_slug = bool(data.get("slug"))
        has_org = bool(data.get("org"))
        if has_url and not has_full:
            org, slug = extract_team_parts(data["url"])
            data["full_name"] = f"{org}/{slug}"
            data.setdefault("org", org)
            data.setdefault("slug", slug)
        elif has_full and not has_url:
            org, slug = data["full_name"].split("/", 1)
            data["url"] = _team_url(org, slug)
            data.setdefault("org", org)
            data.setdefault("slug", slug)
        elif has_org and has_slug and not has_url:
            data["url"] = _team_url(data["org"], data["slug"])
            data["full_name"] = f"{data['org']}/{data['slug']}"
        return data


# --- GitLab subclasses ------------------------------------------------------
#
# These inherit every GitHub-shaped field and override ``subkind`` so the
# Pydantic v2 discriminated union below can pick the right concrete class
# when deserializing a mixed-platform GraphData snapshot.


class GitLabUserModel(UserModel):
    """A user on a GitLab instance."""

    subkind: Literal["GitLabUser"] = "GitLabUser"
    state: Literal["active", "blocked", "deactivated"] = "active"
    public_email: str = ""


class GitLabGroupModel(OrgModel):
    """A group on a GitLab instance (the GitLab analogue of a GitHub org)."""

    subkind: Literal["GitLabGroup"] = "GitLabGroup"
    # URL of parent group when this is a subgroup; None for top-level groups.
    parent: Optional[str] = None
    visibility: Literal["private", "internal", "public"] = "public"


class GitLabProjectModel(RepoModel):
    """A project on a GitLab instance (the GitLab analogue of a GitHub repo)."""

    subkind: Literal["GitLabProject"] = "GitLabProject"
    visibility: Literal["private", "internal", "public"] = "public"
    # Owner namespace URL (a user or group); None when not yet resolved.
    namespace: Optional[str] = None


# --- Zenodo subclasses ------------------------------------------------------
#
# Zenodo is a research-data archive (DOI-keyed records, communities,
# uploader accounts). It has no social graph, so the inherited follower /
# following / star / watch lists stay empty by construction. See
# ``docs/superpowers/specs/2026-05-28-zenodo-adapter-design.md`` for the
# full model contract.


class ZenodoUserModel(UserModel):
    """A Zenodo platform account (uploader).

    Distinct from creators — creators are author names recorded on records
    (``ZenodoRecordModel.creators``), not Zenodo accounts. Cross-platform
    identity resolution is intentionally out of scope (handled by another
    tool downstream), so we model only the platform-internal account here.

    Social fields (``followers``, ``following``, ``starred_repositories``,
    ``watched_repositories``) inherited from ``UserModel`` are unused —
    Zenodo has no social graph. They stay empty by construction.
    """
    subkind: Literal["ZenodoUser"] = "ZenodoUser"
    orcid: Optional[str] = None
    affiliation: str = ""


class ZenodoCommunityModel(OrgModel):
    """A Zenodo community.

    ``login`` (inherited from ``OrgModel``) is the community slug. URL form:
    ``https://zenodo.org/communities/<slug>``. ``members`` is intentionally
    not populated — the member-listing endpoint is auth-gated on production
    Zenodo and out of scope for this adapter.
    """
    subkind: Literal["ZenodoCommunity"] = "ZenodoCommunity"
    doi: Optional[str] = None
    description: str = ""
    community_type: str = ""   # "project" | "organization" | "event" | "topic"


class ZenodoRecordModel(RepoModel):
    """A Zenodo record. One node per concept DOI; versions collapse into
    ``versions: list[dict]`` metadata.

    The concept DOI is the primary identity. When a record has no versioning,
    ``concept_doi == doi`` (self-reference) and ``versions`` is empty. When
    a record has versions, ``doi == concept_doi`` (the node represents the
    lineage, not a specific version), ``latest_version_*`` describes the
    latest, and ``versions`` carries every version's metadata.

    ``creators`` is a list of dicts ``{name, orcid, affiliation, type}`` —
    not crawled to User nodes. Identity resolution is downstream of this
    adapter.
    """
    subkind: Literal["ZenodoRecord"] = "ZenodoRecord"
    doi: str
    concept_doi: str
    latest_version: str = ""
    latest_version_doi: str = ""
    latest_version_url: str = ""
    versions: List[Dict[str, Any]] = Field(default_factory=list)
    resource_type: str = ""
    publication_date: str = ""
    title: str = ""
    creators: List[Dict[str, Any]] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    license: str = ""
    access_right: Literal["open", "embargoed", "restricted", "closed"] = "open"


# --- Infoscience subclasses -------------------------------------------------
#
# Infoscience is EPFL's institutional DSpace-CRIS repository. It has three
# entity types that map onto the existing base models: Person → UserModel,
# OrgUnit → OrgModel, Item → RepoModel. Social graph fields inherited from
# the base models stay empty — DSpace has no follower / star / fork concept.


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
    ``members`` (inherited from ``OrgModel``) stays empty — Infoscience has
    no direct Person→OrgUnit index so member enumeration is not possible.
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
    affiliations: List[Dict[str, Any]] = Field(default_factory=list)
    relations: List[Dict[str, Any]] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    language: str = ""
    license: str = ""
    journal: str = ""
    issn: str = ""
    isbn: str = ""


# --- DataCite subclasses ----------------------------------------------------
#
# DataCite is a global DOI registration agency. Its Commons portal exposes
# Works (DOI metadata), Organizations (ROR-identified institutions), Persons
# (ORCID-identified researchers), and Clients (registered data repositories).
# Social graph fields inherited from the base models stay empty — DataCite
# has no follower / star / fork concept.


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


class CrossrefWork(RepoModel):
    """A DOI registered with Crossref (journal article / preprint) materialized
    by the Crossref enrichment pass. Distinct from DataCiteWork: Crossref owns
    article/preprint DOIs, DataCite owns dataset/software DOIs.
    """
    subkind: Literal["CrossrefWork"] = "CrossrefWork"
    doi: str
    title: str = ""
    publication_year: Optional[int] = None
    publisher: str = ""
    container_title: str = ""
    work_type: str = ""
    abstract: str = ""
    creators: List[Dict[str, Any]] = Field(default_factory=list)
    subjects: List[str] = Field(default_factory=list)
    funders: List[Dict[str, Any]] = Field(default_factory=list)
    relations: List[Dict[str, Any]] = Field(default_factory=list)
    is_referenced_by_count: Optional[int] = None
    # Phase-2 expand edge: canonical https://doi.org/... URLs this work cites.
    references: List[str] = Field(default_factory=list)
    reference_dois: List[str] = Field(default_factory=list)


# --- HuggingFace subclasses -------------------------------------------------
#
# HuggingFace is an ML-platform hub for models, datasets, spaces, papers,
# and user-curated collections. The social graph is sparser than GitHub
# (no following/forking on models in the GH sense), but HF papers are a
# strong cross-platform pivot via arxiv IDs and linked-model cross-references.


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


# --- OpenAlex subclasses -------------------------------------------------------
#
# OpenAlex is an open catalogue of scholarly works, authors, institutions,
# sources (journals/repositories), and funders. It integrates DOI, ORCID, ROR,
# and ISSN identifiers, making it the primary cross-platform identity pivot for
# the academic literature graph.


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
    references: List[str] = Field(default_factory=list)      # outbound (raw W-urls until expand resolves them)
    cited_by: List[str] = Field(default_factory=list)        # inbound citations (works that cite this one)
    authored_by: List[str] = Field(default_factory=list)     # orcid/openalex-A urls
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
    """An institution from OpenAlex, keyed by ROR URL when available."""
    subkind: Literal["OpenAlexInstitution"] = "OpenAlexInstitution"
    ror_id: str = ""
    openalex_id: str = ""
    country_code: str = ""
    institution_type: str = ""


class OpenAlexSource(OrgModel):
    """A publication venue (journal/repository) from OpenAlex, keyed by openalex S… (ISSN-L retained)."""
    subkind: Literal["OpenAlexSource"] = "OpenAlexSource"
    openalex_id: str = ""
    issn_l: str = ""
    issns: List[str] = Field(default_factory=list)
    host_organization: str = ""
    is_oa: Optional[bool] = None


class OpenAlexFunder(OrgModel):
    """A funding body from OpenAlex, keyed by its Crossref Funder Registry DOI (10.13039/…) when available."""
    subkind: Literal["OpenAlexFunder"] = "OpenAlexFunder"
    openalex_id: str = ""
    funder_doi: str = ""             # 10.13039/...
    country_code: str = ""


# Schema version bumped when the graph contract changed. v2 added URL-keyed
# nodes; v3 adds the ``subkind`` discriminator + ``extras`` /
# ``external_identifiers`` slots so GitLab subclasses can coexist with the
# GitHub-shaped concrete models in the same dicts.
GRAPH_SCHEMA_VERSION = 3


# Discriminated unions on ``subkind`` let the same dict hold either the
# GitHub concrete class or its GitLab / Zenodo / Infoscience / DataCite /
# HuggingFace / OpenAlex counterpart. Pydantic v2 picks the right class on
# deserialization by reading the literal subkind tag.
UserNode = Annotated[
    Union[UserModel, GitLabUserModel, ZenodoUserModel, InfosciencePerson,
          DataCitePerson, HuggingFaceUser,
          OpenAlexAuthor],
    Field(discriminator="subkind"),
]
OrgNode = Annotated[
    Union[OrgModel, GitLabGroupModel, ZenodoCommunityModel, InfoscienceOrgUnit,
          DataCiteOrganization, DataCiteClient,
          HuggingFaceOrg, HuggingFaceCollection,
          OpenAlexInstitution, OpenAlexSource, OpenAlexFunder],
    Field(discriminator="subkind"),
]
RepoNode = Annotated[
    Union[RepoModel, GitLabProjectModel, ZenodoRecordModel, InfoscienceItem,
          DataCiteWork, CrossrefWork,
          HuggingFaceRepo, HuggingFacePaper,
          OpenAlexWork],
    Field(discriminator="subkind"),
]


class GraphData(BaseModel):
    """Holds references to users, orgs, repos, and teams discovered.

    All dict keys are canonical URLs (see :mod:`open_pulse_crawler.node_id`).
    """

    schema_version: int = GRAPH_SCHEMA_VERSION
    users: Dict[str, UserNode] = Field(default_factory=dict)
    orgs: Dict[str, OrgNode] = Field(default_factory=dict)
    repos: Dict[str, RepoNode] = Field(default_factory=dict)
    teams: Dict[str, TeamModel] = Field(default_factory=dict)

    # --- mutation helpers ----------------------------------------------------

    def add_user(self, user: UserModel) -> None:
        """Add a user to the graph (keyed by its URL)."""
        self.users[user.url] = user

    def add_org(self, org: OrgModel) -> None:
        """Add an organization to the graph (keyed by its URL)."""
        self.orgs[org.url] = org

    def add_repo(self, repo: RepoModel) -> None:
        """Add a repository to the graph (keyed by its URL)."""
        self.repos[repo.url] = repo

    def add_team(self, team: TeamModel) -> None:
        """Add a team to the graph (keyed by its URL)."""
        self.teams[team.url] = team

    # --- membership tests ----------------------------------------------------

    def has_user(self, url: str) -> bool:
        return url in self.users

    def has_org(self, url: str) -> bool:
        return url in self.orgs

    def has_repo(self, url: str) -> bool:
        return url in self.repos

    def has_team(self, url: str) -> bool:
        return url in self.teams

    # --- lookups -------------------------------------------------------------

    def get_user(self, url: str) -> Optional[UserModel]:
        return self.users.get(url)

    def get_org(self, url: str) -> Optional[OrgModel]:
        return self.orgs.get(url)

    def get_repo(self, url: str) -> Optional[RepoModel]:
        return self.repos.get(url)

    def get_team(self, url: str) -> Optional[TeamModel]:
        return self.teams.get(url)

    # --- multi-platform convenience accessors --------------------------------

    def of_subkind(self, subkind: str):
        """Yield every node across all dicts whose ``subkind`` matches."""
        for d in (self.users, self.orgs, self.repos, self.teams):
            for n in d.values():
                if n.subkind == subkind:
                    yield n

    def by_platform(self, platform: str):
        """Yield every node across all dicts on the given platform."""
        for d in (self.users, self.orgs, self.repos, self.teams):
            for n in d.values():
                if n.platform == platform:
                    yield n

    def by_instance(self, instance_host: str):
        """Yield every node whose URL host matches ``instance_host``.

        ``instance`` is not yet a first-class field on Node — defer that to
        the task that introduces multi-instance routing. For now we derive
        the host from the canonical URL.
        """
        from urllib.parse import urlparse

        for d in (self.users, self.orgs, self.repos, self.teams):
            for n in d.values():
                if urlparse(n.url).netloc.lower() == instance_host:
                    yield n
