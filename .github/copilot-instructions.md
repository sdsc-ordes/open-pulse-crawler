# Copilot Instructions - Open Pulse Crawler

## Project Overview

**Open Pulse Crawler** is a GitHub network discovery tool using breadth-first search (BFS) to map relationships between users, organizations, and repositories. The crawler discovers entities layer-by-layer from seed nodes, tracking relationships like "owner of", "member of", "contributor of", and fork relationships.

**Core Architecture**: The system follows a pipeline pattern:
1. **Seeds** (users/orgs/repos) → **Queue** → **BFS Crawler** → **Graph Data** → **Exporters** (JSON/CSV/PNG)
2. The `GitHubClient` handles API calls with multi-token rotation, rate limiting, and file-based caching
3. Pydantic models (`UserModel`, `OrgModel`, `RepoModel`) enforce type safety throughout
4. The `GraphData` container maintains all discovered entities and relationships

## Development Environment

- **Python**: 3.13 (minimum 3.10)
- **Package Manager**: `uv` (preferred) or `pip`
- **Dependencies**: Install with `uv pip install -e .` or `uv pip install -e ".[viz,dev]"`
- **Virtual Environment**: Already configured if in dev container
- **Testing**: `pytest` in project root, tests in `tests/`

## Key Files and Responsibilities

### Core Engine (`src/open_pulse_crawler/`)
- **`models.py`**: Pydantic models for Users, Orgs, Repos, and GraphData container
- **`github_client.py`**: GitHub API wrapper with multi-token rotation, rate limiting (semaphore + delay), and MD5-keyed file caching
- **`crawler.py`**: BFS implementation using `deque` for queue, processes nodes in rounds, tracks visited nodes
- **`cli.py`**: Typer-based CLI with Rich formatting, loads tokens from env or `.env` file
- **`io_utils.py`**: Parsers and exporters for seeds, JSON, CSV (edges + nodes)
- **`visualization.py`**: NetworkX + Matplotlib graph rendering with modern dark theme, cluster detection

### Entry Points
- **CLI**: `open-pulse-crawler crawl [seeds] --rounds N --seed-file seeds.txt`
- **Programmatic**: See `examples/quick_start.py` for usage without CLI

## Critical Patterns

### 1. Token Management
GitHub tokens are loaded from `GITHUB_TOKEN` env var (comma-separated for multiple tokens):
```python
tokens = os.getenv('GITHUB_TOKEN', '').split(',')
```
- Falls back to `.env` file in project root
- Client rotates tokens when rate limits approach buffer threshold (default: 50 requests remaining)

### 2. API Rate Limiting Strategy
The `GitHubClient` uses **three-layer rate limiting**:
1. **Semaphore**: Limits concurrent requests (`max_concurrent_requests`, default: 5)
2. **Request Delay**: Minimum time between requests (`request_delay`, default: 0.0s)
3. **Proactive Checking**: Checks rate limit before each request, rotates tokens or waits if < buffer

```python
client = GitHubClient(
    tokens,
    cache_dir=Path('./cache'),
    request_delay=0.5,           # 0.5s between requests
    max_concurrent_requests=3,   # Max 3 concurrent
    rate_limit_buffer=100        # Wait when <100 requests left
)
```

### 3. Caching Mechanism
Cache uses MD5 hash of `endpoint:params` as filename:
- Cache hits return **dict** (not PyGithub objects)
- Cached data includes nested entities (e.g., user data includes their repos)
- Check cache type with `isinstance(obj, dict)`

### 4. BFS Queue Structure
Each queue item is `(type, identifier, round_number)`:
- Types: `'user'`, `'org'`, `'repo'`, `'user_or_org'` (needs detection)
- Queue processes all nodes of current round before advancing
- `visited` set prevents re-processing

### 5. Seed Parsing
Seeds accept multiple formats:
- Username: `caviri`
- Org/repo: `sdsc-ordes/gimie`
- Full URL: `https://github.com/torvalds/linux`
- URLs are normalized to remove `https://github.com/` prefix

### 6. State Management
State files enable resume functionality:
```python
state = {
    'current_round': int,
    'visited': list[str],
    'seed_nodes': list[str],
    'queue': list[tuple],
    'graph': dict,  # GraphData.model_dump()
    'round_stats': list[dict]
}
```
State is auto-saved after each round if `state_file` provided.

### 7. Progress Tracking
Uses `tqdm` for real-time progress:
- **Overall progress bar**: Tracks rounds with live stats (nodes, users, orgs, repos, queue)
- **Per-round progress bar**: Tracks node processing within round
- **Timestamps**: Human-readable start/end times and duration formatting
- Disable with `crawler.crawl(show_progress=False)` (programmatic only)

## Common Workflows

### Running Tests
```bash
pytest                    # All tests
pytest tests/test_models.py  # Specific test file
pytest -v                # Verbose
pytest --cov            # With coverage
```

### Development Commands
```bash
# Install for development
uv pip install -e ".[dev,viz]"

# Format code
black src/ tests/

# Lint
ruff check src/

# Run crawler
export GITHUB_TOKEN="ghp_token1,ghp_token2"
open-pulse-crawler crawl caviri --rounds 2 --cache-dir cache --visualize

# Programmatic usage
python examples/quick_start.py
```

### Adding New Entity Types
1. Add model to `models.py` (inherit from `BaseModel`)
2. Add container field to `GraphData`
3. Update `_process_*` method in `crawler.py`
4. Update export functions in `io_utils.py`
5. Update visualization color map in `visualization.py`

### Debugging Cache Issues
Check cache directory structure:
```bash
ls -lh cache/  # Each .json file is MD5(endpoint:params)
```
Delete cache to force fresh API calls:
```bash
rm -rf cache/
```

## Project-Specific Conventions

### Error Handling
- **404 errors**: Return `None` (entity doesn't exist)
- **403/429 errors**: Retry with exponential backoff + jitter
- **Rate limit exceeded**: Should never happen due to proactive checking, but handled as last resort

### Logging
- Uses Python `logging` module with Rich handlers
- Levels: DEBUG (verbose flag), INFO (default), WARNING, ERROR
- Format: `timestamp - module - level - message`

### Output Structure
```
output/
├── graph_YYYYMMDD_HHMMSS.json           # Full graph data
├── edges_YYYYMMDD_HHMMSS.csv            # Relationships
├── nodes_YYYYMMDD_HHMMSS.csv            # All entities
└── graph_YYYYMMDD_HHMMSS.png            # Visualization
```

### Visualization Features
- **Color coding**: Users=cyan, Orgs=yellow, Repos=green
- **Node shapes**: Seeds=squares, others=circles
- **Edge colors**: Relationship-specific (owner=red, contributor=teal, member=light-teal, fork=yellow)
- **Dark theme**: Modern technical diagram aesthetic (#2b2b2b background)
- **Cluster detection**: Separate visualizations for disconnected components with `--visualize-clusters`
- **Smart labels**: Uses `adjustText` library for non-overlapping labels with arrows (falls back to on-node labels)

## Documentation Location

All documentation goes in `docs/` folder:
- Architecture notes, implementation guides, feature docs
- Keep README.md as user-facing quickstart
- Example: `docs/PROGRESS_TRACKING.md`, `docs/VISUALIZATION.md`

## Edge Cases to Handle

1. **User vs Org ambiguity**: Use `user_or_org` type, try user first, then org
2. **Large contributor lists**: Limit to top 10 to avoid API abuse (see `crawler.py:_process_repository`)
3. **Disconnected graphs**: Visualization handles with grid layout (see `visualization.py:visualize_graph`)
4. **Forked repos**: Track both user→fork relationship AND fork→parent relationship
5. **Token exhaustion**: All tokens can run low; wait for reset of best token