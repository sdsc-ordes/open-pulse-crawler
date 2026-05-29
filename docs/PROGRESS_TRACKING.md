# Progress Tracking with tqdm

The Open Pulse Crawler now includes built-in progress tracking using [tqdm](https://github.com/tqdm/tqdm), providing real-time visibility into the crawling process.

## Features

The progress tracking system provides:

### 1. **Overall Round Progress**
- Shows the current round out of total rounds
- Displays percentage completion for the entire crawl
- Provides estimated time to completion (ETA)
- Shows statistics in real-time:
  - `nodes`: Number of nodes processed in the current round
  - `users`: Users discovered in the current round
  - `orgs`: Organizations discovered in the current round
  - `repos`: Repositories discovered in the current round
  - `queue`: Number of nodes waiting to be processed

### 2. **Per-Round Node Progress**
- Separate progress bar for each round
- Shows current node being processed out of total nodes in the round
- Displays percentage completion for the current round
- Provides ETA for the current round

### 3. **Time Estimates**
- Automatic calculation of time remaining based on processing speed
- Updates dynamically as the crawl progresses
- Helps plan for long-running crawls

## Usage

### Command Line Interface

Progress tracking is **enabled by default** when using the CLI:

```bash
# Progress bars will automatically appear
open-pulse-crawler crawl caviri --rounds 3

# With seed file
open-pulse-crawler crawl --seed-file seeds.txt --rounds 5
```

### Example Output

```
🚀 Crawl started at 2025-10-02 14:30:15
📊 Target: 3 rounds

Overall Progress:  67%|████████████▋      | 2/3 [00:45<00:22] nodes=156 users=12 orgs=3 repos=141 queue=234
Round 2 [14:30:47]:   100%|████████████████████| 156/156 [00:18<00:00,  8.67node/s]

✅ Crawl completed at 2025-10-02 14:31:38
⏱️  Total duration: 1m 23s
📦 Collected: 56 users, 8 orgs, 170 repos
```

### Programmatic Usage

When using the crawler as a library:

```python
from open_pulse_crawler.platforms.github import GitHubClient
from open_pulse_crawler.crawler import GitHubCrawler

client = GitHubClient(tokens)
crawler = GitHubCrawler(client, max_rounds=3)
crawler.add_seeds(['caviri'])

# Progress tracking enabled (default)
crawler.crawl(show_progress=True)

# Disable progress bars if needed
crawler.crawl(show_progress=False)
```

## Testing

A test script is provided to demonstrate the progress tracking:

```bash
cd examples
python test_progress.py
```

This will run a small crawl and show how the progress bars work.

## Benefits

1. **Visibility**: See exactly what's happening during the crawl
2. **Planning**: Use time estimates to plan your work
3. **Debugging**: Identify slow operations or stuck processes
4. **Monitoring**: Track progress without checking logs
5. **User Experience**: Clear feedback on long-running operations
6. **Timestamps**: Know when crawl started, ended, and duration in human-readable format
7. **Audit Trail**: Track execution times for reports and analysis

## Technical Details

### Implementation

- Uses `tqdm` library for progress bars
- Two-level progress tracking:
  1. Top level: Overall round progress (position=0)
  2. Second level: Current round node progress (position=1, leave=False)
- Progress bars are properly cleaned up even if the crawl is interrupted
- Compatible with logging output (uses different output streams)

### Performance Impact

- Minimal overhead (progress bar updates are very efficient)
- Can be disabled with `show_progress=False` if needed
- Does not affect API rate limiting or caching behavior

### Integration with Logging

Progress bars and log messages work together:
- Progress bars use position-based display
- Log messages appear above the progress bars
- No interference between progress tracking and logging

## Configuration

Currently, progress tracking cannot be disabled from the CLI (it's always on). To disable it, use the crawler programmatically with `show_progress=False`.

Future enhancements may include:
- CLI option to disable progress bars
- Customizable progress bar formats
- Additional statistics in the progress display
- Integration with web-based monitoring dashboards
