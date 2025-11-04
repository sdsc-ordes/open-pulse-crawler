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
- 🎯 **Relationship Mapping**: Tracks "owner of", "contributor of", "member of", "fork of", and "parent of" relationships
- 🚦 **Intelligent Rate Limiting**: Adaptive rate limit management with semaphores, delays, and multi-token rotation
- ⚡ **Concurrent Control**: Configurable request throttling to prevent API abuse

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

## Configuration

Set your GitHub personal access token(s) in the environment:

```bash
# Single token
export GITHUB_TOKEN="ghp_your_token_here"

# Multiple tokens (comma-separated for better rate limits)
export GITHUB_TOKEN="ghp_token1,ghp_token2,ghp_token3"
```

You can create a `.env` file in your project directory:

```bash
GITHUB_TOKEN=ghp_your_token_here
```

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

### Visualize Existing Graphs

You can visualize previously crawled graphs without re-crawling:

```bash
# Visualize a graph JSON file
open-pulse-crawler visualize data/output/graph_20251003_100015.json

# Specify custom output file
open-pulse-crawler visualize graph.json --output my_visualization.png

# Generate cluster visualizations
open-pulse-crawler visualize graph.json --clusters

# Verbose mode for debugging
open-pulse-crawler visualize graph.json -v
```

**Note**: The `visualize` command doesn't require GitHub tokens since it only reads existing graph JSON files!

### Command-Line Options

#### Basic Options
- `seeds`: Initial seed nodes (users, orgs, or repos)
- `--seed-file, -f`: Path to file containing seed nodes (one per line)
- `--rounds, -r`: Number of BFS rounds to perform (default: 3)
- `--output-dir, -o`: Directory for output files (default: ./output)
- `--cache-dir, -c`: Directory for caching API responses
- `--state-file, -s`: File to save/load crawler state
- `--resume`: Resume from saved state file
- `--no-json`: Skip JSON output
- `--no-csv`: Skip CSV output
- `--visualize, -v`: Generate graph visualization (PNG)
- `--verbose`: Enable verbose logging

#### Rate Limiting Options (New!)
- `--request-delay`: Minimum delay in seconds between API requests (default: 0.0)
- `--max-concurrent`: Maximum number of concurrent API requests (default: 5)
- `--rate-limit-buffer`: Buffer of requests to keep before waiting (default: 50)

See [RATE_LIMITING.md](./RATE_LIMITING.md) for detailed guide on rate limiting and API management.

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
caviri,caviri/repo1,owner of,user,repo
user1,org1,member of,user,org
repo1,repo2,parent of,repo,repo
```

### CSV Output (Nodes)

All discovered nodes:

```csv
id,name,type,is_seed
caviri,Carlos Vivar,user,true
sdsc-ordes/gimie,gimie,repo,true
torvalds,Linus Torvalds,user,false
```

### Visualization

When `--visualize` is enabled, generates a PNG image with:
- **Color-coded nodes**: Users=cyan, Orgs=yellow, Repos=green
- **Node shapes**: Seed nodes=squares, Regular nodes=circles
- **Edge types**: 
  - Owner relationships (red arrows)
  - Contributor relationships (teal arrows)
  - Member relationships (light teal arrows)
  - Fork relationships (yellow arrows)
  - **Bidirectional relationships** (purple double-headed arrows `<->`)
- **Smart label placement**: Non-overlapping labels with arrows (when adjustText is installed)
- **Cluster detection**: Separate visualizations for disconnected components with `--visualize-clusters`

## How It Works

1. **Seed Parsing**: Accepts GitHub URLs, usernames, or org/repo identifiers
2. **BFS Expansion**: For each round:
   - Processes all nodes in the current level
   - Discovers connected entities (repos, members, contributors)
   - Adds new entities to the queue for the next round
3. **Relationship Mapping**:
   - Users/Orgs → Repos: "owner of" or "contributor of"
   - Users → Orgs: "member of"
   - Repos → Repos: "parent of" (for forks)
4. **Caching**: Stores API responses to avoid redundant calls
5. **Rate Limiting**: Automatically handles GitHub API rate limits with token rotation

## Project Structure

```
src/open_pulse_crawler/
├── __init__.py          # Package initialization
├── models.py            # Pydantic models for GitHub entities
├── github_client.py     # GitHub API client with caching
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
