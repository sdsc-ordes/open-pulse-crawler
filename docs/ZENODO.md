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

> **⚠️ A version seed resolves to its concept record — the node URL may
> differ from the seed URL.** Every Zenodo record is keyed by its
> *concept DOI* (the version-agnostic identity), so seeding a specific
> *version* record collapses to the concept node. For example, seeding
> `https://zenodo.org/records/6494798` (a version) produces the node
> `https://zenodo.org/records/6494797` (the concept record), with the
> version recorded in the concept node's `versions` field. **This is by
> design — the seed is not lost.** If you need to match a submitted seed
> URL back to its graph node, expect the records/`<id>` to change when the
> seed was a non-concept version; the original version id is preserved
> under `versions`.

## Recipes

Each recipe lists the **command**, the **output** it produces, and **what to
look for**. All run anonymously — no token required for public records. Output
is trimmed for clarity; counts are from live runs.

### Software-rich community (ESCAPE OSSR — the canonical demo)

The ESCAPE Open Science Software Repository is a Zenodo community where
~14/15 records carry `related_identifiers` pointing at their actual source
repos on GitHub / GitLab — the gold-standard cross-platform demo.

```bash
# Round 0 fetches the community; round 1 walks `contains` to its 56 records.
opc crawl --platforms zenodo.org --rounds 2 \
    https://zenodo.org/communities/escape2020 --output-dir ./out \
    --no-cache --no-csv
```

```text
✓ Registered adapters for: zenodo.org
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 5.7s, 56 in queue
  - Round 1 completed: 56 nodes processed in 6.2s, 75 in queue
  - Total nodes: 57
  - Duration: 11s
✓ JSON: ./out/20260603084943.graph.json
```

The exported graph holds **1 `ZenodoCommunity`** (escape2020) and **42
`ZenodoRecord`** nodes. Anonymous rate limits occasionally cause a handful
of records to 429; the total-nodes figure (57) counts all attempts while the
JSON holds only successful fetches. Round 2 would fan out to uploaders and
sibling communities queued (75 items).

**What to look for:** the community node is keyed by
`https://zenodo.org/communities/escape2020`; each record is keyed by its
*concept DOI URL* (version-agnostic). Any version seed collapses to the
concept node — see the **Seed forms accepted** section above.

### Smaller demo community (EOSC Association)

A lighter alternative (~73 records, fewer outgoing links) useful when you
just want to verify the `contains` fan-out without hitting the ESCAPE
rate-limit edge:

```bash
opc crawl --platforms zenodo.org --rounds 2 \
    https://zenodo.org/communities/eosc --output-dir ./out \
    --no-cache --no-csv
```

```text
✓ Registered adapters for: zenodo.org
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 3.9s, 73 in queue
  - Round 1 completed: 73 nodes processed in 4.4s, 29 in queue
  - Total nodes: 74
  - Duration: 8s
✓ JSON: ./out/20260603085107.graph.json
```

The exported graph holds **1 `ZenodoCommunity`** (eosc) and **41
`ZenodoRecord`** nodes. 29 uploaders / sibling communities are queued for
round 3 but not yet fetched.

**What to look for:** the `ZenodoCommunity` node carries the full record list
in its `contains` edges; each `ZenodoRecord` links back via `in_community`.

### Single record — version seed resolves to concept record

Seeding a specific-version Zenodo URL collapses to the version-agnostic
concept record. The seed `records/20432079` (a version) redirects to
`records/4701488` (the concept).

```bash
opc crawl --platforms zenodo.org --rounds 1 \
    https://zenodo.org/records/20432079 --output-dir ./out \
    --no-cache --no-csv
```

```text
✓ Registered adapters for: zenodo.org
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 0.6s, 7 in queue
  - Total nodes: 1
  - Duration: 0s
✓ JSON: ./out/20260603085032.graph.json
```

The exported graph holds **1 `ZenodoRecord`** node keyed by
`https://zenodo.org/records/4701488` (Gammapy: Python toolbox for
gamma-ray astronomy). 7 items are queued for the next round: the uploader
account and any sibling communities the record belongs to.

**What to look for:** the node URL (`records/4701488`) differs from the seed
URL (`records/20432079`) because the seed was a non-concept version. The
original version id is preserved under the node's `versions` field. Use
`--rounds 2` to also fetch the queued uploaders and communities.

### Cross-platform crawl (Zenodo → GitHub via `related_to.*`)

End-to-end: seed the ESCAPE community on Zenodo, follow the
`related_to.isDerivedFrom` / `isDocumentedBy` edges into GitHub. Requires
a GitHub token; Zenodo reads remain anonymous.

```bash
CRAWLER_PLATFORMS=zenodo.org,github.com \
CRAWLER_TOKEN__GITHUB_COM=ghp_… \
opc crawl --rounds 2 \
    https://zenodo.org/communities/escape2020 --output-dir ./out
```

**What to look for:** the *Registered adapters* line shows both `zenodo.org`
and `github.com`. After round 1 the `related_to.*` edges on each
`ZenodoRecord` are followed into GitHub, producing `GitHubRepository`,
`GitHubOrganization`, and `GitHubUser` nodes alongside the Zenodo nodes.

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

## Limitations
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
