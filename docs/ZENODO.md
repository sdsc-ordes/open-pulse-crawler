# Zenodo adapter

Open Pulse Crawler v3.1+ supports Zenodo records, communities, and uploader
accounts as part of the unified open-science graph. Multiple Zenodo
instances can be crawled in the same run (`zenodo.org` and
`sandbox.zenodo.org`); both work anonymously for public reads.

## Supported entities

- **Records** — one node per *concept DOI* (the version-agnostic identity).
  Specific versions collapse into the concept node's `versions` field;
  there is **no** separate node per version.
- **Communities** — one node per community slug.
- **Users (uploaders)** — one node per Zenodo account. Distinct from
  *creators* on a record, which are author names + ORCIDs embedded in the
  record's `creators` field (creators are intentionally NOT crawled as User
  nodes — identity resolution is downstream).

## Edge kinds

| Source | Kind | Target |
|---|---|---|
| `ZenodoRecord` | `in_community` | `ZenodoCommunity` |
| `ZenodoRecord` | `uploaded_by` | `ZenodoUser` |
| `ZenodoUser` | `uploaded` | `ZenodoRecord` |
| `ZenodoCommunity` | `contains` | `ZenodoRecord` |
| `ZenodoRecord` | `related_to.<RelationType>` | URL on any platform |

`related_to.<RelationType>` carries the DataCite RelationType
(`isSupplementTo`, `cites`, `isCitedBy`, …) as a compound kind string.
Downstream consumers can split on `.` to recover the relation semantics.

## Configuring tokens

Anonymous reads work for `/api/records`, `/api/communities`, and many
record-search endpoints — no token required for a discovery crawl.
Authenticated mode raises rate limits and unlocks `/api/users/<id>`:

```bash
CRAWLER_PLATFORMS=zenodo.org
CRAWLER_TOKEN__ZENODO_ORG=zen-pat-here
# Or rotation pool:
CRAWLER_TOKEN_POOL__ZENODO_ORG=zen-pat-a,zen-pat-b
```

Sandbox tokens are independent:

```bash
CRAWLER_PLATFORMS=zenodo.org,sandbox.zenodo.org
CRAWLER_TOKEN__SANDBOX_ZENODO_ORG=zen-sandbox-pat
```

Get a Personal Access Token at <https://zenodo.org/account/settings/applications/tokens/new/>.

## Seed forms accepted

- Canonical Zenodo URL: `https://zenodo.org/records/<id>` /
  `/communities/<slug>` / `/users/<id>`.
- Legacy singular record path: `https://zenodo.org/record/<id>` rewritten
  to `/records/<id>`.
- Production DOI URL: `https://doi.org/10.5281/zenodo.<id>` → rewritten to
  `https://zenodo.org/records/<id>` (regex, no HTTP).
- Sandbox DOI URL: `https://doi.org/10.5072/zenodo.<id>` → rewritten to
  `https://sandbox.zenodo.org/records/<id>`.
- Non-Zenodo DOIs raise — they belong to other adapters.

## Manual-test recipes

### Software-rich community (ESCAPE OSSR — the canonical demo)

The ESCAPE Open Science Software Repository is a Zenodo community where
~14/15 records carry `related_identifiers` pointing at their actual source
repos on GitHub / GitLab — the gold-standard cross-platform demo.

```bash
# Round 0 fetches the community; round 1 walks `contains` to its 56 records.
opc crawl --platforms zenodo.org \
    --default-host zenodo.org --rounds 2 \
    https://zenodo.org/communities/escape2020
```

### Smaller demo community (EOSC Association)

Lighter alternative used by the integration test (~72 records, fewer
outgoing links — better when you just want to verify the `contains`
fan-out):

```bash
opc crawl --platforms zenodo.org \
    --default-host zenodo.org --rounds 2 \
    https://zenodo.org/communities/eosc
```

### Single record via DOI URL

```bash
opc crawl --platforms zenodo.org \
    --default-host zenodo.org --rounds 2 \
    https://doi.org/10.5281/zenodo.20432079
```

### Cross-platform crawl (Zenodo → GitHub via `related_to.*`)

End-to-end: seed the ESCAPE community on Zenodo, follow the
`related_to.isDerivedFrom` / `isDocumentedBy` edges into GitHub.

```bash
CRAWLER_PLATFORMS=zenodo.org,github.com \
CRAWLER_TOKEN__GITHUB_COM=ghp_… \
opc crawl --rounds 2 \
    https://zenodo.org/communities/escape2020
```

Verified result (anonymous Zenodo + authenticated GitHub, 2 rounds, 15 of
the 56 ESCAPE records as seeds): **28 graph nodes** — 15 `ZenodoRecord`,
8 `ZenodoCommunity` (escape2020 + 7 sibling communities discovered via
`in_community` edges), 4 `GitHubOrganization` (gammapy, FairRootGroup,
R3BRootGroup, cds-astro, ctlearn-project), 1 `GitHubUser`.

### Communities surveyed for `related_identifiers` density

| Community | Records | Rich-link density (sampled) | Notes |
|---|---:|:---|---|
| `escape2020` | 56 | 14/15 | Software repos — GitHub + GitLab |
| `neuroinformatics` | 16 | 11/15 | Computational neuroscience repos |
| `elixir` | 133 | 7/15 | Bioinformatics — mostly `doi` citations |
| `nfdi4ing` | 145 | 5/15 | Engineering — gitlab.rwth-aachen.de |
| `eosc-life` | 63 | 2/15 | Life-sciences citation chains |
| `pangeo` | 68 | 1/15 | Geoscience |
| `cernopenlab` | 355 | 0/150 → 1 elsewhere | Mostly reports |

## Limitations (v3.1)

- **Community members are not crawled.** The `/api/communities/<slug>/members`
  endpoint is auth-gated on production Zenodo. No `member_of` edges.
- **Versions are not separate nodes.** Each concept DOI is one node;
  `versions: list[dict]` carries the chain. If you need per-version
  provenance (which contributor was on v2 but not v3), this adapter does
  not model it.
- **`/api/users/<id>`** is often 401 anonymously on production Zenodo —
  the adapter degrades silently. User-seeded crawls require a token.
- **No deposit/upload/draft flows.** This is a read-only adapter.
- **ORCID resolution** stays embedded in `creators` / `external_identifiers`
  — no cross-platform identity linking. That layer is handled by a
  separate downstream tool.
