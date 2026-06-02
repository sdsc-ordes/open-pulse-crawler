# Spec — Crossref enrichment pass (Spec 6)

**Status:** draft / awaiting review
**Branch base:** develop
**Author:** Carlos Vivar Rios

## Problem

Any DOI that is **not** registered with DataCite (journal articles / preprints —
Nature, Elsevier, etc., which are registered with **Crossref**) ends up in the
graph as a *bare* node: just the canonical `https://doi.org/{doi}` URL as the
key, with **no** title, authors, date or journal. This happens today because
`platforms/datacite.py:50` normalizes any unknown-prefix DOI to
`https://doi.org/{doi}`, and when the DataCite adapter cannot resolve it the
fetch returns `None` (datacite.py:40/73/124…), leaving the node unenriched.

Measured: a Zenodo/HF record that cites the AlphaFold paper
(`10.1038/s41586-021-03819-2`, RA = **Crossref**) leaves a bare node. Crossref
holds full metadata for it (34 authors, 9 ORCID, 84 references, journal, funders).

## Goal

Use Crossref as an **enrichment fallback** over already-crawled graph data —
**not** a new crawl-discovery source. This dissolves the DOI-routing conflict
with the DataCite adapter: we never decide Crossref-vs-DataCite at discovery
time; DataCite stays the primary resolver and Crossref only touches the
`https://doi.org/...` nodes DataCite left bare.

```
DOI discovered → DataCite (primary, unchanged)
                  ├─ resolves → Work node populated  ✓
                  └─ None      → bare doi.org node    ──►  Crossref enricher
```

## Scope — two sequenced phases (decided)

### Phase 1 — Enrich (materialize the dangling DOI as a node)
For each **target DOI** (see selection below), call Crossref `/works/{doi}` and
materialize a **new `CrossrefWork` node** in `graph.repos` (keyed by the
canonical `https://doi.org/{doi}` URL), populated with title, authors + ORCID,
publication year, container-title/journal, publisher, type, funders,
`is_referenced_by_count`. No edges are added in this phase.

**Model:** `CrossrefWork(RepoModel)` is a **new subkind** (mirroring the shape
of `DataCiteWork`, models.py:496) — NOT a reuse of the `DataCiteWork` subkind,
because subkinds are platform-specific (`DataCiteWork`, `ZenodoRecord`,
`InfoscienceItem` …). It MUST be registered in the `RepoNode` discriminated
union (models.py:661) so snapshots round-trip.

### Phase 2 — Expand (opt-in, bounded)
From each enriched work, take the `reference` list (outbound DOIs) and:
- emit a `references` edge from the work to each referenced DOI, and
- queue each referenced `https://doi.org/{doi}` as a new node so it is itself
  enriched on the next pass.

Phase 2 is gated by `expand` (default **on**, per decision "enrich y luego
expand") and bounded by `max_expand_depth` (default **1**) and
`max_references_per_work` to prevent unbounded recursion (84 DOIs/paper × deep
recursion would explode). Any truncation is `log()`-ged — no silent caps.

## Target selection (idempotent)

Because a DataCite miss creates **no node** (`DataCiteAdapter.fetch` →
`None`, adapter.py:145), Crossref-registered DOIs exist only as *dangling
strings* inside other nodes' edge lists (e.g. a `DataCiteWork.relations` entry
synthesized to `https://doi.org/...`). So selection scans references, not nodes:

A DOI URL is an enrichment target iff **all** hold:
1. it appears as a `https://doi.org/...` value referenced somewhere in the graph
   (scan edge-list/relation string values across all nodes), and
2. it is **not** owned by a sibling rewriter (`is_owned_doi_url` → Zenodo etc.),
   and
3. it is **not already materialized** as a node in `graph.repos`
   (URL key absent).

Condition 3 gives idempotency: once enriched (or already present), it is skipped
on re-runs. Crossref `/works/{doi}` returning **404** (DOI not Crossref-owned)
also skips cleanly — counted as `skipped_404`, no node created.

## Configuration

- **`CRAWLER_CROSSREF_MAILTO`** (new env var, `CRAWLER_` prefix per `config.py`
  convention) — email for the Crossref **polite pool**. Sent as the `mailto`
  query param and in the `User-Agent`
  (`OpenPulseCrawler/<ver> (+https://openpulse.science; mailto:<addr>)`).
  - If **unset**: the enricher still works (public pool) but logs a one-time
    warning recommending it be set for better/consistent rate limits.
  - Added to `.env.dist` with documentation.
- Enricher options (constructor args): `expand: bool = True`,
  `max_expand_depth: int = 1`, `max_references_per_work: int | None = None`.

## Architecture / new code

- **`platforms/crossref.py`** — passive, anonymous `CrossrefClient` mirroring the
  shape of the DataCite client:
  - `fetch_work(doi: str) -> CrossrefWork | None` — GET `api.crossref.org/works/{doi}`
    with polite-pool headers; returns `None` on 404 (DOI not Crossref-owned) so
    the enricher cleanly skips non-Crossref bare DOIs.
  - Respects `Retry-After` on HTTP 429; polite `User-Agent`.
  - A small mapper from the Crossref `message` JSON → `CrossrefWork` fields.
- **`CrossrefEnricher`** — runs as a **post-pass over a graph snapshot**
  (URL-keyed dict, matching the v2 export shape). `enrich(graph) -> graph`:
  selects target nodes, runs Phase 1, then (if `expand`) Phase 2, returning the
  augmented graph + a summary (counts: enriched, skipped-404, queued-references,
  truncated).
- **Routing unchanged.** No edits to `_DOI_PREFIX_REWRITERS` or the DataCite
  adapter. The enricher is additive.

## Tests (TDD — write first)

- `CrossrefClient.fetch_work` parses an AlphaFold fixture → title / 34 authors /
  9 ORCID / 84 references / journal; returns `None` on a 404 fixture.
- mailto: env set → `mailto` param + UA present on the request; env unset →
  warning logged once, request still issued.
- Enricher Phase 1: bare `https://doi.org/10.1038/...` node + Crossref hit →
  Work fields populated; a Zenodo doi.org node (`is_owned_doi_url`) → skipped;
  an already-enriched node → skipped (idempotency).
- Enricher Phase 2: enriched work with references → N `references` edges + N new
  bare doi.org nodes queued; `max_references_per_work` truncates + logs; depth
  bound stops recursion at `max_expand_depth`.

## Out of scope (explicit)

- Crossref as a BFS crawl-discovery source / host-keyed adapter.
- Citation **cited-by** direction (the 41k inbound citations) — that is
  OpenAlex/OpenCitations territory, separate spec.
- Event Data (DOI↔GitHub software bridge) — separate spec.

## Resolved anchors (confirmed against the code)

- The graph is the `GraphData` pydantic model (models.py:669): URL-keyed dicts
  `users` / `orgs` / `repos` / `teams`. Works are `RepoModel` subkinds → live in
  `graph.repos`. `CrossrefWork` nodes are added via `graph.add_repo(...)`.
- `CrossrefWork` mirrors `DataCiteWork` (models.py:496–520): `doi`, `title`,
  `publication_year`, `publisher`, `container_title`, `creators`, `abstract`,
  `subjects`, `relations`, plus `is_referenced_by_count: int` and
  `reference_dois: List[str]` for Phase 2.
- Reuse the DataCite client's 429/`Retry-After` retry pattern
  (datacite_adapter/client.py:86–108) and anonymous-warn shape.
- `references` edge in Phase 2 is a new edge-list field on `CrossrefWork`
  holding canonical `https://doi.org/...` target URLs (host-aware, consistent
  with the URL-keyed edge convention).
