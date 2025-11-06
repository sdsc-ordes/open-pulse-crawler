# Timestamps Feature Update

## What's New

Human-readable timestamps have been added to the progress tracking, showing:
- **Start time** - When the crawl begins
- **Round times** - When each BFS round starts
- **End time** - When the crawl completes
- **Duration** - Total time elapsed in human-readable format (hours, minutes, seconds)

## Example Output

```
🚀 Crawl started at 2025-10-02 14:30:15
📊 Target: 3 rounds

Overall Progress:  33%|████▋        | 1/3 [00:15<00:30] nodes=45 users=12 orgs=3 repos=30 queue=234
Round 1 [14:30:20]:   100%|████████████████████| 45/45 [00:12<00:00,  3.75node/s]

Overall Progress:  67%|████████▋    | 2/3 [00:45<00:22] nodes=156 users=28 orgs=5 repos=123 queue=456
Round 2 [14:30:47]:   100%|████████████████████| 156/156 [00:18<00:00,  8.67node/s]

Overall Progress: 100%|█████████████| 3/3 [01:23<00:00] nodes=234 users=56 orgs=8 repos=170 queue=0
Round 3 [14:31:25]:   100%|████████████████████| 234/234 [00:25<00:00,  9.36node/s]

✅ Crawl completed at 2025-10-02 14:31:38
⏱️  Total duration: 1m 23s
📦 Collected: 56 users, 8 orgs, 170 repos
```

## Timestamp Information

### Start Timestamp
- **Format**: `YYYY-MM-DD HH:MM:SS`
- **Example**: `2025-10-02 14:30:15`
- **Shows**: Exact date and time when crawl begins

### Round Timestamps
- **Format**: `[HH:MM:SS]`
- **Example**: `Round 2 [14:30:47]`
- **Shows**: Time when each BFS round starts processing
- **Location**: In the progress bar description

### End Timestamp
- **Format**: `YYYY-MM-DD HH:MM:SS`
- **Example**: `2025-10-02 14:31:38`
- **Shows**: Exact date and time when crawl completes

### Duration
- **Format**: Adaptive based on length
  - Under 1 minute: `Xs` (e.g., `42s`)
  - Under 1 hour: `Xm Ys` (e.g., `5m 23s`)
  - 1 hour or more: `Xh Ym Zs` (e.g., `2h 15m 47s`)
- **Shows**: Total time from start to completion

## Benefits

1. **Audit Trail**: Know exactly when your crawl ran
2. **Planning**: See when each round starts to understand pacing
3. **Comparison**: Compare durations across different crawls
4. **Monitoring**: Track time of day for rate limit patterns
5. **Documentation**: Record timestamps for reports and analysis

## Log Output

Timestamps are also recorded in the log files:

```
2025-10-02 14:30:15 - open_pulse_crawler.crawler - INFO - Starting crawl at 2025-10-02 14:30:15 for 3 rounds
2025-10-02 14:30:30 - open_pulse_crawler.crawler - INFO - Round 0 completed: 45 nodes processed in 15.2s, 234 nodes in queue
2025-10-02 14:31:05 - open_pulse_crawler.crawler - INFO - Round 1 completed: 156 nodes processed in 34.8s, 456 nodes in queue
2025-10-02 14:31:38 - open_pulse_crawler.crawler - INFO - Round 2 completed: 234 nodes processed in 33.1s, 0 nodes in queue
2025-10-02 14:31:38 - open_pulse_crawler.crawler - INFO - Crawl completed after 3 rounds
2025-10-02 14:31:38 - open_pulse_crawler.crawler - INFO - Ended at 2025-10-02 14:31:38 (Duration: 1m 23s)
```

## Visual Elements

The timestamps use emoji icons for easy visual recognition:
- 🚀 Start time
- ⏱️  Duration
- ✅ Completion
- 📊 Statistics
- 📦 Results

## Implementation Details

### Changes Made

1. **Import datetime module**: Added to crawler.py
2. **Start timestamp**: Captured and displayed at crawl beginning
3. **Round timestamps**: Shown in each round's progress bar description
4. **End timestamp**: Captured and displayed at crawl completion
5. **Duration calculation**: Smart formatting based on elapsed time
6. **Log integration**: Timestamps in both console and log output

### Code Location

All timestamp functionality is in `src/open_pulse_crawler/crawler.py` in the `crawl()` method.

## Demo

Try the demo to see timestamps in action:

```bash
cd examples
python demo_progress.py
```

The demo will show:
- Start timestamp
- Round timestamps
- End timestamp
- Duration calculation

## No Configuration Required

Timestamps are automatically enabled when progress tracking is enabled (default behavior). No additional configuration needed!

---

**Last Updated**: October 2, 2025
