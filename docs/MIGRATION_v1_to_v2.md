# Migration: v1.x → v2.0

Open Pulse Crawler 2.0 is a **breaking release**. The export format, the
environment-variable contract, and the on-disk snapshot format all change.
This page is the one-stop migration guide for operators and integrators.

## TL;DR

1. Rename `GITHUB_TOKEN` to `CRAWLER_GITHUB_TOKEN` (single) or
   `CRAWLER_GITHUB_TOKEN_POOL` (comma-separated list for rotation).
2. Re-crawl from seeds. Snapshots and the response cache from 1.x are not
   readable in 2.0.
3. Update downstream consumers that read the JSON / CSV / API responses:
   every node ID — dict keys in the JSON, the `id` column in the nodes
   CSV, and the `source` / `target` columns in the edges CSV — is now the
   node's canonical public URL.

## Why 2.0 is breaking

* **Multi-platform foundation.** Going forward the crawler will support
  GitLab (multi-instance), Zenodo, and HuggingFace alongside GitHub.
  Bare logins and `owner/repo` strings collide across platforms; canonical
  URLs do not. Switching the identity model to URLs now keeps follow-up
  releases additive.
* **JSON-LD / linked-data fit.** The gimie integration the project
  already uses keys entities by `@id` (a URL). The new format aligns
  internal IDs with that contract.

## Environment-variable changes

The CLI, REST/GraphQL workers, and the helper scripts under
`tools/scripts/` all resolve tokens through the same priority chain:

| Variable                       | Role                                                                   |
| ------------------------------ | ---------------------------------------------------------------------- |
| `CRAWLER_GITHUB_TOKEN_POOL`    | Comma-separated list of PATs for rotation. **Wins over** `CRAWLER_GITHUB_TOKEN` when both are set. |
| `CRAWLER_GITHUB_TOKEN`         | Single PAT.                                                             |
| `GITHUB_TOKEN` (**deprecated**) | Still read as a fallback. Logs a one-shot deprecation warning. Will be removed in a future release. |

Migration steps:

```bash
# Old (1.x): one variable, comma-separated for rotation
export GITHUB_TOKEN="ghp_a,ghp_b,ghp_c"

# New (2.0): explicit pool variable
export CRAWLER_GITHUB_TOKEN_POOL="ghp_a,ghp_b,ghp_c"

# Or, for a single-token deployment:
export CRAWLER_GITHUB_TOKEN="ghp_a"
```

Update your `.env`, your `docker-compose.yml` (or whichever `--env-file` it
references), and any CI workflow that exports the variable. Note the
GitHub Actions built-in `${{ secrets.GITHUB_TOKEN }}` is a *different*
variable owned by Actions itself and is unrelated.

## Output format: nodes are keyed by canonical URL

Every user, organization, repository, and team in the exported graph is
identified by its public URL on the source platform:

| Node kind     | Canonical URL example                                       |
| ------------- | ----------------------------------------------------------- |
| User          | `https://github.com/torvalds`                               |
| Organization  | `https://github.com/sdsc-ordes`                             |
| Repository    | `https://github.com/sdsc-ordes/gimie`                       |
| Team          | `https://github.com/orgs/sdsc-ordes/teams/core`             |

**Normalization** (see [`NODE_IDS.md`](./NODE_IDS.md) for the full rules):

* `https` scheme; host lowercased; path preserved as-is; no trailing slash.

### JSON shape

```diff
 {
+  "schema_version": 2,
   "users": {
-    "torvalds": {
-      "login": "torvalds",
+    "https://github.com/torvalds": {
+      "url": "https://github.com/torvalds",
+      "platform": "github",
+      "login": "torvalds",
       …
     }
   },
   "orgs":  { … },
   "repos": { … },
   "teams": { … }
 }
```

Every model gains two new fields: `url: str` (the canonical identifier)
and `platform: str = "github"`. Legacy fields (`login`, `full_name`,
`owner`, `slug`) remain on the model for display and for any code that
still needs the platform-native shorthand.

### CSV shapes

* **Nodes CSV (`*.nodes.csv`):** the `id` column is the canonical URL
  (was: `login` for users/orgs, `full_name` for repos/teams). `is_seed`
  comparison uses URLs.
* **Edges CSV (`*.edges.csv`):** both `source` and `target` columns are
  canonical URLs. The set of edge kinds (`owner_of`, `contributor_of`,
  `follows`, `starred`, `watching`, `parent_of`, `depends_on`,
  `issue_author`, `pr_author`, `commented_on`, `pr_reviewer`, `has_team`,
  `has_access`) is unchanged.

### REST API response shape

`GET /api/v1/graph/{job_id}` returns the same wrapper as before, with
the underlying `graph` object reshaped per the JSON-diff above. The
`schema_version: 2` field on `graph` is the durable signal for
consumers to detect the new format.

## On-disk state and cache

* **Snapshot files** (`{OPC_DATA_DIR}/{job_id}/graph.snapshot.json`) and
  **state files** (`{OPC_DATA_DIR}/{job_id}/state.json`) record a
  `schema_version`. 2.0 refuses snapshots and state files with
  `schema_version < 2`: it logs a clear warning and treats the job as
  "no snapshot / no resumable state" so the only safe path is to
  re-crawl from seeds.
* **API response cache** (`OPC_CACHE_DIR`) keys are now URL-based.
  Existing cache entries written under 1.x are not readable; the first
  access to any entity refetches from GitHub. You can delete the cache
  directory or let it self-refresh — both work.

### Operator checklist

1. Update the env vars (see above).
2. Stop the running service / scheduled job.
3. (Optional) clear the cache directory: `rm -rf ${OPC_CACHE_DIR:-data/open-pulse-crawler/cache}`.
4. (Optional) clear the per-job state: `rm -rf ${OPC_DATA_DIR:-/tmp/open-pulse-crawler}/*`.
5. Pull the 2.0 image / install the 2.0 package.
6. Restart and re-crawl from your seeds.

## Seed input is unchanged

The crawler still accepts the same seed forms it did in 1.x:

* Bare login: `torvalds`
* `owner/repo`: `sdsc-ordes/gimie`
* Full URL: `https://github.com/sdsc-ordes/gimie`

All three are normalized to the canonical URL internally before the BFS
begins. Existing seed files do not need editing.

## Programmatic API

If you are using the crawler as a library (rather than the CLI / REST
API), three call sites changed signature:

| Client method                                     | 1.x argument         | 2.0 argument        |
| ------------------------------------------------- | -------------------- | ------------------- |
| `GitHubClient.get_user`                           | `login`              | canonical URL       |
| `GitHubClient.get_organization`                   | `org_name`           | canonical URL       |
| `GitHubClient.get_repository`                     | `repo_full_name`     | canonical URL       |
| `GitHubClient.get_contributor_count`              | `repo_full_name`     | canonical URL       |
| `GitHubGraphQLClient.get_user / .get_organization / .get_repository` | login / org / full_name | canonical URL |

Crawler-internal methods (`_process_user`, `_process_organization`,
`_process_repository`, `_process_node`, `_parse_seed`) also take URLs;
these are not considered part of the public API but are documented for
contributors.

`GraphData` dict-access helpers (`has_user`, `get_user`, `add_user`, and
their org / repo / team siblings) all take URLs:

```python
# 1.x
graph.has_user("torvalds")
graph.get_repo("sdsc-ordes/gimie")

# 2.0
graph.has_user("https://github.com/torvalds")
graph.get_repo("https://github.com/sdsc-ordes/gimie")
```

The [`open_pulse_crawler.node_id`](./NODE_IDS.md) helpers
(`user_url`, `repo_url`, `team_url`, `parse_seed`) build canonical URLs
from logins / `owner/repo` / full URLs, including for any non-default host.
