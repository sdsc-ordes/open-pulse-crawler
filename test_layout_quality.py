#!/usr/bin/env python3
"""Diagnostic script to verify layout quality."""

import json
import math
from pathlib import Path
import networkx as nx
from open_pulse_crawler.models import GraphData

def analyze_layout_distribution(pos):
    """Analyze if layout is circular/symmetric or organic."""
    coords = list(pos.values())
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    
    # Calculate center
    center_x = sum(xs) / len(xs)
    center_y = sum(ys) / len(ys)
    
    # Calculate distances from center
    distances = [math.sqrt((x - center_x)**2 + (y - center_y)**2) 
                 for x, y in coords]
    
    # Calculate angles from center
    angles = [math.atan2(y - center_y, x - center_x) for x, y in coords]
    
    # Statistics
    avg_dist = sum(distances) / len(distances)
    std_dist = math.sqrt(sum((d - avg_dist)**2 for d in distances) / len(distances))
    
    # Check for circular pattern: if std_dev is very small, it's circular
    circularity = std_dist / avg_dist if avg_dist > 0 else 0
    
    return {
        'x_range': (min(xs), max(xs)),
        'y_range': (min(ys), max(ys)),
        'avg_distance': avg_dist,
        'std_distance': std_dist,
        'circularity': circularity,  # Low value = circular, high value = organic
        'aspect_ratio': (max(xs) - min(xs)) / (max(ys) - min(ys)) if (max(ys) - min(ys)) > 0 else 0
    }

# Load graph
state_file = Path("data/enac/output/state.json")
if state_file.exists():
    with open(state_file) as f:
        state = json.load(f)
    
    graph = GraphData.model_validate(state['graph'])
    
    # Create NetworkX graph
    G = nx.DiGraph()
    for user in graph.users.values():
        G.add_node(user.login, node_type='user')
    for org in graph.orgs.values():
        G.add_node(org.login, node_type='org')
    for repo in graph.repos.values():
        G.add_node(repo.full_name, node_type='repo')
    
    # Add edges
    for user in graph.users.values():
        for repo_name in user.authored_repositories:
            if repo_name in graph.repos:
                G.add_edge(user.login, repo_name)
        for repo_name in user.forked_repositories:
            if repo_name in graph.repos:
                G.add_edge(user.login, repo_name)
    
    print("=" * 70)
    print("LAYOUT QUALITY ANALYSIS")
    print("=" * 70)
    
    # Test different layouts
    n_nodes = len(G.nodes())
    print(f"\nGraph: {n_nodes} nodes, {len(G.edges())} edges")
    
    layouts = {}
    
    # Old way: Spring with seed
    print("\n1. Testing Spring layout WITH seed=42 (old way)...")
    optimal_k = 0.8 / math.sqrt(n_nodes) if n_nodes > 1 else 0.8
    pos_with_seed = nx.spring_layout(G, k=optimal_k, iterations=500, seed=42)
    layouts['Spring (seed=42)'] = analyze_layout_distribution(pos_with_seed)
    
    # Without seed
    print("2. Testing Spring layout WITHOUT seed...")
    pos_no_seed = nx.spring_layout(G, k=optimal_k, iterations=500, seed=None)
    layouts['Spring (no seed)'] = analyze_layout_distribution(pos_no_seed)
    
    # New way: Spectral
    print("3. Testing Spectral layout (NEW - uses eigenvectors)...")
    try:
        pos_spectral = nx.spectral_layout(G, scale=3.0)
        layouts['Spectral'] = analyze_layout_distribution(pos_spectral)
    except Exception as e:
        print(f"   Failed: {e}")
    
    # Hybrid: Random + limited spring
    print("4. Testing Random + Limited Spring (hybrid)...")
    pos_hybrid = nx.random_layout(G)
    pos_hybrid = nx.spring_layout(G, pos=pos_hybrid, k=1.5/math.sqrt(n_nodes), iterations=100, seed=None)
    layouts['Random+Spring'] = analyze_layout_distribution(pos_hybrid)
    
    # Display results
    print("\n" + "=" * 70)
    print("RESULTS:")
    print("=" * 70)
    
    for name, stats in layouts.items():
        print(f"\n{name}:")
        print(f"  X range: [{stats['x_range'][0]:.2f}, {stats['x_range'][1]:.2f}] "
              f"(span: {stats['x_range'][1] - stats['x_range'][0]:.2f})")
        print(f"  Y range: [{stats['y_range'][0]:.2f}, {stats['y_range'][1]:.2f}] "
              f"(span: {stats['y_range'][1] - stats['y_range'][0]:.2f})")
        print(f"  Aspect ratio: {stats['aspect_ratio']:.2f}")
        print(f"  Circularity score: {stats['circularity']:.3f} ", end="")
        
        if stats['circularity'] < 0.3:
            print("⚠️  VERY CIRCULAR/SYMMETRIC")
        elif stats['circularity'] < 0.5:
            print("⚠️  SOMEWHAT CIRCULAR")
        else:
            print("✅ ORGANIC DISTRIBUTION")
    
    print("\n" + "=" * 70)
    print("INTERPRETATION:")
    print("=" * 70)
    print("- Circularity < 0.3: Nodes arranged in circular/symmetric pattern")
    print("- Circularity > 0.5: Truly organic, varied distribution")
    print("- Aspect ratio ~1.0: Square layout (may indicate poor aspect)")
    print("=" * 70)
    
else:
    print("State file not found. Run a crawl first.")
