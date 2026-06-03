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

## Manual-test recipes

These can't run in CI (institutional tokens needed). Run locally.

### gitlab.com — one-round seed

```bash
CRAWLER_TOKEN__GITLAB_COM=glpat-… \
opc crawl --platforms gitlab.com --rounds 1 \
  https://gitlab.com/gitlab-org/gitlab-foss
```

### gitlab.epfl.ch — single user (Matthieu Bovel)

```bash
CRAWLER_TOKEN__GITLAB_EPFL_CH=glpat-… \
opc crawl --platforms gitlab.epfl.ch \
  --default-host gitlab.epfl.ch --rounds 2 \
  https://gitlab.epfl.ch/users/bovel
```

The dashboard-form URL `https://gitlab.epfl.ch/users/bovel` is canonicalized
to `https://gitlab.epfl.ch/bovel` by the adapter; both forms produce the
same graph node.

### gitlab.ethz.ch — single user (vermeul, anonymous OK)

```bash
# Authenticated (read_api scope recommended)
CRAWLER_TOKEN__GITLAB_ETHZ_CH=glpat-… \
opc crawl --platforms gitlab.ethz.ch \
  --default-host gitlab.ethz.ch --rounds 2 \
  https://gitlab.ethz.ch/vermeul

# Anonymous (public profile reads only; expansion endpoints return 403,
# the adapter degrades silently — fine for a quick visibility check).
opc crawl --platforms gitlab.ethz.ch \
  --default-host gitlab.ethz.ch --rounds 2 \
  https://gitlab.ethz.ch/vermeul
```

### gitlab.renkulab.io — Renku group + fork tree (anonymous)

```bash
# Group seed — discovers owned projects (members are gated, gracefully skipped).
opc crawl --platforms gitlab.renkulab.io \
  --default-host gitlab.renkulab.io --rounds 2 \
  https://gitlab.renkulab.io/HSLU-Predictive-Modeling

# Project seed — follows the fork tree. The HSLU course repo has ~74
# student forks, all discovered in round 1.
opc crawl --platforms gitlab.renkulab.io \
  --default-host gitlab.renkulab.io --rounds 2 \
  https://gitlab.renkulab.io/HSLU-Predictive-Modeling/hslu-predictive-modeling
```

### gitlab.epfl.ch — group + project mix

```bash
CRAWLER_TOKEN__GITLAB_EPFL_CH=glpat-… \
opc crawl --platforms gitlab.epfl.ch --rounds 2 \
  https://gitlab.epfl.ch/<group>/<project> \
  https://gitlab.epfl.ch/<other-group>
```

### Multi-instance crawl

```bash
CRAWLER_PLATFORMS=github.com,gitlab.com,gitlab.epfl.ch \
CRAWLER_TOKEN__GITHUB_COM=… \
CRAWLER_TOKEN__GITLAB_COM=… \
CRAWLER_TOKEN__GITLAB_EPFL_CH=… \
opc crawl --rounds 2 \
  https://github.com/sdsc-ordes/open-pulse-crawler \
  https://gitlab.com/gitlab-org/gitlab-foss
```

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
