# Crossref enrichment

Open Pulse Crawler ships a **Crossref enrichment pass** — a post-crawl
step that materializes journal/article DOIs which DataCite cannot resolve.
It is not a crawl-time adapter; it runs over an already-crawled graph
snapshot via the `enrich-crossref` CLI command. Anonymous reads work
without a token.

## Why this exists (DataCite-miss fallback)

DataCite only indexes DOIs registered through DataCite (datasets,
software, theses — Figshare, Dryad, Zenodo, etc.). **Crossref-issued
DOIs** — journal articles and preprints from Nature, ACM, IEEE,
Elsevier, and friends — are *not* in DataCite's index and 404 against
`/dois/<doi>`.

During a crawl, when the DataCite adapter cannot resolve a referenced
DOI it returns `None` and **no node is created**. The DOI survives only
as a *dangling string* `https://doi.org/...` nested inside another
node's edge/relation data (e.g. a `DataCiteWork.relations` entry, a
`references` list). These journal/article DOIs would otherwise remain
bare, unmaterialized `https://doi.org/...` references forever.

The enrichment pass scans the graph for those dangling doi.org URLs and
asks Crossref to materialize the ones it owns, turning them into
first-class `CrossrefWork` nodes with title, publisher, year,
container, authors, subjects, and reference list.

DOIs owned by a sibling adapter (e.g. Zenodo prefixes) are **excluded** —
see `is_owned_doi_url` in `platforms/datacite.py`. Those route through
their own adapter at crawl time.

## Polite pool — `CRAWLER_CROSSREF_MAILTO`

Crossref runs a faster, dedicated "polite pool" for clients that
identify themselves. Set a contact email so the server can reach you in
case of abuse:

```bash
export CRAWLER_CROSSREF_MAILTO=you@example.org
```

When set (or passed via `--mailto`), requests include `mailto=<addr>`
as a query parameter and an enriched `User-Agent` header, routing to the
polite pool. Without it, the public pool is used and a one-time warning
is logged. No token is required either way.

Resolution order for the mailto: `--mailto` flag → `CRAWLER_CROSSREF_MAILTO`
→ none (public pool).

## CLI usage — `enrich-crossref`

```bash
opc enrich-crossref --input output/20250601.graph.json
```

By default this rewrites the snapshot **in place**. Use `--output` to
write elsewhere:

```bash
opc enrich-crossref \
    --input  output/20250601.graph.json \
    --output output/20250601.enriched.json \
    --mailto you@example.org
```

### Options

| Option | Default | Meaning |
|---|---|---|
| `--input` / `-i` | *(required)* | Crawled graph snapshot JSON (as written by `export_to_json`). |
| `--output` / `-o` | `--input` | Where to write the enriched snapshot (in place by default). |
| `--expand` / `--no-expand` | `--expand` | Phase 2: also materialize works referenced by enriched works. |
| `--max-expand-depth` | `1` | Max reference-expansion depth (only with `--expand`). |
| `--max-references-per-work` | `None` | Cap references expanded per work (`None` = no cap). |
| `--mailto` | `$CRAWLER_CROSSREF_MAILTO` | Crossref polite-pool email. |

On completion it prints a summary table: `enriched`, `skipped_404`,
`skipped_owned`, `skipped_already_present`, `references_expanded`,
`references_truncated`, `max_depth_reached`, and the output path.

## Two phases

1. **Enrich.** Scan every node for dangling `https://doi.org/...`
   strings. For each eligible URL (not owned by a sibling adapter, not
   already a node), fetch it from Crossref. A hit becomes a
   `CrossrefWork` node; a 404 is counted as `skipped_404` and dropped.

2. **Bounded expand** (when `--expand`). Each newly materialized work
   exposes a `references` list of cited doi.org URLs. The pass walks
   those references outward, breadth-first, up to `--max-expand-depth`
   levels. `--max-references-per-work` caps how many references are
   expanded per work (the rest are counted as `references_truncated`).

Both bounds exist to keep the fan-out finite: a single highly-cited
article can reference hundreds of works, each of which references
hundreds more. `--max-expand-depth 1` (the default) materializes the
direct references of the dangling DOIs and stops.

## Idempotency

The pass is safe to re-run. Eligibility skips any URL already present as
a node (`skipped_already_present`), and a per-run `visited` set prevents
re-fetching the same DOI or cycling through mutual citations. Running
`enrich-crossref` twice on the same snapshot enriches nothing the second
time.

## Out of scope

- **Cited-by / inbound citations.** Crossref's `is-referenced-by-count`
  is stored on the node, but the ~41k inbound citation *edges* (who
  cites this work) are **not** materialized here. Inbound citation
  graphs belong to OpenAlex / OpenCitations, not this pass — which only
  follows the outbound `references` direction.
- **The DOI ↔ GitHub software bridge.** Linking a paper's DOI to the
  GitHub repository implementing it is **Crossref Event Data**
  territory, not the metadata `/works/<doi>` endpoint this pass uses.
- **DataCite-owned DOIs.** Datasets/software registered with DataCite are
  handled by the DataCite adapter at crawl time and are explicitly
  skipped here (`skipped_owned`). See [`DATACITE.md`](DATACITE.md).
