"""Visualization utilities for graph rendering."""

import logging
from pathlib import Path
from typing import Set, Optional

from .models import GraphData

logger = logging.getLogger(__name__)

try:
    import networkx as nx
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    VISUALIZATION_AVAILABLE = True
except ImportError:
    VISUALIZATION_AVAILABLE = False
    logger.warning("Visualization libraries not available. Install networkx and matplotlib for graph visualization.")


def visualize_graph(
    graph: GraphData,
    output_path: Path,
    seed_nodes: Set[str],
    figsize: tuple = (20, 20),
    dpi: int = 300
):
    """
    Visualize the graph with color-coded node types.
    
    Args:
        graph: GraphData to visualize
        output_path: Path to save the visualization
        seed_nodes: Set of initial seed nodes (rendered as squares)
        figsize: Figure size in inches
        dpi: Resolution in dots per inch
    """
    if not VISUALIZATION_AVAILABLE:
        logger.error("Visualization requires networkx and matplotlib. Install with: pip install networkx matplotlib")
        return
    
    try:
        # Create directed graph
        G = nx.DiGraph()
        
        # Add nodes with attributes
        for user in graph.users.values():
            G.add_node(
                user.login,
                node_type='user',
                is_seed=user.login in seed_nodes,
                label=user.name or user.login
            )
        
        for org in graph.orgs.values():
            G.add_node(
                org.login,
                node_type='org',
                is_seed=org.login in seed_nodes,
                label=org.name or org.login
            )
        
        for repo in graph.repos.values():
            G.add_node(
                repo.full_name,
                node_type='repo',
                is_seed=repo.full_name in seed_nodes,
                label=repo.name or repo.full_name
            )
        
        # Add edges
        # Users -> repos
        for user in graph.users.values():
            for repo_name in user.authored_repositories:
                if repo_name in graph.repos:
                    G.add_edge(user.login, repo_name, relationship='owner of')
            for repo_name in user.forked_repositories:
                if repo_name in graph.repos:
                    G.add_edge(user.login, repo_name, relationship='contributor of')
        
        # Orgs -> repos and members
        for org in graph.orgs.values():
            for member in org.members:
                if member in graph.users:
                    G.add_edge(member, org.login, relationship='member of')
            for repo_name in org.authored_repositories:
                if repo_name in graph.repos:
                    G.add_edge(org.login, repo_name, relationship='owner of')
            for repo_name in org.forked_repositories:
                if repo_name in graph.repos:
                    G.add_edge(org.login, repo_name, relationship='contributor of')
        
        # Repos -> contributors and forks
        for repo in graph.repos.values():
            for contributor in repo.contributors:
                if contributor in graph.users or contributor in graph.orgs:
                    if contributor == repo.owner:
                        G.add_edge(contributor, repo.full_name, relationship='owner of')
                    else:
                        G.add_edge(contributor, repo.full_name, relationship='contributor of')
            
            if repo.is_fork and repo.forked_from and repo.forked_from in graph.repos:
                G.add_edge(repo.forked_from, repo.full_name, relationship='parent of')
        
        if len(G.nodes()) == 0:
            logger.warning("No nodes to visualize")
            return
        
        # Create figure
        fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
        
        # Layout
        try:
            pos = nx.spring_layout(G, k=0.5, iterations=50, seed=42)
        except:
            pos = nx.spring_layout(G, iterations=50, seed=42)
        
        # Define colors for node types
        color_map = {
            'user': '#3498db',     # Blue
            'org': '#e74c3c',      # Red
            'repo': '#2ecc71',     # Green
        }
        
        # Prepare node colors and shapes
        node_colors = []
        node_shapes = []
        seed_nodes_list = []
        regular_nodes_list = []
        
        for node in G.nodes():
            node_type = G.nodes[node].get('node_type', 'user')
            is_seed = G.nodes[node].get('is_seed', False)
            
            color = color_map.get(node_type, '#95a5a6')
            
            if is_seed:
                seed_nodes_list.append(node)
            else:
                regular_nodes_list.append(node)
            
            node_colors.append(color)
        
        # Draw regular nodes (circles)
        if regular_nodes_list:
            regular_colors = [color_map.get(G.nodes[n].get('node_type', 'user'), '#95a5a6') 
                            for n in regular_nodes_list]
            nx.draw_networkx_nodes(
                G, pos,
                nodelist=regular_nodes_list,
                node_color=regular_colors,
                node_size=300,
                node_shape='o',
                alpha=0.7,
                ax=ax
            )
        
        # Draw seed nodes (squares)
        if seed_nodes_list:
            seed_colors = [color_map.get(G.nodes[n].get('node_type', 'user'), '#95a5a6') 
                          for n in seed_nodes_list]
            nx.draw_networkx_nodes(
                G, pos,
                nodelist=seed_nodes_list,
                node_color=seed_colors,
                node_size=500,
                node_shape='s',
                alpha=0.9,
                ax=ax
            )
        
        # Draw edges
        nx.draw_networkx_edges(
            G, pos,
            edge_color='#7f8c8d',
            alpha=0.3,
            arrows=True,
            arrowsize=10,
            ax=ax
        )
        
        # Draw labels (only for seed nodes to avoid clutter)
        seed_labels = {n: G.nodes[n].get('label', n) for n in seed_nodes_list}
        if seed_labels:
            nx.draw_networkx_labels(
                G, pos,
                labels=seed_labels,
                font_size=8,
                font_weight='bold',
                ax=ax
            )
        
        # Create legend
        legend_elements = [
            mpatches.Patch(color=color_map['user'], label='User'),
            mpatches.Patch(color=color_map['org'], label='Organization'),
            mpatches.Patch(color=color_map['repo'], label='Repository'),
            mpatches.Patch(facecolor='gray', edgecolor='black', label='Seed Node (square)'),
        ]
        ax.legend(handles=legend_elements, loc='upper left', fontsize=10)
        
        # Title and styling
        ax.set_title(
            f'GitHub Network Graph\n'
            f'{len(graph.users)} Users, {len(graph.orgs)} Organizations, {len(graph.repos)} Repositories',
            fontsize=16,
            fontweight='bold'
        )
        ax.axis('off')
        
        # Save
        plt.tight_layout()
        plt.savefig(output_path, dpi=dpi, bbox_inches='tight')
        plt.close()
        
        logger.info(f"Graph visualization saved to {output_path}")
        logger.info(f"Nodes: {len(G.nodes())}, Edges: {len(G.edges())}")
    
    except Exception as e:
        logger.error(f"Failed to create visualization: {e}")
        raise
