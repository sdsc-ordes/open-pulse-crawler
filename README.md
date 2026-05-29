# Open Pulse Crawler

A powerful GitHub crawler based on breadth-first search (BFS) strategy to discover and map relationships between users, organizations, and repositories.

## Features

- 🔍 **BFS Crawling**: Discovers GitHub entities layer by layer from initial seed nodes
- 🔄 **Multi-Token Support**: Use multiple GitHub tokens for higher rate limits
- 💾 **Smart Caching**: Avoids redundant API calls with file-based caching
- 📊 **Multiple Output Formats**: Export data as JSON, CSV (edges & nodes)
- 📈 **Visualization**: Generate network graphs with color-coded node types
- ⏸️ **State Management**: Save and resume crawler state
- 📝 **Rich Logging**: Timestamped logs with progress tracking and statistics
- 📉 **Progress Tracking**: Real-time progress bars with percentage, ETA, and statistics using tqdm
- 🎯 **Relationship Mapping**: Tracks ownership, contribution, forks, follows, stars, watches, org teams, and issue/PR activity
- 🔗 **URL-keyed nodes**: Every user / org / repo / team is identified by its canonical public URL (e.g. `https://github.com/torvalds`) — designed for future multi-platform crawling (GitLab next)
- 🔗 **Dependency Graph**: Crawl repository dependencies (SBOM) and dependents ("Used by")
- ⚡ **GraphQL Mode**: Optional GraphQL-backed crawl that collapses dozens of REST calls into a single query per entity
- 🚦 **Intelligent Rate Limiting**: Adaptive rate limit management with semaphores, delays, and multi-token rotation
- ⚙️ **Concurrent Control**: Configurable request throttling to prevent API abuse

## Multi-platform support

Open Pulse Crawler v3+ crawls both **GitHub and GitLab** in a single run.
Multiple GitLab instances are supported side-by-side — `gitlab.com`,
`gitlab.epfl.ch`, `gitlab.ethz.ch`, and `renkulab.io` are exercised in
manual-test recipes; other vanilla GitLab instances work with a valid
`read_api` token.

See [`docs/GITLAB.md`](docs/GITLAB.md) for the full GitLab guide
(supported instances, token scopes, per-instance quirks, and
manual-test recipes).

- **Zenodo** (`zenodo.org`, `sandbox.zenodo.org`): records, communities,
  uploader accounts. Cross-platform `related_to.<RelationType>` edges
  follow `metadata.related_identifiers` into GitHub etc. Anonymous reads
  supported. See [docs/ZENODO.md](docs/ZENODO.md).
- **Infoscience** (`infoscience.epfl.ch`): EPFL's DSpace 7 + DSpace-CRIS
  repository. Publications + Resources + Researchers + Departments,
  with DataCite `related_to.<RelationType>` edges identical to Zenodo's.
  Anonymous reads supported (with built-in 429 retry-after handling).
  See [docs/INFOSCIENCE.md](docs/INFOSCIENCE.md).

### Enabling platforms

`CRAWLER_PLATFORMS` is a comma-separated list of enabled hosts. When
unset, only `github.com` is enabled — preserving v2.x behaviour:

```bash
CRAWLER_PLATFORMS=github.com,gitlab.com,gitlab.epfl.ch
```

### Host-keyed token env vars

Tokens are configured per host. Each host accepts either a single token
or a comma-separated rotation pool:

```bash
# Single token per host
CRAWLER_TOKEN__GITHUB_COM=ghp_…
CRAWLER_TOKEN__GITLAB_COM=glpat-…
CRAWLER_TOKEN__GITLAB_EPFL_CH=glpat-…

# Or a rotation pool
CRAWLER_TOKEN_POOL__GITHUB_COM=ghp_a,ghp_b,ghp_c
CRAWLER_TOKEN_POOL__GITLAB_COM=glpat-a,glpat-b
```

The host name is uppercased and dots are converted to underscores
(`gitlab.epfl.ch` → `GITLAB_EPFL_CH`).

Check what is configured at a glance:

```bash
opc doctor
```

### Legacy GitHub env vars (deprecated)

`CRAWLER_GITHUB_TOKEN_POOL`, `CRAWLER_GITHUB_TOKEN`, and `GITHUB_TOKEN`
still work in v3 for `github.com` — each emits a one-shot deprecation
warning on first read. **They will be removed in v4**; migrate to the
host-keyed names above.

## Installation

Using uv (recommended):

```bash
# Install the package
uv pip install -e .

# With visualization support
uv pip install -e ".[viz]"

# With development tools
uv pip install -e ".[dev,viz]"
```

Using pip:

```bash
pip install -e .
# or with visualization
pip install -e ".[viz]"
```

## Testing

Ensure dev dependencies are installed first:

```bash
uv sync --extra dev
```

Run the Python unit tests in parallel (recommended):

```bash
uv run pytest -n auto
```

Run the same fast unit-test path used in CI:

```bash
uv run pytest -n auto -m "not integration"
```

If your machine has limited CPU or memory, cap workers explicitly:

```bash
uv run pytest -n 2 -m "not integration"
# or serialize for debugging
uv run pytest -n 1 -m "not integration"
```

Integration checks remain separate from this fast unit-test path. Run
`tests/test_integration.sh` when you specifically want to validate the Docker stack.

## Configuration

Set your GitHub personal access token(s) in the environment:

```bash
# Single token
export CRAWLER_GITHUB_TOKEN="ghp_your_token_here"

# Multiple tokens for rotation (comma-separated, better effective rate limit)
export CRAWLER_GITHUB_TOKEN_POOL="ghp_token1,ghp_token2,ghp_token3"
```

If both are set, `CRAWLER_GITHUB_TOKEN_POOL` wins. The legacy `GITHUB_TOKEN`
variable is still read as a fallback for now but will log a deprecation warning.

You can create a `.env` file in your project directory:

```bash
CRAWLER_GITHUB_TOKEN=ghp_your_token_here
API_TOKEN=your_api_token_for_rest_api
```

## REST API

Open Pulse Crawler includes a FastAPI service at `/api/v1` with:

- `GET /api/v1/health` (public)
- `POST /api/v1/crawl` — start a crawl (Bearer auth)
- `POST /api/v1/crawl/graphql` — start a GraphQL-backed crawl, same request body (Bearer auth)
- `GET /api/v1/crawl/{job_id}` — job status (Bearer auth)
- `POST /api/v1/crawl/{job_id}/stop` — cooperatively stop a running crawl (Bearer auth)
- `POST /api/v1/crawl/{job_id}/resume` — resume a stopped/failed crawl from saved state (Bearer auth)
- `GET /api/v1/graph/{job_id}` — graph data; `?partial=true` reads partial/recovered results (Bearer auth)

The crawl request body supports the same core crawl controls as the CLI, including
dependent/dependency crawling, issue/PR activity (`crawl_issues`, `crawl_prs`),
`min_stars`, `max_dependents`, `batch_size`, and inline `epfl_entities` tagging.

Crawls are checkpointed to disk per round under `OPC_DATA_DIR`, so partial results
survive a stop, failure, or container restart.

Run locally:

```bash
export CRAWLER_GITHUB_TOKEN="ghp_..."
export API_TOKEN="my-secret-api-token"
uvicorn open_pulse_crawler.api:app --host 0.0.0.0 --port 8000
```

See `docs/API.md` for endpoint details and example payloads.

### Node identifiers

Every node in the exported graph (user, organization, repository, team) is
keyed by its canonical public URL — for example
`https://github.com/torvalds`, `https://github.com/torvalds/linux`, or
`https://github.com/orgs/acme/teams/core`. This is the single ID used as
the dict key in the JSON output, the `id` column in the nodes CSV, and the
`source`/`target` columns in the edges CSV.

Seed input still accepts short forms — `torvalds`, `owner/repo`, or a full
GitHub URL — and they are normalized to the canonical form internally.
Snapshots written under the old (login-keyed) format are not readable;
operators upgrading from a previous release should re-crawl.

## Docker and GUI

The repository ships a three-container stack:

- FastAPI backend (`api`)
- Streamlit GUI (`gui`)
- Nginx reverse proxy (`nginx`)

Start everything with Docker Compose:

```bash
cp .env.dist .env
docker compose -f infra/docker-compose.yml up -d --build
```

Open:

- `http://localhost/` for the Streamlit GUI
- `http://localhost/api/v1/health` for API health
- `http://localhost/api/v1/docs` for Swagger docs

If you set `OPC_PORT` (for example `OPC_PORT=8080`), replace `localhost` with
`localhost:<port>` in the URLs above.

See `docs/DEPLOYMENT.md` for full deployment options and integration checks.

## Usage

### Basic Usage

Crawl from command-line seeds:

```bash
open-pulse-crawler crawl caviri sdsc-ordes/gimie --rounds 2
```

### Using a Seed File

Create a `seeds.txt` file:

```txt
caviri
sdsc-ordes/gimie
https://github.com/torvalds/linux
torvalds
```

Run the crawler:

```bash
open-pulse-crawler crawl --seed-file seeds.txt --rounds 3
```

### Advanced Options

```bash
open-pulse-crawler crawl \
  --seed-file seeds.txt \
  --rounds 3 \
  --output-dir ./results \
  --cache-dir ./cache \
  --state-file state.json \
  --visualize \
  --visualize-clusters \
  --verbose
```

### Resume from Saved State

```bash
open-pulse-crawler crawl --resume --state-file state.json
```

### Dependency & Dependent Crawling

The crawler can also discover relationships based on repository dependencies (using GitHub's SBOM) and dependents (using the "Used by" graph).

```bash
open-pulse-crawler crawl DeepLabCut/DeepLabCut \
  --rounds 1 \
  --crawl-dependencies \
  --crawl-dependents \
  --min-stars 10 \
  --max-dependents 50
```

- `--crawl-dependencies`: Crawl downstream dependencies (what the repo uses).
- `--crawl-dependents`: Crawl upstream dependents (who uses the repo).
- `--min-stars N`: Filter dependents/dependencies by minimum star count (default: 0).
- `--max-dependents N`: Limit the number of dependents to fetch per repository (default: all).

### Command-Line Options

#### Basic Options
- `seeds`: Initial seed nodes (users, orgs, or repos)
- `--seed-file, -f`: Path to file containing seed nodes (one per line)
- `--rounds, -r`: Number of BFS rounds to perform (default: 3)
- `--output-dir, -o`: Directory for output files (default: ./output)
- `--cache-dir, -c`: Directory for caching API responses (default: `$OPC_CACHE_DIR` or `data/open-pulse-crawler/cache`). Cached entries expire after `$OPC_CACHE_TTL_DAYS` (default 30); see [docs/DEPLOYMENT.md → Caching](docs/DEPLOYMENT.md#caching).
- `--no-cache`: Disable API response caching
- `--state-file, -s`: File to save/load crawler state
- `--resume`: Resume from saved state file
- `--no-json`: Skip JSON output
- `--no-csv`: Skip CSV output
- `--visualize, -v`: Generate graph visualization (PNG)
- `--verbose`: Enable verbose logging
- `--epfl-list`: Path to file containing EPFL entities (one per line) to flag in output

#### Dependency Options (New!)
- `--crawl-dependencies`: Crawl downstream dependencies (SBOM)
- `--crawl-dependents`: Crawl upstream dependents ("Used by")
- `--min-stars`: Minimum stars for filtering dependents/dependencies (default: 0)
- `--max-dependents`: Maximum number of dependents to fetch (default: all)
- `--max-contributors`: Skip contributor expansion for repos with more than N contributors. The repo node still lands in the graph (with owner / fork / deps); only its contributors are not queued. Useful for avoiding mega-projects (e.g. linux kernel) that would dominate the BFS frontier. The total count is cached, so this is roughly free on re-crawls. Default: unlimited.

#### Issue & PR Activity Options
- `--crawl-issues`: Fetch issue authors and conversation commenters per repo (opt-in)
- `--crawl-prs`: Fetch PR authors, conversation commenters, and reviewers per repo (opt-in)
- `--issue-max`: Max issues scanned per repo when `--crawl-issues` is set (default: 100)
- `--pr-max`: Max PRs scanned per repo when `--crawl-prs` is set (default: 100)

These are opt-in because issues/PRs paginate heavily on busy repos. They emit
`issue_author`, `pr_author`, `commented_on`, and `pr_reviewer` edges.

#### Rate Limiting Options (New!)
- `--request-delay`: Minimum delay in seconds between API requests (default: 0.0)
- `--max-concurrent`: Maximum number of concurrent API requests (default: 5)
- `--rate-limit-buffer`: Buffer of requests to keep before waiting (default: 50)

See [docs/CONCURRENCY.md](./docs/CONCURRENCY.md) for the detailed guide on concurrency, rate limiting, and multi-token rotation.

## Output Formats

### JSON Output

Complete graph data with all discovered entities:

```json
{
  "users": {
    "caviri": {
      "login": "caviri",
      "name": "Carlos Vivar",
      "id": 12345,
      "type": "User",
      "authored_repositories": ["caviri/repo1"],
      "forked_repositories": []
    }
  },
  "orgs": {...},
  "repos": {...}
}
```

### CSV Output (Edges)

Relationships between entities:

```csv
source,target,property,source_type,target_type
caviri,caviri/repo1,owner_of,user,repo
caviri,torvalds,follows,user,user
caviri,sdsc-ordes/gimie,starred,user,repo
repo1,repo2,parent_of,repo,repo
repo1,lib1,depends_on,repo,repo
```

Edge `property` values:

| Property         | Direction        | Meaning                                         |
| ---------------- | ---------------- | ----------------------------------------------- |
| `owner_of`       | user/org → repo  | Owns the repository                             |
| `contributor_of` | user/org → repo  | Contributed to the repository                   |
| `parent_of`      | repo → repo      | Upstream repo of a fork                         |
| `parent_of`      | team → team      | Parent of a nested team                         |
| `depends_on`     | repo → repo      | Dependency / dependent edge                     |
| `follows`        | user → user      | Follows the target user                         |
| `starred`        | user → repo      | Starred the repository                          |
| `watching`       | user → repo      | Watching (subscribed to) the repository         |
| `has_team`       | org → team       | Org contains the team                           |
| `has_access`     | team → repo      | Team has access to the repository               |
| `issue_author`   | user → repo      | Opened an issue (opt-in `--crawl-issues`)       |
| `pr_author`      | user → repo      | Opened a pull request (opt-in `--crawl-prs`)    |
| `commented_on`   | user → repo      | Commented on an issue/PR (opt-in)               |
| `pr_reviewer`    | user → repo      | Reviewed a pull request (opt-in `--crawl-prs`)  |

Edges are only emitted between nodes that are both present in the graph.

### CSV Output (Nodes)

All discovered nodes (including unexplored frontier nodes):

```csv
id,name,type,is_seed,is_explored,exploration_timestamp,is_epfl
caviri,Carlos Vivar,user,true,true,2025-11-19T10:00:00,false
sdsc-ordes/gimie,gimie,repo,true,true,2025-11-19T10:00:05,true
torvalds,Linus Torvalds,user,false,false,,false
```

Node `type` is one of `user`, `org`, `repo`, or `team`.

### Visualization

When `--visualize` is enabled, generates a PNG image with:
- Color-coded nodes (users=blue, orgs=red, repos=green)
- Seed nodes shown as squares
- Regular nodes shown as circles
- Directed edges showing relationships

## How It Works

1. **Seed Parsing**: Accepts GitHub URLs, usernames, or org/repo identifiers
2. **BFS Expansion**: For each round:
   - Processes all nodes in the current level
   - Discovers connected entities (repos, members, contributors)
   - Adds new entities to the queue for the next round
3. **Relationship Mapping**:
   - Users/Orgs → Repos: "owner of" or "contributor of"
   - Users → Orgs: "member of"
   - Repos → Repos: "parent of" (for forks) or "depends_on" (for dependencies)
4. **Caching**: Stores API responses to avoid redundant calls
5. **Rate Limiting**: Automatically handles GitHub API rate limits with token rotation

## Project Structure

```
src/open_pulse_crawler/
├── __init__.py          # Package initialization
├── models.py            # Pydantic models for GitHub entities
├── platforms/
│   └── github/
│       ├── client.py    # GitHub REST client (PyGithub) with caching
│       └── graphql.py   # GitHub GraphQL client
├── crawler.py           # BFS crawler core logic
├── io_utils.py          # Input/output handlers
├── visualization.py     # Graph visualization
└── cli.py              # Command-line interface
```

## Progress Tracking

The crawler now includes real-time progress tracking with **tqdm** and **human-readable timestamps**:

- **Overall round progress**: Shows completion percentage and ETA across all rounds
- **Per-round progress**: Displays node processing progress within each round
- **Live statistics**: Real-time updates of nodes, users, orgs, repos, and queue size
- **Timestamps**: Start time, end time, and duration in human-readable format
- **Round timestamps**: See when each BFS round begins

Example progress output:

```
🚀 Crawl started at 2025-10-02 14:30:15
📊 Target: 3 rounds

Overall Progress:  67%|████████████▋      | 2/3 [00:45<00:22] nodes=156 users=12 orgs=3 repos=141 queue=234
Round 2 [14:30:47]:   100%|████████████████████| 156/156 [00:18<00:00,  8.67node/s]

✅ Crawl completed at 2025-10-02 14:31:38
⏱️  Total duration: 1m 23s
📦 Collected: 56 users, 8 orgs, 170 repos
```

See [PROGRESS_TRACKING.md](./docs/PROGRESS_TRACKING.md) and [TIMESTAMPS.md](./docs/TIMESTAMPS.md) for more details.

## Statistics and Monitoring

The crawler provides detailed statistics:

- Nodes processed per round
- API calls made and cache hits
- Rate limit waits and token switches
- Time taken per round
- Total entities discovered

Example output:

```
╭─────────────────────────── Crawl Statistics ────────────────────────────╮
│ Metric                      │ Value                                     │
├─────────────────────────────┼───────────────────────────────────────────┤
│ Rounds Completed            │ 3                                         │
│ Total Nodes Visited         │ 150                                       │
│ Users Discovered            │ 45                                        │
│ Organizations Discovered    │ 12                                        │
│ Repositories Discovered     │ 93                                        │
│ API Calls Made              │ 200                                       │
│ Cache Hits                  │ 50                                        │
╰─────────────────────────────┴───────────────────────────────────────────╯
```

## License

Apache 2.0

## Author

caviri
