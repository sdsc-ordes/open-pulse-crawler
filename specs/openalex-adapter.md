# Spec 7 — OpenAlex platform adapter

**Status:** draft / awaiting review
**Date:** 2026-06-02
**Branch base:** develop (working on `feat/multi-platform-gitlab`)
**Author:** Carlos Vivar Rios

## Goal

Add OpenAlex as a **first-class host-keyed crawl adapter** (like DataCite /
HuggingFace), seedable by OpenAlex ID / DOI / ORCID / ROR and participating in
live BFS discovery. The unique value over the existing Crossref + DataCite DOI
resolution is OpenAlex's **bidirectional citation graph** — both
`referenced_works` (outbound) and inbound `cited_by` (forward, via the `cites:`
filter), which nothing else in the crawler provides — plus ORCID authors, ROR
institutions, venues, and funders.

## Decisions (from brainstorming)

1. **Integration shape:** full host-keyed adapter (not an enricher post-pass).
2. **Entity scope:** 5 entity types — Works, Authors, Institutions, Sources,
   Funders.
3. **Citation traversal:** both directions; inbound `cited_by` **capped**
   (`max_citations_per_work`).
4. **Keying:** canonical-ID unification — canonical external URL as the dedup
   key, with **all alternate IDs retained** as connectors (dual storage).
5. **Precedence:** **OpenAlex-first** on the shared hosts (`doi.org`,
   `orcid.org`, `ror.org`); **DataCite is fallback-on-miss**; Crossref enricher
   remains the final metadata fallback.

## Entities, subkinds, keys

5 new subkinds in `models.py`, mirroring DataCite's per-platform pattern, each
registered into the correct discriminated union:

| Subkind | Base | Node union | Canonical key (else fallback) |
|---|---|---|---|
| `OpenAlexWork` | `RepoModel` | `RepoNode` | `https://doi.org/{doi}` else `https://openalex.org/W…` |
| `OpenAlexAuthor` | `UserModel` | `UserNode` | `https://orcid.org/{orcid}` else `https://openalex.org/A…` |
| `OpenAlexInstitution` | `OrgModel` | `OrgNode` | `https://ror.org/{ror}` else `https://openalex.org/I…` |
| `OpenAlexSource` | `OrgModel` | `OrgNode` | `https://openalex.org/S…` (ISSN-L retained as field) |
| `OpenAlexFunder` | `OrgModel` | `OrgNode` | `https://doi.org/10.13039/{id}` (Funder Registry) else `https://openalex.org/F…` |

**Alternate IDs are always preserved** (the canonical URL is only the merge
key). Stored via the existing `external_identifiers` mechanism + typed fields:
- Work: `openalex` (W…), `mag`, `pmid`, `pmcid`, `wikidata` + scalars
  (`title`, `publication_year`, `work_type`, `cited_by_count`, `is_oa`).
- Author: `openalex` (A…), `scopus`, `twitter`, `wikipedia`, display name,
  last-known institution.
- Institution: `openalex` (I…), `grid`, `wikidata`, `mag`, country, type.
- Source: `openalex` (S…), `issn_l`, `issn[]`, `wikidata`, `fatcat`,
  host org, is_oa.
- Funder: `openalex` (F…), `ror`, `wikidata`, country.

## Edges & traversal

**Works are the active crawl frontier; Authors / Institutions / Sources /
Funders are passive anchors** (recorded with IDs + incoming edges, not
independently expanded — same philosophy as DataCite's bare anchors).

Per `OpenAlexWork`, `expand` emits:

| Edge field | Direction | Target key | Bound |
|---|---|---|---|
| `references` | outbound (backward) | referenced_works → canonical URLs | full |
| `cited_by` | **inbound (forward, NEW)** | `cites:` filter results | `max_citations_per_work` (default 50, most-recent first; logged on truncation) |
| `authored_by` | Work → Author | ORCID / openalex A… | full |
| `affiliated_with` | Author → Institution | ROR / openalex I… | full |
| `published_in` | Work → Source | ISSN-L / openalex S… | one |
| `funded_by` | Work → Funder | Funder-Registry DOI / openalex F… | full |

**Seed-vs-reached behavior for Author/Institution:** passive when *reached* via
an edge; when used as a **crawl seed** they expand to a capped slice of their
works (`max_works_per_entity`, default 25) so seeding by an ORCID/ROR is useful.
Sources/Funders are always passive.

**Knobs** (logged when they truncate, like `max_dependents` /
`max_references_per_work`): `max_citations_per_work` (forward fan-out),
`max_works_per_entity` (author/institution seed fan-out).

## Architecture — new module `platforms/openalex_adapter/`

Mirrors `platforms/datacite_adapter/`.

### `client.py` — `OpenAlexHTTPClient`
- Anonymous; polite pool via new env var **`CRAWLER_OPENALEX_MAILTO`**
  (consistent with `CRAWLER_CROSSREF_MAILTO`; sent as `mailto` param + in
  `User-Agent`; one-time warning if unset).
- 429/`Retry-After` retry-once (reuse the DataCite client pattern, cap 60s).
- Methods: `get_work/author/institution/source/funder` (accept `W…`/`doi:`/
  `orcid:`/`ror:`/full-URL id forms), `iter_citing_works(work_id, cap)`
  (cursor-paginated `filter=cites:{id}`), `iter_works_by_entity(id, cap)`
  (`filter=author.id:` / `institutions.id:`). 404 → `None`.

### `adapter.py` — `OpenAlexAdapter(PlatformAdapter)`
- `register_hosts`: `api.openalex.org`, `openalex.org`, **`doi.org`,
  `orcid.org`, `ror.org`** (becomes registered owner of the shared hosts).
- `normalize_uri(raw)`: canonicalize every accepted form to the canonical key.
- `kind_of(uri)`: classify by host/path → work / author / institution /
  source / funder. Note the `doi.org` host is polysemous: a `10.13039/…` prefix
  (Crossref Funder Registry) classifies as **Funder**, every other DOI prefix as
  **Work**. `orcid.org` → Author, `ror.org` → Institution,
  `openalex.org/{W,A,I,S,F}` → by letter.
- `fetch(uri)`: route to the client, build the right subkind keyed by canonical
  URL. **On OpenAlex miss (404), delegate to `self.fallback_adapter`
  (DataCite).**
- `expand(node)`: emit the edges above, honoring caps + logging.

### Precedence wiring (the one core-touching part)
The OpenAlex adapter holds a **`fallback_adapter`** reference (the DataCite
adapter). On a `fetch` miss it delegates. This preserves the registry's
one-host-one-adapter rule (OpenAlex owns the shared hosts) while keeping
DataCite reachable. The registry builder constructs DataCite, then constructs
OpenAlex with DataCite as fallback, and registers OpenAlex's hosts last so it
wins the shared hosts. DataCite keeps sole ownership of `api.datacite.org` /
`commons.datacite.org`. The Crossref enricher (Spec 6) is unchanged and remains
the final metadata fallback (post-pass).

### Config / enablement / doctor
- `CRAWLER_OPENALEX_MAILTO` documented in `.env.dist`.
- `openalex.org` addable to `CRAWLER_PLATFORMS`.
- `doctor` reports OpenAlex as ANONYMOUS-capable (no token required), like
  DataCite.

## Data flow

seed (DOI / ORCID / ROR / openalex ID) → `normalize_uri` → `kind_of` →
`fetch` (OpenAlex; DataCite fallback on miss) → build subkind (canonical key,
alt-IDs retained) → `expand` (queue edge targets as canonical URLs) → BFS
materializes them in later rounds (Work targets active; anchor targets passive).

## Error handling
- 404 → `None` → fallback adapter → bare/skip.
- 429 → `Retry-After` retry-once (cap 60s), then raise.
- Cursor pagination for `cites:` / works-by-entity; cap + log truncation.
- Malformed/partial JSON → defensive field access (no raise).

## Testing (TDD throughout)
- **Client:** per-entity fixture parsing; `cites:` pagination + cap; works-by-
  entity; mailto polite pool (param + UA; one-time warn when unset); 429 retry;
  404 → None.
- **Adapter:** `normalize_uri`/`kind_of` across every host form (`doi.org`,
  `orcid.org`, `ror.org`, `openalex.org/{W,A,I,S,F}`, `api.openalex.org`);
  `fetch` → correct subkind keyed by canonical URL; **fallback-to-DataCite on
  OpenAlex miss**; `expand` emits correct edges with canonical targets;
  `max_citations_per_work` + `max_works_per_entity` respected and logged;
  passive-when-reached vs seed-expansion behavior.
- **Models:** 5 subkinds instantiate + `GraphData` union round-trip;
  alternate IDs preserved through dump/reparse.
- **Precedence:** shared hosts resolve OpenAlex-first with DataCite fallback;
  `api.datacite.org`/`commons.datacite.org` ownership unchanged.
- **Integration (mocked):** seed a DOI → Work + references + capped `cited_by` +
  Author/Institution anchors + Source/Funder edges.

## Out of scope
- Crossref/OpenAlex reconciliation of differing counts (indexing variance —
  accept both, OpenAlex is broader).
- OpenAlex Concepts/Topics, Publishers, Geo entities.
- Re-crawling a passive anchor's full output (only capped seed-expansion).
- Code-layer/software discovery (still the separate code-search path).

## Likely task decomposition (for the implementation plan)
1. 5 subkind models + union registration + alt-ID fields.
2. `CRAWLER_OPENALEX_MAILTO` config + `.env.dist` + doctor.
3. `OpenAlexHTTPClient` + entity fixtures.
4. `OpenAlexAdapter` core: `normalize_uri` / `kind_of` / `fetch` + subkind build.
5. Precedence wiring: DataCite fallback + registry builder ordering.
6. `expand` + bidirectional traversal + caps/logging + seed-vs-reached.
7. CLI/enablement integration + `docs/OPENALEX.md` + CHANGELOG.
