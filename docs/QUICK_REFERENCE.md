# Quick Reference: Timestamps & Progress Tracking

## What You'll See

```
🚀 Crawl started at 2025-10-02 14:30:15
📊 Target: 3 rounds

Overall Progress:  67%|██████▋  | 2/3 [00:45<00:22] nodes=156 users=12 orgs=3 repos=141 queue=234
Round 2 [14:30:47]: 100%|████████| 156/156 [00:18<00:00,  8.67node/s]

✅ Crawl completed at 2025-10-02 14:31:38
⏱️  Total duration: 1m 23s
📦 Collected: 56 users, 8 orgs, 170 repos
```

## Understanding the Output

### Start Header
```
🚀 Crawl started at 2025-10-02 14:30:15
📊 Target: 3 rounds
```
- Shows when crawl begins
- Shows how many rounds planned

### Progress Bars
```
Overall Progress:  67%|██████▋  | 2/3 [00:45<00:22] nodes=156 users=12 orgs=3...
```
- `67%` = Percentage complete
- `2/3` = Current round / Total rounds
- `[00:45<00:22]` = Elapsed time / Time remaining
- `nodes=156` = Nodes processed this round
- `users=12` = Users found this round
- `orgs=3` = Organizations found this round
- `repos=141` = Repositories found this round
- `queue=234` = Nodes waiting to process

```
Round 2 [14:30:47]: 100%|████████| 156/156 [00:18<00:00,  8.67node/s]
```
- `[14:30:47]` = Time round started
- `156/156` = Nodes processed / Total nodes in round
- `8.67node/s` = Processing speed

### Completion Summary
```
✅ Crawl completed at 2025-10-02 14:31:38
⏱️  Total duration: 1m 23s
📦 Collected: 56 users, 8 orgs, 170 repos
```
- Shows end time
- Shows total time taken
- Shows final results

## Duration Formats

| Duration | Format | Example |
|----------|--------|---------|
| < 1 min  | `Xs`   | `42s`   |
| < 1 hour | `Xm Ys` | `5m 23s` |
| ≥ 1 hour | `Xh Ym Zs` | `2h 15m 47s` |

## Quick Commands

### See Demo
```bash
cd examples && python demo_progress.py
```

### Run Real Crawl
```bash
export GITHUB_TOKEN="your_token"
open-pulse-crawler crawl caviri --rounds 3
```

### Disable Progress
```python
# Programmatic only - no CLI option
crawler.crawl(show_progress=False)
```

## Documentation

- Full details: [PROGRESS_TRACKING.md](./PROGRESS_TRACKING.md)
- Timestamps: [TIMESTAMPS.md](./TIMESTAMPS.md)
- Main readme: [README.md](../README.md)

---

**Quick Tip**: Progress tracking is automatic - just run the crawler normally!
