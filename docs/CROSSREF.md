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

## Recipe — enrich a crawled snapshot

`enrich-crossref` runs over a snapshot produced by `opc crawl` and fills in the
**bare `https://doi.org/…` nodes** — journal/article DOIs that were referenced
during the crawl but never resolved, because they belong to Crossref rather than
DataCite or OpenAlex.

```bash
# 1. A crawl that references journal DOIs it doesn't resolve (e.g. a dataset
#    that IsSupplementTo a journal article) leaves them as bare doi.org nodes:
opc crawl --platforms datacite.org --rounds 2 <seed> -o ./out

# 2. Enrich those bare journal DOIs from Crossref (Phase 1 fill + bounded Phase 2):
CRAWLER_CROSSREF_MAILTO=you@example.org \
opc enrich-crossref --input ./out/<timestamp>.graph.json \
    --output ./out/enriched.json --expand --max-references-per-work 5
```

```text
        Crossref enrichment
┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ Metric                  ┃ Value ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ enriched                │ 6     │   ← bare journal DOIs now materialized
│ skipped_404             │ 0     │
│ skipped_owned           │ 0     │
│ skipped_already_present │ 1     │
│ references_expanded     │ 5     │   ← Phase 2 walked each work's references
│ references_truncated    │ 62    │   ← capped by --max-references-per-work 5
│ max_depth_reached       │ 1     │
└─────────────────────────┴───────┘
✓ Wrote enriched snapshot: ./out/enriched.json
```

(Numbers above are from a small representative snapshot with one supplemented
journal DOI.) Omit `--output` to rewrite the snapshot **in place**.

**What to look for:** `enriched` > 0 means bare journal DOIs gained titles,
authors, and references; `skipped_already_present` counts nodes the crawl had
already resolved; `references_truncated` reflects the `--max-references-per-work`
cap. The pass is idempotent — a second run enriches 0 (everything is present).

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
