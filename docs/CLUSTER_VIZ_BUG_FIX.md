# Cluster Visualization Bug Fix

## Problem

When running the crawler with `--visualize-clusters`, the following error occurred:

```
Generating cluster visualizations...
✗ Cluster visualization failed: 'bool' object is not callable
```

## Root Cause

The issue was a **variable name shadowing** problem in `cli.py`:

1. The CLI parameter was named `visualize_clusters` (boolean)
2. The function was also imported as `visualize_clusters`
3. Inside the function scope, the parameter shadowed the imported function
4. When trying to call `visualize_clusters()`, Python tried to call the boolean parameter instead of the function

```python
# cli.py (BEFORE - BROKEN)
from .visualization import visualize_clusters  # Function import

def crawl(
    ...
    visualize_clusters: bool = typer.Option(...)  # Parameter shadows function!
):
    ...
    if visualize_clusters:  # This is the boolean
        visualize_clusters(...)  # ❌ Tries to call boolean as function!
```

## Solution

Renamed the imported function to avoid the name collision:

```python
# cli.py (AFTER - FIXED)
from .visualization import visualize_clusters as viz_clusters  # Import with alias

def crawl(
    ...
    visualize_clusters: bool = typer.Option(...)  # Parameter name kept
):
    ...
    if visualize_clusters:  # Boolean check
        viz_clusters(...)  # ✅ Calls the function correctly!
```

## Files Changed

- `src/open_pulse_crawler/cli.py`:
  - Line 21: Changed import to `visualize_clusters as viz_clusters`
  - Line 364: Changed function call to `viz_clusters(...)`

## Testing

```bash
# Test that cluster visualization works
open-pulse-crawler crawl \
  --seed-file data/enac/enac.seeds.txt \
  --rounds 1 \
  --output-dir test_output \
  --cache-dir data/enac/cache \
  --visualize-clusters

# Result: ✅ Success
# Cluster 1 visualization saved to test_output/clusters_.../cluster_01.png
# Cluster 2 visualization saved to test_output/clusters_.../cluster_02.png
# ...
```

## Lesson Learned

**Always avoid naming collisions between:**
- Function parameters and imported functions
- Local variables and module-level imports
- Class methods and their parameters

Use descriptive aliases when necessary to prevent shadowing.

## Status

✅ **Fixed** - Cluster visualization now works correctly
