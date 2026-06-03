# DataCite Commons adapter

Open Pulse Crawler v3.3+ supports **DataCite Commons** —
DOI-identified works from any DataCite-registered repository (Figshare,
Dryad, ETH WSL EnviDat, etc.), ROR-identified organizations, and
ORCID-identified researchers. Anonymous reads work without a token.

## Supported entities

- **`DataCiteWork`** — a DOI registered with DataCite. The `resource_type`
  field carries `resourceTypeGeneral` (Dataset, JournalArticle, Software,
  Text, …); the `resource_type_detail` field carries free-text
  `resourceType`. Cross-repository — does **not** cover Crossref-issued
  DOIs (Nature, ACM, IEEE).
- **`DataCiteOrganization`** — bare anchor for a ROR identifier
  (`https://ror.org/<id>`). Name populated opportunistically from
  `DataCiteWork.creators[].affiliation[].name` entries; NOT enriched via
  `api.ror.org`.
- **`DataCitePerson`** — bare anchor for an ORCID identifier
  (`https://orcid.org/<id>`). Name populated opportunistically; NOT
  enriched via `pub.orcid.org`.
- **`DataCiteClient`** — a DataCite-registered repository (e.g.
  `cern.zenodo`, `figshare.ars`, `dryad.dryad`). Passive node — `expand`
  emits no edges. Enriched from `/clients/<id>` with `clientType`,
  `domains`, and the `re3data` registry cross-reference.

## Edge kinds

| Source | Kind | Target |
|---|---|---|
| `DataCiteWork` | `authored_by` | `DataCitePerson` (orcid.org URL) |
| `DataCiteWork` | `affiliated_with` | `DataCiteOrganization` (ror.org URL) |
| `DataCiteWork` | `related_to.<RelationType>` | URL on any platform |
| `DataCiteWork` | `published_by` | `DataCiteClient` (commons.datacite.org/repositories/<id>) |
| `DataCiteOrganization` | `has_publication` | `DataCiteWork` (doi.org URL) |
| `DataCitePerson` | `authored` | `DataCiteWork` (doi.org URL) |
| `DataCiteClient` | *(passive)* | — |

`related_to.<RelationType>` reuses the shared DataCite RelationType
vocabulary (also consumed by Zenodo and Infoscience): the URL
synthesizer in `platforms/datacite.py` handles `arxiv`, `orcid`, `pmid`,
`pmcid`, `swh`, `doi`, and `url` schemes uniformly.

## DOI prefix routing

`DataCiteWork` is intentionally **never** produced for DOIs whose prefix
is owned by a sibling adapter. The `_DOI_PREFIX_REWRITERS` table in
`platforms/datacite.py` handles the rewrite:

- `10.5281/zenodo.X` → `https://zenodo.org/records/X` (Zenodo adapter)
- `10.5072/zenodo.X` → `https://sandbox.zenodo.org/records/X` (Zenodo sandbox)

Other DOI prefixes route through DataCite. New entries can be added as
sibling adapters land.

> **Heads-up: cross-platform crawls need every target platform enabled.**
> The prefix rewrite happens *before* BFS dispatch, so a Zenodo-prefix DOI
> seeded as `https://doi.org/10.5281/zenodo.42` becomes
> `https://zenodo.org/records/42` — which then needs a registered Zenodo
> adapter to crawl. If you run `--platforms datacite.org` alone, the
> rewritten URL has nowhere to go and the BFS drops it. To follow
> Zenodo-prefix DOIs, enable both:
>
> ```bash
> CRAWLER_PLATFORMS=datacite.org,zenodo.org
> ```
>
> The same applies for any future DOI-prefix entry: enable its target
> platform alongside `datacite.org`.

## Configuring tokens

Anonymous reads work for `/dois`, `/clients`, `/dois?query=…`. Tokens
raise rate limits:

```bash
CRAWLER_PLATFORMS=datacite.org
CRAWLER_TOKEN__API_DATACITE_ORG=<bearer-token>
# Or rotation pool:
CRAWLER_TOKEN_POOL__API_DATACITE_ORG=<tok-a>,<tok-b>
```

DataCite tokens are provisioned at <https://commons.datacite.org/sign-in>
for organization members.

## Seed forms accepted

- Canonical: `https://doi.org/<DOI>` (non-Zenodo prefix), `https://ror.org/<id>`,
  `https://orcid.org/<id>`, `https://commons.datacite.org/repositories/<client_id>`.
- Aliases (rewritten to canonical form):
  - `https://api.datacite.org/dois/<DOI>` → `https://doi.org/<DOI>`
  - `https://api.datacite.org/clients/<id>` → `https://commons.datacite.org/repositories/<id>`
  - `https://commons.datacite.org/doi.org/<DOI>` → `https://doi.org/<DOI>`
  - `https://commons.datacite.org/ror.org/<id>` → `https://ror.org/<id>`
  - `https://commons.datacite.org/orcid.org/<id>` → `https://orcid.org/<id>`

**Not supported as seeds:** Crossref-issued DOIs (Nature, ACM, IEEE,
Elsevier, …). They 404 against `/dois/<doi>`. A future Crossref adapter
could cover them.

## Recipes

Each recipe lists the **command**, the **output** it produces, and **what to
look for**. All are anonymous — no token required. Output is trimmed for
clarity; counts are from live runs.

### Crawl a single DataCite dataset (DOI seed)

```bash
opc crawl --platforms datacite.org --rounds 1 \
    https://doi.org/10.16904/envidat.1 --output-dir ./out
```

```text
✓ Registered adapters for: api.datacite.org, commons.datacite.org, doi.org, orcid.org, ror.org
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 0.2s, 25 nodes in queue (25 users, 0 orgs, 0 repos)
  - Total nodes: 1
Exporting results... → ./out/<timestamp>.graph.json
```

The exported graph holds **1 node**: 1 `DataCiteWork` keyed by
`https://doi.org/10.16904/envidat.1`. The 25 neighbours (ORCID-identified
creators and a `DataCiteClient`) sit in the queue — run `--rounds 2` to
materialize them.

**What to look for:** the work node is stored under `repos` with
`subkind=DataCiteWork`; the `resource_type` field carries
`resourceTypeGeneral` (e.g. `Dataset`).

### Crawl all DataCite works for an institution (ROR seed)

```bash
opc crawl --platforms datacite.org --rounds 1 \
    https://ror.org/02s376052 --output-dir ./out
```

```text
✓ Registered adapters for: api.datacite.org, commons.datacite.org, doi.org, orcid.org, ror.org
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 22.9s, 3636 nodes in queue (3636 users, 0 orgs, 0 repos)
  - Total nodes: 1
Exporting results... → ./out/<timestamp>.graph.json
```

The exported graph holds **1 node**: 1 `DataCiteOrganization` anchor for
EPFL's ROR. Round 0 paginates through DataCite's affiliation index and
enqueues **3,636 DOIs** — run `--rounds 2` to fetch and store them all.

**What to look for:** the organization node is stored under `orgs` with
`subkind=DataCiteOrganization`; the queue count reflects the total
ROR-tagged works DataCite has indexed for this institution.

### Crawl all DataCite works by a researcher (ORCID seed)

```bash
opc crawl --platforms datacite.org --rounds 2 \
    https://orcid.org/0000-0002-1477-6999 --output-dir ./out
```

```text
✓ Registered adapters for: api.datacite.org, commons.datacite.org, doi.org, orcid.org, ror.org
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 0.3s, 3 nodes in queue (3 users, 0 orgs, 0 repos)
  - Round 1 completed: 3 nodes processed in 0.2s, 32 nodes in queue (32 users, 0 orgs, 0 repos)
  - Total nodes: 4
Exporting results... → ./out/<timestamp>.graph.json
```

The exported graph holds **4 nodes**: 1 `DataCitePerson` and 3
`DataCiteWork` datasets. Round 1 fetches each queued DOI and expands
co-author and affiliation edges into the next queue.

**What to look for:** the person node is under `users` with
`subkind=DataCitePerson`; each work is under `repos` with
`subkind=DataCiteWork`; the person is keyed by its `orcid.org` URL.

### Cross-platform crawl (DataCite + Zenodo, ROR seed)

```bash
CRAWLER_PLATFORMS=datacite.org,zenodo.org \
opc crawl --rounds 1 \
    https://ror.org/02s376052 --output-dir ./out
```

```text
✓ Registered adapters for: api.datacite.org, commons.datacite.org, doi.org, orcid.org, ror.org, zenodo.org
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 27.3s, 3636 nodes in queue (3636 users, 0 orgs, 0 repos)
  - Total nodes: 1
Exporting results... → ./out/<timestamp>.graph.json
```

With both adapters enabled, DOI-prefix routing rewrites any
`10.5281/zenodo.*` DOIs in the queue to `zenodo.org/records/<id>` so the
Zenodo adapter picks them up in round 2.

**What to look for:** the *Registered adapters* line now includes
`zenodo.org`; Zenodo-prefix DOIs that would 404 against DataCite are
rewritten transparently before BFS dispatch.

## Limitations
- **Crossref-issued DOIs (Nature, ACM, IEEE, Elsevier, …) are not in
  DataCite's index.** They 404 against `/dois/<doi>`. A future Crossref
  adapter could cover them.
- **ROR and ORCID nodes are bare anchors.** Name / country / type fields
  are not enriched from `api.ror.org` or `pub.orcid.org` — identity
  resolution stays downstream of this tool.
- **`DataCiteClient` is passive.** A `cern.zenodo` seed doesn't fan out
  to its corpus (millions of DOIs). Work nodes link to it via
  `published_by`; expand on a client emits nothing.
- **Coverage of institutional output via ROR is partial.** EPFL ROR
  returns ~3,500 works — vastly less than Infoscience holds, because
  most EPFL submissions to Infoscience aren't ROR-tagged in their
  creator affiliations.
- **No Bearer token validation.** Anonymous reads degrade gracefully; an
  invalid token silently 401s on protected endpoints (none are used in
  v3.3 — all reads are public).
- **No deposit/submit/draft flows.** Read-only.

## See also

- [Node identifiers](NODE_IDS.md) — how canonical node keys are formed across platforms.
- [REST API](API.md) — drive crawls programmatically.
- [All platform guides](index.md) — the documentation hub.
