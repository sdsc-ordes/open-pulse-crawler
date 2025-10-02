# Visualization Improvements Summary

## What Changed

I've significantly improved the graph visualization functionality with three major enhancements:

### 1. 🎨 Modern Dark Theme
The visualizations now use a sleek, modern dark theme that looks professional and is easier on the eyes:
- **Dark gray background** (#2b2b2b) instead of white
- **Bright, contrasting colors**:
  - Cyan (#00d9ff) for Users
  - Gold/Yellow (#ffcc00) for Organizations  
  - Bright Green (#00ff88) for Repositories
- **White edges and labels** for maximum contrast
- **Professional appearance** suitable for presentations and technical documentation

### 2. 🔄 Smart Cluster Handling
The main visualization now intelligently handles disconnected graph components:
- **Automatic detection** of disconnected clusters
- **Grid layout** that positions clusters separately to prevent overlap
- **Cluster count** displayed in the title
- **Proper spacing** between components (3.0 units)

### 3. 📊 Individual Cluster Visualizations
New feature to generate separate visualization files for each cluster:
- **One PNG per cluster** (e.g., `cluster_01.png`, `cluster_02.png`)
- **Sorted by size** - largest clusters first
- **Detailed statistics** - each shows node and edge counts
- **Better focus** - examine each cluster in detail without overlap

## How to Use

### Command Line

Generate main visualization:
```bash
open-pulse-crawler crawl --seed-file seeds.txt --visualize
```

Generate cluster visualizations:
```bash
open-pulse-crawler crawl --seed-file seeds.txt --visualize-clusters
```

Generate both:
```bash
open-pulse-crawler crawl --seed-file seeds.txt --visualize --visualize-clusters
```

### Python API

```python
from pathlib import Path
from open_pulse_crawler.visualization import visualize_graph, visualize_clusters

# Main visualization
visualize_graph(
    graph=crawler.graph,
    output_path=Path("output/graph.png"),
    seed_nodes=crawler.seed_nodes
)

# Cluster visualizations
visualize_clusters(
    graph=crawler.graph,
    output_dir=Path("output/clusters"),
    seed_nodes=crawler.seed_nodes
)
```

## Visual Improvements

### Node Styling
- Regular nodes: Circles with white borders (400 size)
- Seed nodes: Squares with thicker borders (700 size) - more prominent
- All nodes have slight transparency (85-95%) for aesthetics

### Edge Styling
- White color at 25% opacity - subtle but visible
- Curved arrows (arc3 style) - more organic look
- Larger arrowheads (size 15) - clearer directionality

### Labels
- **Smart display logic**:
  - Small graphs (<50 nodes): Show all labels
  - Large graphs: Only show seed labels
  - Cluster views: Show labels for graphs <30 nodes
- Bold white text with black outline for readability
- Truncated to prevent overlap

### Legend
- Dark gray background (#3a3a3a) with white border
- Semi-transparent (90% opacity)
- Shows node type counts in cluster visualizations

## Testing

I've created test scripts in `examples/`:
- `test_visualization.py` - Demo with synthetic data (3 clusters)
- `regenerate_enac_viz.py` - Regenerate ENAC data with new theme

The ENAC dataset revealed 10 disconnected clusters, each now properly visualized!

## Files Modified

1. **src/open_pulse_crawler/visualization.py**
   - Enhanced `visualize_graph()` with cluster detection and dark theme
   - Added new `visualize_clusters()` function

2. **src/open_pulse_crawler/cli.py**
   - Added `--visualize-clusters` flag
   - Integrated cluster visualization into the workflow

## Output Examples

### Before (Old Style)
- White background
- Overlapping disconnected clusters
- Basic blue/red/green colors
- Single visualization only

### After (New Style)
- Dark background (#2b2b2b)
- Clusters positioned in grid (no overlap)
- Modern cyan/gold/green palette  
- Multiple visualization options (main + clusters)
- Professional technical diagram appearance

## Requirements

```bash
pip install networkx matplotlib
```

These remain optional - if not installed, visualization is gracefully skipped.

## Documentation

Created comprehensive documentation in:
- `docs/VISUALIZATION.md` - Detailed feature guide and API reference
