# Visualization Feature Summary

## What's New? 🎉

Your GitHub network visualizations just got a major upgrade! Here's what changed:

### 1. 🎨 Color-Coded Edges by Relationship Type

**Before**: All edges were white and looked the same.

**Now**: Edges are color-coded to show different types of relationships:
- 🔴 **Red** - "Owner of" (user/org owns a repository)
- 🔵 **Teal** - "Contributor of" (user/org contributes to a repository)
- 💚 **Light Teal** - "Member of" (user is member of an organization)
- 💛 **Yellow** - "Parent of" (repository is a fork of another)

This makes it **instantly clear** what type of relationship connects any two nodes!

### 2. 🧲 Improved Force-Directed Layout

**Before**: Using basic spring layout with default parameters.

**Now**: Optimized Fruchterman-Reingold force-directed algorithm with:
- Adaptive spacing based on graph size (k = 0.8-1.0 / √n)
- More iterations (100 vs 50) for better convergence
- Larger scale (1.5-2.0) for better node separation
- Less overlap and more readable graphs

**Result**: Nodes are better spaced, clusters are more visible, and the overall graph is easier to understand!

### 3. 🏷️ Smart Label Placement with Arrows

**Before**: Labels were placed directly on top of nodes, causing clutter and making edges hard to see.

**Now**: Labels are intelligently positioned:
- ✨ Labels float near their nodes in empty spaces
- 🎯 White arrows point from labels to their nodes
- 🚫 Automatic collision detection prevents label overlap
- 📦 Labels have rounded backgrounds for better readability
- 🔄 Works for both main graph and individual cluster views

**Fallback**: If `adjustText` library is not installed, labels fall back to traditional on-node placement.

### 4. 📊 Enhanced Legend

**Before**: Legend only showed node types.

**Now**: Comprehensive legend showing:
- Node types (Users, Organizations, Repositories)
- Seed node indicator
- **All edge types** present in the graph with their colors
- Node counts per type in cluster views

### 5. 🎨 Modern Dark Theme (Already Had This!)

The sleek dark theme with bright colors makes graphs look professional and modern:
- Dark background (#2b2b2b)
- Bright cyan for users (#00d9ff)
- Gold/yellow for organizations (#ffcc00)
- Bright green for repositories (#00ff88)

## Installation

To get all the new features, make sure you have:

```bash
pip install networkx matplotlib adjustText
```

Or with uv:

```bash
uv pip install networkx matplotlib adjustText
```

## Usage

Nothing changed in how you use it! Just run:

```bash
# Main visualization with all new features
open-pulse-crawler crawl --seed-file seeds.txt --visualize

# Separate cluster visualizations
open-pulse-crawler crawl --seed-file seeds.txt --visualize-clusters

# Both!
open-pulse-crawler crawl --seed-file seeds.txt --visualize --visualize-clusters
```

## Examples

Check out the example scripts:

1. **Test with synthetic data**:
   ```bash
   python examples/test_visualization.py
   ```
   Creates a simple 3-cluster graph showing the features clearly.

2. **Regenerate ENAC data with new style**:
   ```bash
   python examples/regenerate_enac_viz.py
   ```
   Takes your existing ENAC graph and regenerates it with all the new features.

## Visual Comparison

### What You Get:

✅ **Color-coded edges** - See relationship types at a glance  
✅ **Better spacing** - Nodes don't overlap, clusters are clear  
✅ **Smart labels** - Labels with arrows, no clutter  
✅ **Complete legend** - Know what every color means  
✅ **Disconnected clusters** - Each cluster gets its own visualization  

### Perfect For:

- 📊 Research papers and presentations
- 📈 Network analysis and exploration
- 🎓 Teaching and demonstrations
- 📋 Documentation and reports
- 🔍 Understanding complex GitHub ecosystems

## Technical Details

- **Layout**: Fruchterman-Reingold force-directed algorithm via NetworkX spring_layout
- **Label positioning**: adjustText library with automatic collision detection
- **Edge rendering**: Curved arrows with arc3 connection style
- **Resolution**: 300 DPI (publication quality)
- **Format**: PNG with dark background preserved

## Tips

1. **Large graphs**: Only seed node labels are shown to avoid clutter. Use cluster views to see more detail.

2. **Cluster analysis**: If you have disconnected components, use `--visualize-clusters` to get individual high-quality images of each cluster.

3. **Customization**: Edit the color maps in `visualization.py` if you want different colors:
   - `color_map` for node colors
   - `edge_color_map` for edge colors

4. **Performance**: For graphs with 100+ nodes, label adjustment may take a few extra seconds. This is normal!

## Feedback

These improvements make it much easier to understand:
- Who owns what (red edges)
- Who contributes where (teal edges)  
- Who belongs to which organization (light teal edges)
- Which repos are forks (yellow edges)

Enjoy exploring your GitHub networks! 🚀
