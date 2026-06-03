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

## Recipes

Each recipe shows the **command**, **trimmed real output**, and **what to look
for**. All runs are anonymous — no token required.

### Crawl a single publication (Item seed)

```bash
opc crawl --platforms infoscience.epfl.ch --rounds 1 \
    https://infoscience.epfl.ch/handle/20.500.14299/182247 \
    --output-dir ./out --no-cache --no-csv
```

```text
✓ Registered adapters for: infoscience.epfl.ch
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 0.6s, 2 nodes
    in queue (2 users, 0 orgs, 0 repos)
  - Total nodes: 1
  - Users: 0, Orgs: 0, Repos: 1
```

**What to look for:** the seed resolves via the DSpace `pid/find` redirect and
lands as 1 `InfoscienceItem`. The 2 queued users are the `authored_by` persons
whose DSpace authority UUIDs were extracted from the item's author metadata. A
second round (`--rounds 2`) would fetch those `InfosciencePerson` nodes.

### Crawl a researcher and their lab affiliation (Person seed)

```bash
opc crawl --platforms infoscience.epfl.ch --rounds 2 \
    https://infoscience.epfl.ch/handle/20.500.14299/232 \
    --output-dir ./out --no-cache --no-csv
```

```text
✓ Registered adapters for: infoscience.epfl.ch
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 0.3s, 1 nodes
    in queue (1 users, 0 orgs, 0 repos)
  - Round 1 completed: 1 nodes processed in 0.4s, 0 nodes
    in queue (0 users, 0 orgs, 0 repos)
  - Crawl completed after 2 rounds
  - Total nodes: 2
  - Users: 1, Orgs: 1, Repos: 0
```

**2 nodes**: 1 `InfosciencePerson` (Viganò, Paola — ORCID
`0000-0002-5279-109X`) and 1 `InfoscienceOrgUnit` (Institut d'architecture et
de la ville). Round 0 fetches the Person; round 1 follows the `member_of` edge
to the OrgUnit identified by the person's `affiliation_uuid`.

**What to look for:** the person node carries `orcid`, `sciper_id`, and
`scopus_id` as embedded metadata fields. The `member_of` edge is the only
outbound edge emitted by a Person seed — `authored` edges to publications are
not currently yielded (the `author.authority` Solr field returns 0 results
against anonymous DSpace; see Limitations).

### Crawl a laboratory unit (OrgUnit seed)

```bash
opc crawl --platforms infoscience.epfl.ch --rounds 1 \
    https://infoscience.epfl.ch/handle/20.500.14299/509 \
    --output-dir ./out --no-cache --no-csv
```

```text
✓ Registered adapters for: infoscience.epfl.ch
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 0.7s, 0 nodes
    in queue (0 users, 0 orgs, 0 repos)
  - Crawl completed after 1 rounds
  - Total nodes: 1
  - Users: 0, Orgs: 1, Repos: 0
```

**1 node**: the `InfoscienceOrgUnit` for MACE (Microbiome Adaptation to the
Changing Environment, handle `20.500.14299/509`, level-4 unit under ENAC).

**What to look for:** the queue is empty after round 0. The `has_publication`
and `parent_of` expand queries use the `author.parent-organization.authority`
and `organization.parentOrganization.authority` Solr fields respectively;
these fields are not indexed in Infoscience's DSpace 7 Solr schema, so both
return 0 results. To retrieve publications for a unit, use the person seed
approach (recipe 2) and collect the `member_of` → `authored` chain, or query
the Infoscience web interface directly. See Limitations for details.

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

## See also

- [Node identifiers](NODE_IDS.md) — how canonical node keys are formed across platforms.
- [REST API](API.md) — drive crawls programmatically.
- [All platform guides](index.md) — the documentation hub.
