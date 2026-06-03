# Infoscience adapter

Open Pulse Crawler v3.2+ supports **EPFL's Infoscience** (DSpace 7.6.2 +
DSpace-CRIS 2023.02.06) as part of the unified open-science graph.
Anonymous reads work without a token, with aggressive 429 retry handling
built into the client.

## Supported entities

- **`InfoscienceItem`** — publications + resources (papers, theses,
  datasets, software, presentations, …). All are DSpace `item` entities
  with the same shape; the `resource_type` field carries `dc.type` so
  downstream code can filter publications vs datasets without an
  isinstance switch.
- **`InfosciencePerson`** — DSpace-CRIS Person entities (EPFL researcher
  profiles). Carries SciPer / ORCID / Scopus IDs as embedded metadata,
  **not** as cross-platform identity anchors.
- **`InfoscienceOrgUnit`** — DSpace-CRIS OrgUnit entities (EPFL
  departments / schools / labs). Forms the canonical EPFL hierarchy.

## Edge kinds

| Source | Kind | Target |
|---|---|---|
| `InfoscienceItem` | `authored_by` | `InfosciencePerson` |
| `InfoscienceItem` | `affiliated_with` | `InfoscienceOrgUnit` |
| `InfoscienceItem` | `related_to.<RelationType>` | URL on any platform |
| `InfosciencePerson` | `authored` | `InfoscienceItem` |
| `InfosciencePerson` | `member_of` | `InfoscienceOrgUnit` |
| `InfoscienceOrgUnit` | `has_publication` | `InfoscienceItem` |
| `InfoscienceOrgUnit` | `parent_of` | `InfoscienceOrgUnit` (parent → child) |

`related_to.<RelationType>` reuses the DataCite RelationType vocabulary
shared with the Zenodo adapter — the URL synthesizer in
`platforms/datacite.py` handles `arxiv`, `orcid`, `pmid`, `pmcid`, `swh`,
`doi`, and `url` schemes uniformly.

## Configuring tokens

Anonymous reads work for the public REST endpoints — no token required
for a discovery crawl. Authenticated mode raises rate limits and unlocks
some hidden metadata:

```bash
CRAWLER_PLATFORMS=infoscience.epfl.ch
CRAWLER_TOKEN__INFOSCIENCE_EPFL_CH=dspace-api-token-here
```

Tokens use the `Authorization: Bearer <token>` header. Contact EPFL IT
to provision a DSpace API token if you need authenticated access.

## Seed forms accepted

- Canonical handle URL: `https://infoscience.epfl.ch/handle/<prefix>/<id>`
  (for items, persons, and orgunits — they all share this form).
- UUID-form URL: `https://infoscience.epfl.ch/server/api/core/items/<uuid>`
  (resolved to the canonical handle on first fetch; cached).

**Not supported as seeds:** EPFL DOI URLs (`https://doi.org/10.5075/...`)
— the `10.5075` prefix covers multiple EPFL services, not just
Infoscience, so the adapter doesn't auto-rewrite them. Resolve manually
via doi.org redirect if needed.

## Manual-test recipes

### Single publication

```bash
opc crawl --platforms infoscience.epfl.ch \
    --default-host infoscience.epfl.ch --rounds 2 \
    https://infoscience.epfl.ch/handle/20.500.14299/182247
```

### Researcher → all their publications

```bash
opc crawl --platforms infoscience.epfl.ch \
    --default-host infoscience.epfl.ch --rounds 2 \
    https://infoscience.epfl.ch/handle/20.500.14299/99923
```

Round 0 fetches the Person; round 1 emits `authored` edges to every
publication attributable to them via the CRIS author.authority field.

### OrgUnit hierarchy + publications

```bash
opc crawl --platforms infoscience.epfl.ch \
    --default-host infoscience.epfl.ch --rounds 2 \
    https://infoscience.epfl.ch/handle/20.500.14299/77777
```

Round 1 emits `has_publication` edges to all items affiliated with the
department + `parent_of` edges to child OrgUnits.

### Cross-platform (Infoscience + GitHub)

```bash
CRAWLER_PLATFORMS=infoscience.epfl.ch,github.com \
CRAWLER_TOKEN__GITHUB_COM=ghp_… \
opc crawl --rounds 2 \
    https://infoscience.epfl.ch/handle/20.500.14299/182247
```

EPFL papers with `dc.relation.uri` / `dc.relation.isversionof` fields
pointing at GitHub repos spawn cross-platform discovery.

## Limitations
- **Community + Collection layers skipped.** The DSpace community/collection
  structure is redundant with OrgUnit on Infoscience (every item is in
  exactly one collection, every collection in one community, and the
  CRIS OrgUnit hierarchy already captures the canonical EPFL departmental
  structure). If you need them, file an issue.
- **DSpace-CRIS Project entities** (grants, funding) are not modeled.
  Would require a new `ProjectModel` base class — out of scope for v3.2.
- **EPFL DOI URLs are not auto-resolved.** The `10.5075` prefix covers
  multiple EPFL services; the adapter doesn't try to special-case them.
- **No `has_member` edge.** Infoscience's CRIS doesn't expose a direct
  Person→OrgUnit index; queries against all known candidate fields
  (`person.affiliation.authority`, `cris.virtual.parent-organization.authority`,
  `organization.authority`, `parent-organization.authority`,
  `oairecerif.author.affiliation.authority`) return 0 results for real
  OrgUnit UUIDs. To enumerate researchers in a department, iterate
  `has_publication` edges then deduplicate authors downstream.
- **Heavy rate-limiting.** Anonymous probes routinely return 429. The
  client honors `Retry-After` with one automatic retry; second 429
  raises. Provision a token if you crawl at scale.
- **No deposit/upload/draft flows.** This is a read-only adapter.
- **ORCID / SciPer / Scopus IDs stay embedded** in metadata — no
  cross-platform identity linking. That layer is downstream of this tool.
