#!/usr/bin/env python3
"""Test script to verify organic force-directed layout."""

import json
from pathlib import Path
from open_pulse_crawler.models import GraphData
from open_pulse_crawler.visualization import visualize_graph, visualize_clusters

# Load existing graph data
state_file = Path("data/enac/output/state.json")
if state_file.exists():
    with open(state_file) as f:
        state = json.load(f)
    
    graph = GraphData.model_validate(state['graph'])
    seed_nodes = set(state['seed_nodes'])
    
    print(f"Loaded graph with {len(graph.users)} users, {len(graph.orgs)} orgs, {len(graph.repos)} repos")
    
    # Test main visualization
    output_path = Path("data/enac/output/test_organic_layout.png")
    print(f"\nGenerating organic layout visualization...")
    visualize_graph(graph, output_path, seed_nodes, figsize=(24, 24), dpi=300)
    print(f"✓ Main visualization saved to {output_path}")
    
    # Test cluster visualization
    cluster_dir = Path("data/enac/output/test_clusters_organic")
    print(f"\nGenerating cluster visualizations with organic layout...")
    visualize_clusters(graph, cluster_dir, seed_nodes, figsize=(16, 16), dpi=300)
    print(f"✓ Cluster visualizations saved to {cluster_dir}")
    
    print("\n✅ All visualizations generated successfully!")
    print("Compare the new layouts - they should show more organic, natural distribution")
    print("instead of circular arrangements.")
else:
    print(f"State file not found: {state_file}")
    print("Run a crawl first to generate data.")
