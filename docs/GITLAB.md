# GitLab support

Open Pulse Crawler v3+ supports GitLab alongside GitHub. Multiple GitLab
instances can be crawled in a single run.

## Supported instances

* `gitlab.com`
* `gitlab.epfl.ch` (EPFL self-hosted)
* `gitlab.ethz.ch` (ETHZ self-hosted)
* `renkulab.io` (Renku — treated as a vanilla GitLab instance in v3)

Other instances should work, but require a token with sufficient
permissions on the target host. Self-hosted GitLabs may run older
versions where some endpoints (e.g. `/starrers`) are not available;
the adapter degrades gracefully — affected edges are simply not emitted.

## Getting a Personal Access Token

* **gitlab.com:** Settings → Access Tokens → New token. Scope: `read_api`.
* **gitlab.epfl.ch / gitlab.ethz.ch:** Sign in via institutional SSO,
  then User Settings → Access Tokens. Scope: `read_api`.
* **renkulab.io:** Sign in, then Profile → Personal Access Tokens.

## Configuring tokens

Use the per-host env-var scheme:

```bash
# Single token per host
CRAWLER_TOKEN__GITLAB_COM=glpat-…
CRAWLER_TOKEN__GITLAB_EPFL_CH=glpat-…
CRAWLER_TOKEN__GITLAB_ETHZ_CH=glpat-…
CRAWLER_TOKEN__RENKULAB_IO=glpat-…

# Or a rotation pool (comma-separated)
CRAWLER_TOKEN_POOL__GITLAB_COM=glpat-a,glpat-b,glpat-c
```

Set `CRAWLER_PLATFORMS` to a comma-separated list of enabled hosts:

```bash
CRAWLER_PLATFORMS=github.com,gitlab.com,gitlab.epfl.ch
```

When unset, only `github.com` is enabled — preserving v2.x behaviour.

## Quick check

```bash
opc doctor
# Enabled platforms:
#   github.com: 1 token [OK]
#   gitlab.com: 3 tokens [OK]
#   gitlab.epfl.ch: 0 tokens [MISSING]
```

Add `--json` for machine-readable output.

## Recipes

Each recipe lists the **command**, the **output** it produces, and **what to
look for**. Anonymous reads work for all public instances; add a token for
higher rate limits and to unlock member-list endpoints on self-hosted instances.
Output is trimmed for clarity; counts are from live runs.

### Crawl a public project on gitlab.com (project seed)

```bash
opc crawl --platforms gitlab.com --rounds 1 \
  https://gitlab.com/gnuwget/wget2 \
  --output-dir ./out --no-cache --no-csv
```

```text
⚠ No tokens configured for gitlab.com; skipping (run 'opc doctor' for details)
✓ Registered adapters for: gitlab.com
✓ Added 1 seed nodes

  - Round 0 completed: 1 node processed in 11s, 139 nodes in queue (139u/0o/0r)
  - Total nodes: 1
  - Users: 0, Orgs: 0, Repos: 1
```

**What to look for:** the project is keyed by its full URL
(`https://gitlab.com/gnuwget/wget2`) and stored as a `GitLabProject` node.
Round 0 discovers fork owners and contributors; they are queued as
`GitLabUser` nodes for the next round. The "skipping" warning is cosmetic —
anonymous reads still work for public resources; add
`CRAWLER_TOKEN__GITLAB_COM=glpat-…` to lift rate limits.

### Crawl a group on gitlab.com (group seed)

```bash
opc crawl --platforms gitlab.com --rounds 1 \
  https://gitlab.com/gnuwget \
  --output-dir ./out --no-cache --no-csv
```

```text
⚠ No tokens configured for gitlab.com; skipping (run 'opc doctor' for details)
✓ Registered adapters for: gitlab.com
✓ Added 1 seed nodes

WARNING  groups/1530779/members/all on gitlab.com returned 401
         (likely insufficient token scope); skipping that edge source.

  - Round 0 completed: 1 node processed in 3s, 5 nodes in queue (5u/0o/0r)
  - Total nodes: 1
  - Users: 0, Orgs: 1, Repos: 0
```

**What to look for:** the group is stored as a `GitLabGroup` node. The `401`
on `members/all` is expected in anonymous mode — the adapter degrades
gracefully and still discovers members through the group's projects. Add a
token with `read_api` scope to unlock the direct members endpoint.

### Crawl a user on gitlab.ethz.ch (self-hosted, anonymous)

```bash
opc crawl --platforms gitlab.ethz.ch \
  --default-host gitlab.ethz.ch --rounds 1 \
  https://gitlab.ethz.ch/vermeul \
  --output-dir ./out --no-cache --no-csv
```

```text
⚠ No tokens configured for gitlab.ethz.ch; skipping (run 'opc doctor' for details)
✓ Registered adapters for: gitlab.ethz.ch
✓ Added 1 seed nodes

  - Round 0 completed: 1 node processed in 1s, 12 nodes in queue (12u/0o/0r)
  - Total nodes: 1
  - Users: 1, Orgs: 0, Repos: 0
```

**What to look for:** the user is keyed as `https://gitlab.ethz.ch/vermeul`
and stored as a `GitLabUser` node. Expansion discovers co-contributors on the
user's public projects; 12 users are queued for round 1. The same command
works with `CRAWLER_TOKEN__GITLAB_ETHZ_CH=glpat-…` prepended for
authenticated access (more edges, higher rate limits).

### Crawl a user on gitlab.epfl.ch (dashboard URL form, anonymous)

```bash
opc crawl --platforms gitlab.epfl.ch \
  --default-host gitlab.epfl.ch --rounds 1 \
  https://gitlab.epfl.ch/users/bovel \
  --output-dir ./out --no-cache --no-csv
```

```text
⚠ No tokens configured for gitlab.epfl.ch; skipping (run 'opc doctor' for details)
✓ Registered adapters for: gitlab.epfl.ch
✓ Added 1 seed nodes

  - Round 0 completed: 1 node processed in 0s, 3 nodes in queue (3u/0o/0r)
  - Total nodes: 1
  - Users: 1, Orgs: 0, Repos: 0
```

**What to look for:** the dashboard-form URL `…/users/bovel` is canonicalized
to `https://gitlab.epfl.ch/bovel` by the adapter before any fetch; both
forms produce the same `GitLabUser` node. Use
`CRAWLER_TOKEN__GITLAB_EPFL_CH=glpat-…` (institutional SSO token,
`read_api` scope) to unlock project-membership edges.

### Crawl a fork-tree on gitlab.renkulab.io (Renku, anonymous)

```bash
opc crawl --platforms gitlab.renkulab.io \
  --default-host gitlab.renkulab.io --rounds 1 \
  https://gitlab.renkulab.io/HSLU-Predictive-Modeling/hslu-predictive-modeling \
  --output-dir ./out --no-cache --no-csv
```

```text
⚠ No tokens configured for gitlab.renkulab.io; skipping (run 'opc doctor' for details)
✓ Registered adapters for: gitlab.renkulab.io
✓ Added 1 seed nodes

  - Round 0 completed: 1 node processed in 4s, 74 nodes in queue (74u/0o/0r)
  - Total nodes: 1
  - Users: 0, Orgs: 0, Repos: 1
```

**What to look for:** the project is stored as a `GitLabProject` node. Round 0
walks the fork list and discovers 74 student forks, queuing their owners as
`GitLabUser` nodes for the next round. `renkulab.io` is treated as a vanilla
GitLab instance — Renku-specific metadata (datasets, lineage) is not modelled.

## Per-instance quirks

* **`/api/v4/projects/:id/starrers`** — added in GitLab 13.5 (2020-10).
  Older self-hosted instances will return 404; `--crawl-stars` becomes
  a no-op for them. No error.
* **User-vs-group disambiguation** at top-level paths (`https://host/foo`):
  the adapter probes the users endpoint first, then groups. Result is
  cached in-memory per crawl.
* **Subgroup-vs-project disambiguation** at nested paths
  (`https://host/a/b/c`): the adapter probes the projects endpoint first
  (most common), then groups.

## Limitations
* No SBOM / dependents view. GitLab has no public equivalent of GitHub's
  "Used by". `--crawl-dependencies` / `--crawl-dependents` are silently
  ignored for GitLab projects.
* No "follow user" concept on GitLab — the `following`/`followers`
  fields on `GitLabUserModel` stay empty.
* `renkulab.io` is treated as a vanilla GitLab instance — Renku
  datasets, project lineage, and Renku-specific concepts are not
  modelled in v3.0. Track in a future Renku-specific adapter.
