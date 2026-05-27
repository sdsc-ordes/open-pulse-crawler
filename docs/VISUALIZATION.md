# Visualization Improvements

## Overview

The visualization module has been significantly improved with the following features:

### 🎨 Modern Dark Theme
- **Dark background** (#2b2b2b) for a modern technical diagram look
- **Bright, contrasting colors** optimized for dark backgrounds:
  - Users: Cyan (#00d9ff)
  - Organizations: Gold/Yellow (#ffcc00)
  - Repositories: Bright Green (#00ff88)
- **Color-coded edges** by relationship type:
  - Owner of: Red (#ff6b6b)
  - Contributor of: Teal (#4ecdc4)
  - Member of: Light Teal (#95e1d3)
  - Parent of (fork): Yellow (#ffd93d)
- **Professional appearance** suitable for presentations and documentation

### 🔄 Disconnected Cluster Handling
- **Automatic cluster detection**: Identifies disconnected components in the graph
- **Grid layout**: Positions multiple clusters in a grid to prevent overlap
- **Cluster information**: Shows the number of disconnected clusters in the title
- **Force-directed layout**: Optimized Fruchterman-Reingold algorithm for better node spacing
- **Adaptive parameters**: Layout adjusts based on graph size for optimal visualization

### 📊 Separate Cluster Visualizations
- **Individual cluster files**: Generate separate PNG files for each cluster
- **Better focus**: Each cluster is visualized independently for clarity
- **Sorted by size**: Clusters are numbered by size (largest first)
- **Detailed statistics**: Each cluster shows node counts and edge counts
- **Smart label placement**: Labels are positioned away from nodes with arrows pointing to them (when adjustText is installed)
- **Collision avoidance**: Automatic text positioning prevents label overlap

## Usage

### Command Line

Generate the main visualization with all clusters:
```bash
open-pulse-crawler crawl --seed-file seeds.txt --visualize
```

Generate separate visualizations for each cluster:
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

# Main visualization (all clusters in one image)
visualize_graph(
    graph=crawler.graph,
    output_path=Path("output/graph.png"),
    seed_nodes=crawler.seed_nodes,
    figsize=(24, 24),
    dpi=300
)

# Separate cluster visualizations
visualize_clusters(
    graph=crawler.graph,
    output_dir=Path("output/clusters"),
    seed_nodes=crawler.seed_nodes,
    figsize=(16, 16),
    dpi=300
)
```

## Features

### Node Styling
- **Regular nodes**: Circles with white borders
- **Seed nodes**: Squares with thicker white borders (more prominent)
- **Color coding**: Different colors for users, orgs, and repos
- **Transparency**: Slightly transparent for aesthetic appeal

### Edge Styling
- **Color-coded by relationship type**: Different colors for different edge types
  - Red: Ownership relationships
  - Teal: Contribution relationships
  - Light Teal: Membership relationships
  - Yellow: Fork/parent relationships
- **50% opacity**: Visible but not overwhelming
- **Curved arrows**: Using arc3 connection style with slight curvature
- **Directional**: Arrows show the direction of relationships
- **Thicker lines**: 2.0 width for better visibility

### Label Display
- **Smart labeling**: 
  - Small graphs (<50 nodes): Show all labels
  - Large graphs: Show only seed node labels
  - Cluster views: Show more labels (up to 30 nodes)
- **Intelligent positioning**: Labels placed away from nodes with arrows pointing to them
  - Uses adjustText library for automatic label positioning
  - Prevents overlap between labels and nodes
  - Falls back to traditional on-node labels if adjustText not installed
- **Styled text boxes**: Labels have rounded backgrounds with borders
- **Truncated labels**: Long names are truncated to avoid clutter
- **Bold white text**: Easy to read on dark background

### Legend
- **Modern styling**: Dark gray background with white border
- **Node counts**: Cluster visualizations show counts per type
- **Color coded**: Matches the node colors in the graph
- **Edge types**: Shows all relationship types present in the graph with their colors
- **Comprehensive**: Includes both node types and edge types for complete understanding

## Example Output

### Main Visualization
- Shows all clusters positioned in a grid layout
- Prevents overlap between disconnected components
- Title indicates the number of clusters
- Example: `20241002123456.graph.png`

### Cluster Visualizations
- Saved in a timestamped subdirectory
- One file per cluster: `cluster_01.png`, `cluster_02.png`, etc.
- Largest clusters numbered first
- Example directory: `20241002123456.clusters/`

## Technical Details

### Cluster Detection
Uses NetworkX's `weakly_connected_components()` to identify disconnected subgraphs in the directed graph.

### Layout Algorithm
- **Force-directed layout**: Uses NetworkX's spring layout (Fruchterman-Reingold algorithm)
- **Optimized parameters**: 
  - k = 0.8-1.0 / sqrt(n_nodes) for optimal node spacing
  - 100 iterations for better convergence
  - Scale of 1.5-2.0 for proper spacing
- **Single component**: Spring layout with adaptive parameters
- **Multiple components**: Grid arrangement with individual spring layout per component
- **Spacing**: 3.0 units between components in the grid

### Color Palette
Chosen for maximum contrast on dark backgrounds and modern technical aesthetic:
- Cyan (#00d9ff): High visibility for users
- Gold (#ffcc00): Stands out for organizations
- Green (#00ff88): Clear distinction for repositories
- White (#ffffff): Edges and labels

### Performance
- Handles graphs with hundreds of nodes efficiently
- Layout iterations: 50 (balanced quality/speed)
- DPI: 300 (high quality for printing/presentations)

## Requirements

### Required
```bash
pip install networkx matplotlib
```

### Optional (for enhanced features)
```bash
pip install adjustText
```

The adjustText library enables smart label placement with arrows. Without it, labels will be placed directly on nodes (traditional style).

If visualization libraries are not installed, visualization features will be skipped with a warning.
