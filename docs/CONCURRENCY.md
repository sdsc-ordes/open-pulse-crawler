# Concurrent Node Processing

## Overview

The Open Pulse Crawler processes GitHub entities (users, organizations, repositories) in parallel using Python's `ThreadPoolExecutor`. This provides significant performance improvements, especially when working with cached data.

## Performance

**Speedup with Cached Data**: **4-5x faster** with concurrent processing
- Sequential (batch_size=1): ~0.0034s per node
- Concurrent (batch_size=8): ~0.0008s per node  
- **Speedup: 4.55x**

**With Uncached Data**: Performance depends on API rate limits and response times
- Large organizations (100+ repos/members) can take 10-20s to process fully
- Concurrent processing still provides speedup by overlapping API calls
- Multiple nodes can be processed while waiting for API responses

## Configuration

### Batch Size

Controls how many nodes are processed in parallel:

```python
crawler = GitHubCrawler(
    client,
    max_rounds=3,
    batch_size=10  # Process 10 nodes concurrently
)
```

**CLI Usage:**
```bash
open-pulse-crawler crawl seeds.txt --batch-size 10
```

**Default**: Matches `max_concurrent_requests` from GitHubClient (default: 5)

### Max Concurrent Requests

Controls how many API calls can happen simultaneously:

```python
client = GitHubClient(
    tokens,
    max_concurrent_requests=5  # Max 5 API calls at once
)
```

**CLI Usage:**
```bash
open-pulse-crawler crawl seeds.txt --max-concurrent 5
```

## How It Works

### 1. Node Processing Pipeline

```
Round Start
    ↓
Collect nodes from queue
    ↓
ThreadPoolExecutor (batch_size workers)
    ↓
Process nodes in parallel:
    - Node 1 → _process_node() → Add to graph
    - Node 2 → _process_node() → Add to graph
    - Node 3 → _process_node() → Add to graph
    - ...
    ↓
All nodes complete
    ↓
Round End
```

### 2. Thread Safety

The crawler uses two locks to ensure thread-safe operations:

- **`visited_lock`**: Protects the visited set and queue operations
- **`graph_lock`**: Protects the graph data structure

**Lock Contention Optimization:**
- Operations are batched before acquiring locks
- Example: Collect all repos/members, then lock once to add all to queue
- Minimizes lock duration and contention

### 3. API Rate Limiting

The `GitHubClient` semaphore limits concurrent API calls:

```
batch_size=10, max_concurrent_requests=5

[Node 1] ─┐
[Node 2] ─┼─→ ThreadPoolExecutor (10 workers)
[Node 3] ─┤     ↓
[Node 4] ─┤   Each node makes API calls
[Node 5] ─┤     ↓
[Node 6] ─┤   Semaphore limits to 5 concurrent API calls
[Node 7] ─┤     ↓
[Node 8] ─┤   API calls are queued if limit reached
[Node 9] ─┤
[Node 10]─┘
```

## Best Practices

### 1. Choosing Batch Size

**Small batch_size (1-3)**:
- More predictable memory usage
- Better for rate-limited scenarios
- Easier to debug

**Medium batch_size (5-10)**:
- **Recommended for most use cases**
- Good balance of speed and resource usage
- Default configuration

**Large batch_size (15-50)**:
- Maximum speed with cached data
- Higher memory usage
- May hit rate limits faster with uncached data

### 2. Balancing with Rate Limits

```bash
# Conservative: Low concurrency, avoid rate limits
open-pulse-crawler crawl seeds.txt \
  --batch-size 3 \
  --max-concurrent 3 \
  --request-delay 0.5

# Balanced: Good speed, respects rate limits (recommended)
open-pulse-crawler crawl seeds.txt \
  --batch-size 10 \
  --max-concurrent 5

# Aggressive: Maximum speed, may hit rate limits
open-pulse-crawler crawl seeds.txt \
  --batch-size 20 \
  --max-concurrent 10 \
  --rate-limit-buffer 200
```

### 3. With Multiple Tokens

More tokens = more API quota = higher concurrency possible:

```bash
# With 5 tokens, can safely use higher concurrency
export GITHUB_TOKEN="token1,token2,token3,token4,token5"
open-pulse-crawler crawl seeds.txt \
  --batch-size 15 \
  --max-concurrent 10
```

## Performance Tips

### 1. Use Cache

Cache provides the biggest speedup:
```bash
# First run (slow, builds cache)
open-pulse-crawler crawl seeds.txt --rounds 2 --cache-dir ./cache

# Second run (fast, uses cache)
open-pulse-crawler crawl seeds.txt --rounds 3 --cache-dir ./cache
```

### 2. Monitor Progress

Watch the progress bars to understand performance:
```
Round 1 [12:00:15]: 100%|████████| 300/300 [00:12<00:00, 25.0 node/s]
```

If you see:
- **< 1s per node**: Mostly cached data, concurrency working well
- **1-5s per node**: Mix of cached and API calls, normal
- **> 10s per node**: Mostly API calls to large orgs, may need more tokens

### 3. Profile Different Settings

Test different configurations to find optimal settings:

```python
import time
from src.open_pulse_crawler import GitHubClient, GitHubCrawler

configs = [
    (1, 5),   # Sequential
    (5, 5),   # Small batch
    (10, 5),  # Medium batch
    (20, 10), # Large batch
]

for batch, max_concurrent in configs:
    client = GitHubClient(tokens, max_concurrent_requests=max_concurrent)
    crawler = GitHubCrawler(client, max_rounds=1, batch_size=batch)
    crawler.add_seeds(seeds)
    
    start = time.time()
    crawler.crawl(show_progress=False)
    elapsed = time.time() - start
    
    print(f"batch={batch}, max_concurrent={max_concurrent}: {elapsed:.2f}s")
```

## Implementation Details

### Thread Pool

Uses `concurrent.futures.ThreadPoolExecutor`:
```python
with ThreadPoolExecutor(max_workers=self.batch_size) as executor:
    future_to_node = {
        executor.submit(self._process_node, node_type, identifier): (node_type, identifier)
        for node_type, identifier in nodes_to_process
    }
    
    for future in as_completed(future_to_node):
        result = future.result()
        # Add to graph...
```

### Batched Queue Operations

Example from `_process_user`:
```python
# BAD: Acquire lock for each item
for repo_data in cached_repos:
    with self.visited_lock:  # Lock acquired 100x for 100 repos!
        if repo_data['full_name'] not in self.visited:
            self.queue.append(...)

# GOOD: Batch and lock once
repos_to_queue = []
for repo_data in cached_repos:
    repos_to_queue.append(repo_data['full_name'])

with self.visited_lock:  # Lock acquired once
    for repo_name in repos_to_queue:
        if repo_name not in self.visited:
            self.queue.append(...)
```

## Troubleshooting

### Issue: No speedup with concurrency

**Cause**: All API calls hitting rate limits, waiting sequentially

**Solution**: 
- Add more tokens
- Reduce batch_size to match available API quota
- Add request_delay to spread out requests

### Issue: Memory usage too high

**Cause**: Large batch_size with many large organizations

**Solution**:
- Reduce batch_size
- Process in smaller rounds
- Use state file to save progress and restart

### Issue: API rate limit errors

**Cause**: Too many concurrent requests exhausting token quota

**Solution**:
- Increase `rate_limit_buffer` (e.g., 200)
- Reduce `max_concurrent_requests`
- Add more tokens

## See Also

- [Progress Tracking](./PROGRESS_TRACKING.md)
- [Quick Reference](./QUICK_REFERENCE.md)
- [Architecture](./PROGRESS_ARCHITECTURE.md)
