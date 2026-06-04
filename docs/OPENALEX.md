# OpenAlex adapter

Open Pulse Crawler supports **OpenAlex** (`api.openalex.org`) — an open
catalog of scholarly works, authors, institutions, sources (venues), and
funders. OpenAlex is the only source in the crawler that resolves a
**bidirectional citation graph**: both a work's outbound `references` and
its inbound `cited_by` edges. It also unifies **ORCID-identified authors**
and **ROR-identified institutions** across platforms — something no other
adapter provides. Anonymous reads work without a token.

## Rationale

DataCite and Crossref resolve a DOI to its metadata and (Crossref) its
outbound references. Neither answers *"who cites this work?"* — the inbound
direction. OpenAlex indexes citations in both directions, so seeding a
single Work lets the BFS walk forward into its references **and** backward
into the works that cite it. Combined with ORCID/ROR unification, OpenAlex
turns a flat DOI list into a connected author / institution / citation
graph. That bidirectional traversal is the reason the adapter exists.

## Supported entities

Five entities, each with a canonical node key:

The canonical key is **derived from the fetched record's own identifiers**,
never from the URL used to reach the entity. So the same Work / Author /
Institution unifies to a single node whether it was seeded by its
DOI / ORCID / ROR or by its OpenAlex id.

- **`OpenAlexWork`** — a scholarly work. Canonical key: `https://doi.org/<doi>`
  (lowercased) when the record carries a DOI, else the OpenAlex work URL
  `https://openalex.org/W<id>`. **Active** node — `expand` emits citation,
  authorship, venue, and funding edges.
- **`OpenAlexAuthor`** — a researcher. Canonical key:
  `https://orcid.org/<id>` when the record carries an ORCID, else
  `https://openalex.org/A<id>`. **Passive-ish** — `expand` fans out to the
  author's works (capped, see below).
- **`OpenAlexInstitution`** — an organization. Canonical key:
  `https://ror.org/<id>` when the record carries a ROR, else
  `https://openalex.org/I<id>`. `expand` fans out to the institution's
  works (capped).
- **`OpenAlexSource`** — a venue (journal, repository, conference).
  Canonical key: `https://openalex.org/S<id>` (the ISSN-L is retained as a
  field on the node but is not used as the key). **Passive** — `expand`
  emits no edges.
- **`OpenAlexFunder`** — a funding body. Canonical key:
  **always** the OpenAlex funder URL `https://openalex.org/F<id>`. The
  Crossref Funder-Registry DOI (`10.13039/<id>`) is retained as the
  `funder_doi` field and an alternate external identifier, but it is **not**
  the node key and **not** a fetchable seed — OpenAlex's funders endpoint
  cannot resolve a registry DOI. Funders are reached via a Work's
  `funded_by` edges (OpenAlex `F` URLs). **Passive** — `expand` emits no
  edges.

The `10.13039` Funder-Registry prefix is what disambiguates an incoming
`doi.org` URL between a Work (any other prefix) and a Funder during
classification; the resulting funder node is still keyed by its `F` id.

## Edge kinds

| Source | Kind | Target |
|---|---|---|
| `OpenAlexWork` | `references` | Work (doi.org else openalex W URL) |
| `OpenAlexWork` | `cited_by` | Work (doi.org else openalex W URL), **capped** |
| `OpenAlexWork` | `authored_by` | Author (orcid.org URL) |
| `OpenAlexWork` (via authorship; edge `src` is the author's orcid.org URL) | `affiliated_with` | Institution (ror.org URL) |
| `OpenAlexWork` | `published_in` | Source venue URL |
| `OpenAlexWork` | `funded_by` | Funder URL |
| `OpenAlexAuthor` (seed expansion) | `authored` | Work (doi.org else openalex W URL), **capped** |
| `OpenAlexInstitution` | `affiliated_work` | Work (doi.org else openalex W URL), **capped** |
| `OpenAlexSource` | *(passive)* | — |
| `OpenAlexFunder` | *(passive)* | — |

`authored_by` / `affiliated_with` are only emitted for creators that carry
an ORCID — without a stable identifier there is no author node to anchor.

## Precedence and fallback

OpenAlex takes **first precedence** on the shared external-identifier hosts
`doi.org`, `orcid.org`, and `ror.org` when both adapters are enabled. The
wiring lives in `cli._build_registry`:

1. **OpenAlex first** — owns `doi.org` / `orcid.org` / `ror.org` (plus its
   native `openalex.org` / `api.openalex.org`).
2. **DataCite fallback** — a Work / Author / Institution that misses in
   OpenAlex delegates to the DataCite adapter (when `datacite.org` is also
   enabled), so the BFS can still resolve a node OpenAlex doesn't index.
   Sources and Funders have no DataCite equivalent — a miss returns `None`.
3. **Crossref enricher** — the final metadata fallback, run as a post-crawl
   pass over the saved snapshot (`enrich-crossref`), materializing
   Crossref-issued DOIs that neither OpenAlex nor DataCite resolved.

## Polite pool

```bash
CRAWLER_PLATFORMS=openalex.org
CRAWLER_OPENALEX_MAILTO=you@example.org
```

`CRAWLER_OPENALEX_MAILTO` routes requests through the OpenAlex **polite
pool**, which grants higher and more consistent rate limits. It is **not**
a token — there is no authenticated tier; the email only identifies the
caller. Optional but strongly recommended for production crawls.

## Traversal caps

Citation and works traversal is bounded to keep crawls finite:

- **`max_citations_per_work`** (default **50**) — caps the inbound
  `cited_by` edges emitted per Work. When a work's `cited_by_count` exceeds
  the cap, the truncation is logged.
- **`max_works_per_entity`** (default **25**) — caps the works fanned out
  from an Author (`authored`) or Institution (`affiliated_work`).

Both caps accept `None` to disable (no limit).

> **Note:** these caps are `ExpandOpts` fields and currently apply at their
> defaults (50 / 25) for CLI crawls — they are not yet exposed as `crawl`
> command flags. To override them today, set them programmatically on the
> crawler's `_expand_opts` (`ExpandOpts(max_citations_per_work=…,
> max_works_per_entity=…)`). Wiring them to dedicated CLI flags is a planned
> follow-up.

## Seed forms accepted

- External identifiers: `https://doi.org/<DOI>`, `https://orcid.org/<id>`,
  `https://ror.org/<id>`.
- Native OpenAlex: `https://openalex.org/<W|A|I|S|F><id>` and the API form
  `https://api.openalex.org/<works|authors|institutions|sources|funders>/<id>`
  (rewritten to the `https://openalex.org/<ID>` canonical form).

## Recipes

Each recipe lists the **command**, the **output** it produces, and **what to
look for**. All are anonymous — set `CRAWLER_OPENALEX_MAILTO` for the polite
pool. Output is trimmed for clarity; counts are from live runs.

### Crawl a paper and its citation neighborhood (Work seed)

```bash
CRAWLER_OPENALEX_MAILTO=you@example.org \
opc crawl --platforms openalex.org --rounds 2 \
    https://doi.org/10.1002/glia.24258 --output-dir ./out
```

```text
✓ Registered adapters for: api.openalex.org, doi.org, openalex.org, orcid.org, ror.org
✓ Added 1 seed nodes
  - Round 0 completed: 1 node processed, 110 in queue
  - Round 1 completed: 110 nodes processed in 36s
  - Total nodes: 111
Exporting results... → ./out/<timestamp>.graph.json
```

The exported graph holds **111 nodes**: 105 `OpenAlexWork` (the paper + its
references + a capped slice of inbound citations), 5 `OpenAlexAuthor`, and
1 `OpenAlexSource` (the venue).

**What to look for:** the work is keyed by `https://doi.org/10.1002/glia.24258`
and its references/citations by their own DOIs; `cited_by` is capped at 50 by
default (a truncation line is logged when a work exceeds it).

> **Round depth for institutions.** From a *Work* seed, institutions are two
> hops away (work → author → institution) and only materialize at `--rounds 3`+.
> Seed an author or institution directly to reach them in two rounds (next).

### Crawl an author's works and affiliations (ORCID seed)

```bash
opc crawl --platforms openalex.org --rounds 2 \
    https://orcid.org/0000-0002-3336-0163 --output-dir ./out
```

```text
  - Crawl completed after 2 rounds (11s)
  - Total nodes: 37
```

**37 nodes**: 1 `OpenAlexAuthor`, 25 `OpenAlexWork` (up to
`max_works_per_entity`), and **11 `OpenAlexInstitution`** — the author's
affiliations, reached in one hop via `affiliated_with`.

**What to look for:** institutions appear here (unlike the Work-seed crawl); the
author is keyed by its ORCID URL and each institution by its `ror.org` URL.

### Cross-platform crawl with DataCite fallback (ROR seed)

```bash
opc crawl --platforms openalex.org,datacite.org --rounds 2 \
    https://ror.org/02s376052 --output-dir ./out
```

OpenAlex owns `doi.org` / `orcid.org` / `ror.org` here and resolves the
institution and its works first; any node OpenAlex doesn't index falls back to
the DataCite adapter.

**What to look for:** the *Registered adapters* line shows OpenAlex owning the
shared hosts, while `api.datacite.org` / `commons.datacite.org` stay with
DataCite (the fallback resolver).

## Out of scope

- **Concepts, Topics, Publishers, and Geo entities.** OpenAlex exposes
  these, but the adapter covers only the five entities above.
- **Code-layer / software discovery.** OpenAlex does not link to source-code
  repositories; the GitHub adapter handles that layer.
- **No authenticated tier.** All reads are anonymous; the
  `CRAWLER_OPENALEX_MAILTO` email is a polite-pool hint, not a token.
- **No deposit / submit flows.** Read-only.

## See also

- [Node identifiers](NODE_IDS.md) — how canonical node keys are formed across platforms.
- [REST API](API.md) — drive crawls programmatically.
- [All platform guides](index.md) — the documentation hub.
