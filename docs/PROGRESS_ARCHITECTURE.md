# Progress Tracking Architecture

## Visual Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER RUNS CRAWLER                           │
│  $ open-pulse-crawler crawl caviri --rounds 3                       │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    CRAWLER INITIALIZATION                           │
│  • Load seeds                                                       │
│  • Setup GitHub client                                              │
│  • Create BFS queue                                                 │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│              OVERALL PROGRESS BAR CREATED (Position 0)              │
│  Overall Progress:   0%|                | 0/3 [00:00<?, ?round/s]   │
│                                                                     │
│  Shows:                                                             │
│    • Total rounds (0/3)                                             │
│    • Percentage (0%)                                                │
│    • Time elapsed/remaining                                         │
│    • Statistics per round                                           │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────┴────────────────────┐
        │      START ROUND 0 (BFS LEVEL 0)        │
        └────────────────────┬────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│           ROUND PROGRESS BAR CREATED (Position 1)                   │
│  Round 0:            0%|                | 0/5 [00:00<?, ?node/s]    │
│                                                                     │
│  Shows:                                                             │
│    • Nodes in round (0/5)                                           │
│    • Percentage (0%)                                                │
│    • Processing speed (nodes/s)                                     │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────┴────────────────────┐
        │       PROCESS EACH NODE IN QUEUE        │
        │                                         │
        │  For each node:                         │
        │    1. Fetch from GitHub API             │
        │    2. Extract relationships             │
        │    3. Add connected nodes to queue      │
        │    4. Update round progress bar         │
        └────────────────────┬────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ROUND PROGRESS UPDATES                           │
│  Round 0:           40%|████      | 2/5 [00:05<00:07,  2.5s/node]   │
│  Round 0:           60%|██████    | 3/5 [00:08<00:05,  2.5s/node]   │
│  Round 0:          100%|██████████| 5/5 [00:12<00:00,  2.4s/node]   │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ROUND COMPLETE - CLEANUP                         │
│  • Close round progress bar (leave=False)                           │
│  • Calculate round statistics                                       │
│  • Update overall progress bar                                      │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│              OVERALL PROGRESS BAR UPDATED                           │
│  Overall Progress:  33%|███▎      | 1/3 [00:12<00:24, 12.0s/round]  │
│                           nodes=5 users=3 orgs=1 repos=1 queue=45   │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────┴────────────────────┐
        │         NEXT ROUND (IF MORE WORK)       │
        └────────────────────┬────────────────────┘
                             │
        ┌────────────────────┴────────────────────┐
        │    Repeat for Round 1, Round 2, etc.    │
        └────────────────────┬────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ALL ROUNDS COMPLETE                              │
│  Overall Progress: 100%|██████████| 3/3 [02:15<00:00, 45.0s/round]  │
│                          nodes=234 users=56 orgs=8 repos=170 queue=0│
│                                                                     │
│  • Close overall progress bar                                       │
│  • Display final statistics table                                   │
└─────────────────────────────────────────────────────────────────────┘
```

## Data Flow

```
┌──────────────┐
│ Seeds Queue  │
│  [caviri]    │
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────┐
│        BFS Round 0 Queue                 │
│  [('user_or_org', 'caviri', 0)]          │
└──────┬───────────────────────────────────┘
       │
       ▼ Process 'caviri'
       │ → Found: User with 3 repos
       │
       ▼
┌──────────────────────────────────────────┐
│        BFS Round 1 Queue                 │
│  [('repo', 'caviri/repo1', 1),           │
│   ('repo', 'caviri/repo2', 1),           │
│   ('repo', 'caviri/repo3', 1)]           │
└──────┬───────────────────────────────────┘
       │
       ▼ Process each repo
       │ → Each repo has owner + contributors
       │
       ▼
┌──────────────────────────────────────────┐
│        BFS Round 2 Queue                 │
│  [('user', 'contributor1', 2),           │
│   ('user', 'contributor2', 2),           │
│   ...]                                   │
└──────────────────────────────────────────┘

As each node is processed:
  • Round progress bar increments
  • Statistics update in real-time
  • Queue grows with new discoveries
```

## Progress Bar Lifecycle

```
START CRAWL
    │
    ▼
Create Overall Progress Bar ────┐
    │                           │ [Persistent]
    │                           │ [Position 0]
    ▼                           │
FOR each round:                 │
    │                           │
    ▼                           │
    Create Round Progress Bar ──┼─┐
        │                       │ │ [Temporary]
        │                       │ │ [Position 1]
        ▼                       │ │ [leave=False]
        FOR each node:          │ │
            │                   │ │
            ▼                   │ │
            Process node        │ │
            Update round bar ───┘ │
            │                     │
        END FOR                   │
        │                         │
        ▼                         │
    Close Round Progress Bar ─────┘
    Update Overall Progress Bar ───┘
    │
END FOR
    │
    ▼
Close Overall Progress Bar
    │
    ▼
CRAWL COMPLETE
```

## Statistics Flow

```
Node Processing → Round Statistics → Overall Statistics
                                   
caviri (user)                      nodes: 1
  ├─ 3 repos      ──────────────→ users: 1
  └─ round: 0                      orgs: 0
                                   repos: 0
                                   queue: 3
                                   
Round 0 complete  ──────────────→ Update postfix display
                                   
repo1 (repo)                       nodes: 3
  ├─ 5 contributors ─────────────→ users: 0
  ├─ owner: caviri                 orgs: 0
  └─ round: 1                      repos: 3
                                   queue: 15
                                   
Round 1 complete  ──────────────→ Update postfix display

[Continue for all rounds...]
```

## Error Handling

```
try:
    Create overall progress bar
    │
    ▼
    While rounds remaining:
        │
        ▼
        Create round progress bar
        │
        ▼
        Process nodes
        │
        ▼
        Close round progress bar
        │
        ▼
        Update overall progress bar
        │
    END While
    │
except (KeyboardInterrupt, Exception):
    │
    ▼
    Save state (if enabled)
    │
finally:
    │
    ▼
    Close overall progress bar  ← Always executed
    │
    ▼
    Cleanup complete
```

## Key Design Decisions

1. **Two-Level Progress**
   - Gives both macro (rounds) and micro (nodes) views
   - Overall bar persists, round bar is temporary

2. **Position-Based Display**
   - `position=0`: Overall bar (top)
   - `position=1`: Round bar (bottom)
   - Prevents bars from jumping around

3. **leave=False for Round Bar**
   - Keeps terminal clean
   - Only overall progress persists
   - Reduces visual clutter

4. **postfix Statistics**
   - Shows live data without extra bars
   - Compact and informative
   - Updates in real-time

5. **Optional Disable**
   - `show_progress=False` parameter
   - Useful for logging/automation
   - Default is enabled for better UX

## Integration Points

```
CLI Command
    │
    ▼
cli.py: crawl()
    │
    ▼
crawler = GitHubCrawler(...)
    │
    ▼
crawler.crawl(show_progress=True)  ← Progress enabled here
    │
    ▼
    ┌─────────────────────────────┐
    │   Progress bars display     │
    │   Log messages appear above │
    │   Statistics in postfix     │
    └─────────────────────────────┘
    │
    ▼
Export results
Display statistics table
```

---

This architecture ensures:
- ✅ Clear progress visibility
- ✅ Accurate time estimates
- ✅ Minimal performance impact
- ✅ Clean terminal output
- ✅ Graceful error handling
