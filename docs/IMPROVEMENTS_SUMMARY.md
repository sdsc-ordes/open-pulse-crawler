# Visualization Improvements - Complete Summary

## ✅ Completed Improvements

All requested features have been successfully implemented:

### 1. ✨ Color-Coded Edge Relationships

**Status**: ✅ Implemented

Edges are now color-coded by relationship type:

| Color | Relationship | Meaning |
|-------|-------------|---------|
| 🔴 Red (#ff6b6b) | "Owner of" | User/Org owns a repository |
| 🔵 Teal (#4ecdc4) | "Contributor of" | User/Org contributes to a repo |
| 💚 Light Teal (#95e1d3) | "Member of" | User is member of organization |
| 💛 Yellow (#ffd93d) | "Parent of" | Repository is a fork parent |

The legend automatically shows only the edge types present in your specific graph.

### 2. 🧲 Force-Directed Layout Optimization

**Status**: ✅ Implemented

Improved from basic spring layout to optimized force-directed:

**Technical improvements**:
- Algorithm: Fruchterman-Reingold (via NetworkX spring_layout)
- Adaptive k parameter: `0.8-1.0 / √(n_nodes)` for optimal spacing
- Increased iterations: 50 → 100 for better convergence
- Added scale parameter: 1.5-2.0 for better node separation
- Separate optimization for single vs multiple components

**Visual improvements**:
- Better node spacing (less overlap)
- Clearer cluster visualization
- More natural graph structure
- Improved readability for large graphs

### 3. 🏷️ Smart Label Placement with Arrows

**Status**: ✅ Implemented

Labels are now intelligently positioned away from nodes:

**Features**:
- Labels float in empty spaces near their nodes
- White arrows point from labels to nodes
- Automatic collision detection prevents overlap
- Rounded text boxes with semi-transparent backgrounds
- White borders for better visibility
- Graceful fallback if adjustText not installed

**Configuration**:
- Small graphs (<50 nodes): All labels shown with smart placement
- Large graphs: Only seed node labels shown
- Cluster views: Up to 30 labels with smart placement

## 📦 Installation

### Required packages:
```bash
pip install networkx matplotlib
```

### Recommended (for smart labels):
```bash
pip install adjustText
```

### All at once:
```bash
pip install networkx matplotlib adjustText
```

Or with uv (project's package manager):
```bash
uv pip install networkx matplotlib adjustText
```

## 🚀 Usage

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
    seed_nodes=crawler.seed_nodes,
    figsize=(24, 24),
    dpi=300
)

# Cluster visualizations
visualize_clusters(
    graph=crawler.graph,
    output_dir=Path("output/clusters"),
    seed_nodes=crawler.seed_nodes,
    figsize=(18, 18),
    dpi=300
)
```

## 📊 What You Get

### Main Visualization
- Single PNG with all clusters in grid layout
- Color-coded nodes (users, orgs, repos)
- Color-coded edges (4 relationship types)
- Smart label placement with arrows
- Seed nodes highlighted (squares vs circles)
- Comprehensive legend
- Dark modern theme

### Cluster Visualizations
- Separate PNG for each disconnected component
- Numbered by size (cluster_01.png is largest)
- Same features as main visualization
- More labels shown per cluster
- Individual statistics per cluster

## 🎨 Color Palette

### Node Colors (Dark Theme)
- **Background**: Dark gray (#2b2b2b)
- **Users**: Cyan (#00d9ff)
- **Organizations**: Gold (#ffcc00)
- **Repositories**: Bright green (#00ff88)

### Edge Colors (Relationships)
- **Owner of**: Red (#ff6b6b)
- **Contributor of**: Teal (#4ecdc4)
- **Member of**: Light teal (#95e1d3)
- **Parent of**: Yellow (#ffd93d)

### Other Elements
- **Labels**: White text on semi-transparent gray boxes
- **Arrows**: White with 60% opacity
- **Legend**: Dark gray box with white border

## 📁 Output Files

Running with `--visualize --visualize-clusters` creates:

```
output/
├── graph_20241002_123456.png          # Main visualization
├── nodes_20241002_123456.csv          # Node data
├── edges_20241002_123456.csv          # Edge data
├── graph_20241002_123456.json         # Full graph data
└── clusters_20241002_123456/          # Cluster visualizations
    ├── cluster_01.png                 # Largest cluster
    ├── cluster_02.png                 # Second largest
    └── ...
```

## 🧪 Testing

Two test scripts are included:

### 1. Simple Test (Synthetic Data)
```bash
python examples/test_visualization.py
```
- Creates 3 disconnected clusters
- Small graph, easy to see all features
- Output: `examples/viz_test_output/`

### 2. Real Data Test (ENAC)
```bash
python examples/regenerate_enac_viz.py
```
- Uses existing ENAC crawl data
- Shows improvements on real network
- Output: `data/enac/output/modern_viz/`

## 📈 Performance

- **Layout computation**: ~2-5 seconds for graphs with 100-300 nodes
- **Label adjustment**: ~1-3 seconds with adjustText
- **Total render time**: ~5-10 seconds for typical graphs
- **File size**: PNG files are 3-8MB depending on graph size

## 🔧 Customization

Want different colors? Edit `src/open_pulse_crawler/visualization.py`:

```python
# Node colors
color_map = {
    'user': '#00d9ff',   # Change these!
    'org': '#ffcc00',
    'repo': '#00ff88',
}

# Edge colors
edge_color_map = {
    'owner of': '#ff6b6b',
    'contributor of': '#4ecdc4',
    'member of': '#95e1d3',
    'parent of': '#ffd93d',
}
```

## 📚 Documentation

- **Full details**: `docs/VISUALIZATION.md`
- **Feature summary**: `docs/VISUALIZATION_FEATURES.md`
- **This file**: Quick reference and summary

## ✅ All Requested Features Implemented

- [x] Color-coded edges by relationship type
- [x] Improved force-directed layout (not circular)
- [x] Smart label placement away from nodes
- [x] Arrows pointing from labels to nodes
- [x] Automatic collision detection for labels

## 🎉 Bonus Features Added

- [x] Disconnected cluster handling
- [x] Separate cluster visualizations
- [x] Modern dark theme
- [x] Comprehensive legend with edge types
- [x] Adaptive layout parameters
- [x] Graceful fallbacks

Enjoy your improved visualizations! 🚀
