# Quick Start: Progress Tracking

## What You Get

Real-time progress bars during GitHub crawling with:
- ✅ Percentage completion
- ✅ Time estimates (ETA)
- ✅ Live statistics (nodes, users, orgs, repos, queue size)

## Example Output

```
Overall Progress:  67%|████████████▋      | 2/3 [00:45<00:22] nodes=156 users=12 orgs=3 repos=141 queue=234
Round 2:          100%|████████████████████| 156/156 [00:18<00:00,  8.67node/s]
```

## How to Use

### Just run the crawler - it's automatic!

```bash
# Single seed
open-pulse-crawler crawl caviri --rounds 3

# Multiple seeds
open-pulse-crawler crawl caviri torvalds --rounds 2

# From file
open-pulse-crawler crawl --seed-file seeds.txt --rounds 5
```

## Reading the Progress Bars

### Top Bar: Overall Progress
- Shows which round you're on (e.g., 2/3 = round 2 of 3)
- Percentage complete (67% in the example)
- Time elapsed [00:45] and remaining <00:22>
- Statistics for current round:
  - `nodes=156` - processed this round
  - `users=12` - discovered this round  
  - `orgs=3` - discovered this round
  - `repos=141` - discovered this round
  - `queue=234` - waiting to be processed

### Bottom Bar: Current Round
- Shows node processing within current round
- Processing speed (8.67 nodes/second)
- Automatically closes when round finishes

## Programmatic Usage

```python
from open_pulse_crawler.github_client import GitHubClient
from open_pulse_crawler.crawler import GitHubCrawler

# Setup
client = GitHubClient(['your_token'])
crawler = GitHubCrawler(client, max_rounds=3)
crawler.add_seeds(['caviri'])

# Run with progress (default)
crawler.crawl()

# Run without progress bars
crawler.crawl(show_progress=False)
```

## Test It Out

Try the demo script:

```bash
export GITHUB_TOKEN="your_token_here"
cd examples
python test_progress.py
```

## Benefits

1. **Know your progress** - See exactly where you are
2. **Plan ahead** - Use ETA to manage your time
3. **Monitor health** - Watch for slowdowns or issues
4. **Stay informed** - Real-time statistics

## That's It!

No configuration needed. Progress tracking works automatically. 🎉

---

For more details, see [PROGRESS_TRACKING.md](./PROGRESS_TRACKING.md)
