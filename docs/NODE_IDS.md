# Node identifiers

Open Pulse Crawler 2.0 identifies every node in the graph — user,
organization, repository, team — by its **canonical public URL** on the
source platform. This page documents the canonical form, how seed input
is normalized, and the reasoning behind the choice.

## Canonical form

| Component       | Rule                                              | Example                                            |
| --------------- | ------------------------------------------------- | -------------------------------------------------- |
| Scheme          | Always `https://`                                 | `https://`                                         |
| Host            | Lowercased                                        | `github.com`                                       |
| Path            | Preserved as-is (case is *not* folded)            | `/Torvalds/Linux` stays `/Torvalds/Linux`          |
| Trailing slash  | Stripped                                          | `https://github.com/torvalds/` → `https://github.com/torvalds` |
| Query / fragment / userinfo / port | None (rejected at parse time)  | — |

Path case is preserved because GitHub displays it as the user typed it
in profile / repo settings; lowercasing would silently change identity.
The host is lowercased per RFC 3986 — hostnames are case-insensitive.

### Examples by kind

| Kind          | URL                                                              |
| ------------- | ---------------------------------------------------------------- |
| User          | `https://github.com/torvalds`                                    |
| Organization  | `https://github.com/sdsc-ordes`                                  |
| Repository    | `https://github.com/sdsc-ordes/gimie`                            |
| Team          | `https://github.com/orgs/sdsc-ordes/teams/core`                  |

Team URLs intentionally use GitHub's `orgs/<org>/teams/<slug>` browser
path so the URL resolves in a browser and can serve as a JSON-LD `@id`.

## Seed input contract

Any of these forms is accepted at every seed boundary (CLI, REST API,
seed file, programmatic `add_seeds`), and each is normalized to the
canonical URL before the BFS begins:

* Bare login → user URL: `torvalds` → `https://github.com/torvalds`
* `owner/repo` → repo URL: `sdsc-ordes/gimie` → `https://github.com/sdsc-ordes/gimie`
* Full URL (any case / trailing slash / `http://`) → canonical URL:
  `HTTP://GitHub.com/torvalds/` → `https://github.com/torvalds`

A seed that resolves to a single path segment is `USER_OR_ORG` — the
crawler probes the user endpoint first and falls back to the org
endpoint, matching 1.x behavior.

Parsing rejects, with a clear `ValueError`:

* Empty / whitespace-only seeds
* `https://` URLs without a host
* Paths with three or more segments that don't match the team shape

## Programmatic helpers

The `open_pulse_crawler.node_id` module is the single source of truth:

```python
from open_pulse_crawler.node_id import (
    canonical_url, host_of,
    user_url, repo_url, team_url,
    extract_login, extract_full_name, extract_team_parts,
    kind_of, is_team_url,
    parse_seed, NodeKind,
)

user_url("torvalds")
# → "https://github.com/torvalds"

repo_url("sdsc-ordes/gimie")
# → "https://github.com/sdsc-ordes/gimie"

parse_seed("https://GitHub.com/sdsc-ordes/gimie/")
# → (NodeKind.REPO, "https://github.com/sdsc-ordes/gimie")

extract_full_name("https://github.com/sdsc-ordes/gimie")
# → "sdsc-ordes/gimie"

host_of("https://github.com/torvalds")
# → "github.com"
```

`parse_seed` and the builders accept a `default_host` keyword argument
that lets the future per-platform dispatch produce URLs for other hosts
without changing the function shape (e.g. `parse_seed("alice",
default_host="gitlab.com")` returns
`(NodeKind.USER_OR_ORG, "https://gitlab.com/alice")`).

## Where canonical URLs appear

| Boundary                            | Was (1.x)                       | Is (2.0)                                         |
| ----------------------------------- | ------------------------------- | ------------------------------------------------ |
| `GraphData.users/.orgs/.repos/.teams` keys | login / full_name string  | canonical URL                                    |
| Nodes CSV `id` column               | login / full_name               | canonical URL                                    |
| Edges CSV `source` / `target`       | login / full_name               | canonical URL                                    |
| BFS visited set / queue identifier  | login / full_name               | canonical URL                                    |
| State file `visited` / `seed_nodes` | login / full_name strings       | canonical URLs                                   |
| API response cache key              | endpoint+params MD5             | URL-prefixed                                     |
| Client `get_user(...)` argument     | login                           | canonical URL                                    |
| Model edge-list fields (`followers`, `contributors`, `dependencies`, `members`, ...) | login / full_name | **unchanged** — these stay as bare shorthand internally; the CSV/JSON exporter converts to URLs at write time. |

The hybrid choice — URLs at the *identity* boundary, bare shorthand on
internal edge lists — keeps the on-the-wire model compact while ensuring
every external artifact joins on a single uniform key.

## Looking ahead: multi-platform

A future release adds a `PlatformAdapter` per host
(`github.com`, `gitlab.epfl.ch`, `zenodo.org`, …) dispatched on the URL
host. The canonical URL form documented here is designed to round-trip
through that dispatcher unchanged.
