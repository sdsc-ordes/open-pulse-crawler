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

## Manual-test recipes

### Single DataCite work (Figshare)

```bash
opc crawl --platforms datacite.org \
    --default-host api.datacite.org --rounds 2 \
    https://doi.org/10.6084/m9.figshare.99
```

### All EPFL works in DataCite (via ROR)

```bash
opc crawl --platforms datacite.org \
    --default-host api.datacite.org --rounds 2 \
    https://ror.org/02s376052
```

Round 0 fetches the bare Organization anchor (no API call); round 1
walks `has_publication` edges to every DOI in DataCite tagged with
EPFL's ROR. Yields ~3,500 works at time of writing.

### All works by a single ORCID

```bash
opc crawl --platforms datacite.org \
    --default-host api.datacite.org --rounds 2 \
    https://orcid.org/0000-0002-1825-0097
```

### Cross-platform crawl (DataCite → Zenodo)

```bash
CRAWLER_PLATFORMS=datacite.org,zenodo.org \
    opc crawl --rounds 3 \
    https://ror.org/02s376052
```

DataCite emits `has_publication` edges to every affiliated DOI; the
DOI-prefix routing rewrites Zenodo-prefix DOIs to `zenodo.org/records/<id>`
so the Zenodo adapter picks them up in round 2.

## Limitations (v3.3)

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
