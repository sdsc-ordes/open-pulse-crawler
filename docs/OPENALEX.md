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

- **`OpenAlexWork`** — a scholarly work. Canonical key: `https://doi.org/<doi>`
  (lowercased) when the work has a DOI, else the OpenAlex work URL
  `https://openalex.org/W<id>`. **Active** node — `expand` emits citation,
  authorship, venue, and funding edges.
- **`OpenAlexAuthor`** — a researcher. Canonical key:
  `https://orcid.org/<id>` when an ORCID is present, else
  `https://openalex.org/A<id>`. **Passive-ish** — `expand` fans out to the
  author's works (capped, see below).
- **`OpenAlexInstitution`** — an organization. Canonical key:
  `https://ror.org/<id>` when a ROR is present, else
  `https://openalex.org/I<id>`. `expand` fans out to the institution's
  works (capped).
- **`OpenAlexSource`** — a venue (journal, repository, conference).
  Canonical key: `https://openalex.org/S<id>` (the ISSN-L is retained as a
  field on the node but is not used as the key). **Passive** — `expand`
  emits no edges.
- **`OpenAlexFunder`** — a funding body. Canonical key:
  `https://doi.org/10.13039/<id>` (the Crossref Funder Registry DOI prefix)
  when present, else `https://openalex.org/F<id>`. **Passive** — `expand`
  emits no edges.

The `10.13039` Funder-Registry prefix is what disambiguates a `doi.org`
URL between a Work (any other prefix) and a Funder.

## Edge kinds

| Source | Kind | Target |
|---|---|---|
| `OpenAlexWork` | `references` | Work (doi.org else openalex W URL) |
| `OpenAlexWork` | `cited_by` | Work (doi.org else openalex W URL), **capped** |
| `OpenAlexWork` | `authored_by` | Author (orcid.org URL) |
| `OpenAlexWork` | `published_in` | Source venue URL |
| `OpenAlexWork` | `funded_by` | Funder URL |
| `OpenAlexAuthor` (anchor) | `affiliated_with` | Institution (ror.org URL) |
| `OpenAlexAuthor` | `authored` | Work (doi.org else openalex W URL), **capped** |
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

## Seed forms accepted

- External identifiers: `https://doi.org/<DOI>`, `https://orcid.org/<id>`,
  `https://ror.org/<id>`.
- Native OpenAlex: `https://openalex.org/<W|A|I|S|F><id>` and the API form
  `https://api.openalex.org/<works|authors|institutions|sources|funders>/<id>`
  (rewritten to the `https://openalex.org/<ID>` canonical form).

## Manual-test recipes

### Single work, bidirectional citations

```bash
opc crawl --platforms openalex.org --rounds 2 \
    https://doi.org/10.1371/journal.pone.0000308
```

Round 0 fetches the Work; round 1 walks both `references` (outbound) and
`cited_by` (inbound, capped at 50) to neighboring works.

### All works by an ORCID author

```bash
opc crawl --platforms openalex.org --rounds 2 \
    https://orcid.org/0000-0002-1825-0097
```

Round 1 fans out up to `max_works_per_entity` (default 25) of the author's
works via `authored` edges.

### Cross-platform with DataCite fallback

```bash
CRAWLER_PLATFORMS=openalex.org,datacite.org \
    opc crawl --rounds 2 \
    https://ror.org/02s376052
```

OpenAlex resolves the institution and its works first; any DOI / ORCID /
ROR node that misses in OpenAlex falls back to the DataCite adapter.

## Out of scope

- **Concepts, Topics, Publishers, and Geo entities.** OpenAlex exposes
  these, but the adapter covers only the five entities above.
- **Code-layer / software discovery.** OpenAlex does not link to source-code
  repositories; the GitHub adapter handles that layer.
- **No authenticated tier.** All reads are anonymous; the
  `CRAWLER_OPENALEX_MAILTO` email is a polite-pool hint, not a token.
- **No deposit / submit flows.** Read-only.
