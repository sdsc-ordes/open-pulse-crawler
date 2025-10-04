"""Visualization utilities for graph rendering."""

import logging
import math
from pathlib import Path
from typing import Set, Optional

from .models import GraphData

logger = logging.getLogger(__name__)

try:
    import networkx as nx
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.patches import FancyArrowPatch
    from adjustText import adjust_text
    VISUALIZATION_AVAILABLE = True
    HAS_ADJUST_TEXT = True
except ImportError as e:
    if 'adjustText' in str(e):
        import networkx as nx
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.patches import FancyArrowPatch
        VISUALIZATION_AVAILABLE = True
        HAS_ADJUST_TEXT = False
        logger.warning("adjustText not available. Labels will be placed on nodes. Install with: pip install adjustText")
    else:
        VISUALIZATION_AVAILABLE = False
        HAS_ADJUST_TEXT = False
        logger.warning("Visualization libraries not available. Install networkx and matplotlib for graph visualization.")


def visualize_graph(
    graph: GraphData,
    output_path: Path,
    seed_nodes: Set[str],
    figsize: tuple = (24, 24),
    dpi: int = 300
):
    """
    Visualize the graph with color-coded node types using a modern dark theme.
    Handles disconnected components by positioning them separately.
    
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
                    G.add_edge(user.login, repo_name, relationship='owner_of')
            for repo_name in user.forked_repositories:
                if repo_name in graph.repos:
                    G.add_edge(user.login, repo_name, relationship='contributor_of')
        
        # Orgs -> repos and members
        for org in graph.orgs.values():
            for member in org.members:
                if member in graph.users:
                    G.add_edge(member, org.login, relationship='member_of')
            for repo_name in org.authored_repositories:
                if repo_name in graph.repos:
                    G.add_edge(org.login, repo_name, relationship='owner_of')
            for repo_name in org.forked_repositories:
                if repo_name in graph.repos:
                    G.add_edge(org.login, repo_name, relationship='contributor_of')
        
        # Repos -> contributors and forks
        for repo in graph.repos.values():
            for contributor in repo.contributors:
                if contributor in graph.users or contributor in graph.orgs:
                    if contributor == repo.owner:
                        G.add_edge(contributor, repo.full_name, relationship='owner_of')
                    else:
                        G.add_edge(contributor, repo.full_name, relationship='contributor_of')
            
            if repo.is_fork and repo.forked_from and repo.forked_from in graph.repos:
                G.add_edge(repo.forked_from, repo.full_name, relationship='parent_of')
        
        if len(G.nodes()) == 0:
            logger.warning("No nodes to visualize")
            return
        
        # Create figure with dark background
        fig, ax = plt.subplots(figsize=figsize, dpi=dpi, facecolor='#2b2b2b')
        ax.set_facecolor('#2b2b2b')
        
        # Handle disconnected components - position them separately
        components = list(nx.weakly_connected_components(G))
        logger.info(f"Found {len(components)} disconnected component(s)")
        
        pos = {}
        
        # CRITICAL: Use layouts that DON'T produce circular patterns
        # Spring/Fruchterman-Reingold inherently creates circular equilibrium
        # Instead, use a hybrid approach: spectral + force adjustment
        
        if len(components) > 1:
            # Multiple components - layout each independently
            component_data = []
            
            for idx, component in enumerate(components):
                subgraph = G.subgraph(component)
                n_nodes = len(component)
                
                # Choose layout based on connectivity and size
                if n_nodes == 1:
                    # Single node - place at origin
                    node = list(component)[0]
                    sub_pos = {node: (0, 0)}
                elif n_nodes <= 3:
                    # Very small - use simple positions
                    nodes = list(component)
                    sub_pos = {nodes[i]: (i * 2, 0) for i in range(len(nodes))}
                else:
                    # Use spectral layout for non-circular distribution
                    # Spectral uses eigenvectors, doesn't create circular patterns
                    try:
                        sub_pos = nx.spectral_layout(subgraph, scale=3.5)
                        # Add post-processing spring adjustment for extra repulsion
                        optimal_k = 2.0 / math.sqrt(n_nodes)
                        sub_pos = nx.spring_layout(
                            subgraph,
                            pos=sub_pos,
                            k=optimal_k,
                            iterations=50,  # Light adjustment for spacing
                            seed=None
                        )
                        logger.debug(f"Component {idx}: Using spectral+spring layout ({n_nodes} nodes)")
                    except:
                        # Fallback: use random + spring iterations with higher k
                        sub_pos = nx.random_layout(subgraph)
                        optimal_k = 2.0 / math.sqrt(n_nodes)  # Increased from 1.5 for more repulsion
                        sub_pos = nx.spring_layout(
                            subgraph,
                            pos=sub_pos,  # Start from random
                            k=optimal_k,
                            iterations=100,  # Limited iterations to avoid circular convergence
                            seed=None
                        )
                        logger.debug(f"Component {idx}: Using random+spring layout ({n_nodes} nodes)")
                
                # Calculate bounds
                xs = [p[0] for p in sub_pos.values()]
                ys = [p[1] for p in sub_pos.values()]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                width = (max_x - min_x) or 1.0
                height = (max_y - min_y) or 1.0
                center_x = (min_x + max_x) / 2
                center_y = (min_y + max_y) / 2
                
                component_data.append({
                    'component': component,
                    'pos': sub_pos,
                    'width': width,
                    'height': height,
                    'center': (center_x, center_y),
                    'size': n_nodes
                })
            
            # Sort by size (largest first)
            component_data.sort(key=lambda x: x['size'], reverse=True)
            
            # Arrange components in a SCATTERED pattern (not circle, not grid)
            # Use golden angle for optimal spacing
            golden_angle = math.pi * (3 - math.sqrt(5))  # ~137.5 degrees
            
            for i, data in enumerate(component_data):
                if i == 0:
                    # Place largest at center
                    offset_x, offset_y = 0, 0
                else:
                    # Spiral placement using golden angle
                    angle = i * golden_angle
                    # Distance increases with index (spiral out)
                    radius = math.sqrt(i) * 3
                    offset_x = radius * math.cos(angle)
                    offset_y = radius * math.sin(angle)
                
                # Place component
                for node, (x, y) in data['pos'].items():
                    pos[node] = (x - data['center'][0] + offset_x,
                               y - data['center'][1] + offset_y)
        else:
            # Single connected component
            n_nodes = len(G.nodes())
            
            logger.info(f"Using spectral layout for non-circular organic distribution ({n_nodes} nodes)")
            
            try:
                # Spectral layout uses graph eigenvectors - NO circular patterns
                pos = nx.spectral_layout(G, scale=4.0)
                # Add post-processing spring adjustment for extra repulsion
                optimal_k = 2.0 / math.sqrt(n_nodes) if n_nodes > 1 else 2.0
                pos = nx.spring_layout(
                    G,
                    pos=pos,
                    k=optimal_k,
                    iterations=50,  # Light adjustment for spacing
                    seed=None
                )
                logger.info(f"Using spectral+spring layout for optimal spacing ({n_nodes} nodes)")
            except:
                # Fallback: random initialization + spring with higher k
                logger.info("Spectral failed, using random + spring iterations")
                pos = nx.random_layout(G)
                optimal_k = 2.0 / math.sqrt(n_nodes) if n_nodes > 1 else 2.0  # Increased from 1.5
                pos = nx.spring_layout(
                    G,
                    pos=pos,
                    k=optimal_k,
                    iterations=100,  # Limited to avoid full circular convergence
                    seed=None
                )
        
        # Modern technical diagram color palette (dark theme)
        color_map = {
            'user': '#00d9ff',     # Cyan/Electric Blue
            'org': '#ffcc00',      # Gold/Yellow
            'repo': '#00ff88',     # Bright Green
        }
        
        # Edge color mapping by relationship type
        edge_color_map = {
            'owner_of': '#ff6b6b',        # Red - ownership
            'contributor_of': '#4ecdc4',  # Teal - contribution
            'member_of': '#95e1d3',       # Light teal - membership
            'parent_of': '#ffd93d',       # Yellow - fork relationship
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
        
        # Draw regular nodes (circles) with edge borders
        if regular_nodes_list:
            regular_colors = [color_map.get(G.nodes[n].get('node_type', 'user'), '#ffffff') 
                            for n in regular_nodes_list]
            nx.draw_networkx_nodes(
                G, pos,
                nodelist=regular_nodes_list,
                node_color=regular_colors,
                node_size=180,  # Reduced from 250 to prevent overlap
                node_shape='o',
                alpha=0.85,
                edgecolors='#ffffff',
                linewidths=1.5,
                ax=ax
            )
        
        # Draw seed nodes (squares) with thicker borders
        if seed_nodes_list:
            seed_colors = [color_map.get(G.nodes[n].get('node_type', 'user'), '#ffffff') 
                          for n in seed_nodes_list]
            nx.draw_networkx_nodes(
                G, pos,
                nodelist=seed_nodes_list,
                node_color=seed_colors,
                node_size=320,  # Reduced from 450 to prevent overlap
                node_shape='s',
                alpha=0.95,
                edgecolors='#ffffff',
                linewidths=2.5,
                ax=ax
            )
        
        # Draw edges with color coding by relationship type
        edge_lists_by_type = {}
        for u, v, data in G.edges(data=True):
            rel_type = data.get('relationship', 'unknown')
            if rel_type not in edge_lists_by_type:
                edge_lists_by_type[rel_type] = []
            edge_lists_by_type[rel_type].append((u, v))
        
        # Draw each edge type with its own color (straight lines)
        for rel_type, edges in edge_lists_by_type.items():
            edge_color = edge_color_map.get(rel_type, '#ffffff')
            nx.draw_networkx_edges(
                G, pos,
                edgelist=edges,
                edge_color=edge_color,
                alpha=0.5,
                arrows=True,
                arrowsize=15,
                arrowstyle='->',
                width=2.0,
                ax=ax
            )
        
        # Draw labels with smart positioning (offset from nodes)
        if len(G.nodes()) <= 50 or seed_nodes_list:
            # For small graphs, show all labels
            if len(G.nodes()) <= 50:
                labels_to_show = {n: G.nodes[n].get('label', n)[:20] for n in G.nodes()}
            else:
                # For large graphs, only show seed node labels
                labels_to_show = {n: G.nodes[n].get('label', n)[:20] for n in seed_nodes_list}
            
            if HAS_ADJUST_TEXT and len(labels_to_show) > 0:
                # Use adjustText for smart label placement with arrows
                texts = []
                for node, label in labels_to_show.items():
                    x, y = pos[node]
                    text = ax.text(
                        x, y, label,
                        fontsize=7,
                        fontweight='bold',
                        color='#ffffff',
                        ha='center',
                        va='center',
                        bbox=dict(boxstyle='round,pad=0.3', facecolor='#3a3a3a', edgecolor='#ffffff', linewidth=0.5, alpha=0.8)
                    )
                    texts.append(text)
                
                # Adjust text positions to avoid overlap and add arrows
                adjust_text(
                    texts,
                    arrowprops=dict(arrowstyle='->', color='#ffffff', lw=0.8, alpha=0.6),
                    expand_points=(1.5, 1.5),
                    force_text=(0.5, 0.5),
                    force_points=(0.2, 0.2),
                    ax=ax
                )
            else:
                # Fallback: place labels on nodes (old behavior)
                nx.draw_networkx_labels(
                    G, pos,
                    labels=labels_to_show,
                    font_size=7,
                    font_weight='bold',
                    font_color='#ffffff',
                    ax=ax
                )
        
        # Create legend with modern styling (nodes and edges)
        legend_elements = [
            mpatches.Patch(facecolor=color_map['user'], label='User', edgecolor='#ffffff', linewidth=1),
            mpatches.Patch(facecolor=color_map['org'], label='Organization', edgecolor='#ffffff', linewidth=1),
            mpatches.Patch(facecolor=color_map['repo'], label='Repository', edgecolor='#ffffff', linewidth=1),
            mpatches.Patch(facecolor='#666666', edgecolor='#ffffff', linewidth=2, label='Seed Node (square)'),
            mpatches.Patch(facecolor='none', edgecolor='none', label=''),  # Spacer
            mpatches.Patch(facecolor=edge_color_map['owner_of'], label='Owner of', edgecolor='#ffffff', linewidth=1),
            mpatches.Patch(facecolor=edge_color_map['contributor_of'], label='Contributor of', edgecolor='#ffffff', linewidth=1),
            mpatches.Patch(facecolor=edge_color_map['member_of'], label='Member of', edgecolor='#ffffff', linewidth=1),
            mpatches.Patch(facecolor=edge_color_map['parent_of'], label='Parent of (fork)', edgecolor='#ffffff', linewidth=1),
        ]
        legend = ax.legend(
            handles=legend_elements, 
            loc='upper left', 
            fontsize=11,
            framealpha=0.9,
            facecolor='#3a3a3a',
            edgecolor='#ffffff',
            labelcolor='#ffffff'
        )
        
        # Title and styling with modern aesthetics
        component_info = f' | {len(components)} cluster(s)' if len(components) > 1 else ''
        ax.set_title(
            f'GitHub Network Graph\n'
            f'{len(graph.users)} Users • {len(graph.orgs)} Organizations • {len(graph.repos)} Repositories{component_info}',
            fontsize=18,
            fontweight='bold',
            color='#ffffff',
            pad=20
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


def visualize_clusters(
    graph: GraphData,
    output_dir: Path,
    seed_nodes: Set[str],
    figsize: tuple = (16, 16),
    dpi: int = 300
):
    """
    Create separate visualizations for each disconnected cluster in the graph.
    
    Args:
        graph: GraphData to visualize
        output_dir: Directory to save cluster visualizations
        seed_nodes: Set of initial seed nodes
        figsize: Figure size for each cluster visualization
        dpi: Resolution in dots per inch
    """
    if not VISUALIZATION_AVAILABLE:
        logger.error("Visualization requires networkx and matplotlib. Install with: pip install networkx matplotlib")
        return
    
    try:
        # Create directed graph
        G = nx.DiGraph()
        
        # Add nodes with attributes (same as main visualization)
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
        
        # Add edges (same as main visualization)
        for user in graph.users.values():
            for repo_name in user.authored_repositories:
                if repo_name in graph.repos:
                    G.add_edge(user.login, repo_name, relationship='owner_of')
            # User contributed repos
            for repo_name in user.forked_repositories:
                if repo_name in graph.repos:
                    G.add_edge(user.login, repo_name, relationship='contributor_of')
        
        # Add org relationships
        for org in graph.orgs.values():
            # Org members
            for member in org.members:
                if member in graph.users:
                    G.add_edge(member, org.login, relationship='member_of')
            # Org authored repos
            for repo_name in org.authored_repositories:
                if repo_name in graph.repos:
                    G.add_edge(org.login, repo_name, relationship='owner_of')
            # Org forked repos
            for repo_name in org.forked_repositories:
                if repo_name in graph.repos:
                    G.add_edge(org.login, repo_name, relationship='contributor_of')
        
        # Repos -> contributors and forks
        for repo in graph.repos.values():
            for contributor in repo.contributors:
                if contributor in graph.users or contributor in graph.orgs:
                    if contributor == repo.owner:
                        G.add_edge(contributor, repo.full_name, relationship='owner_of')
                    else:
                        G.add_edge(contributor, repo.full_name, relationship='contributor_of')
            
            if repo.is_fork and repo.forked_from and repo.forked_from in graph.repos:
                G.add_edge(repo.forked_from, repo.full_name, relationship='parent_of')
        
        if len(G.nodes()) == 0:
            logger.warning("No nodes to visualize")
            return
        
        # Get disconnected components
        components = list(nx.weakly_connected_components(G))
        logger.info(f"Creating separate visualizations for {len(components)} cluster(s)")
        
        # Create output directory if it doesn't exist
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Modern technical diagram color palette
        color_map = {
            'user': '#00d9ff',
            'org': '#ffcc00',
            'repo': '#00ff88',
        }
        
        # Edge color mapping by relationship type
        edge_color_map = {
            'owner_of': '#ff6b6b',
            'contributor_of': '#4ecdc4',
            'member_of': '#95e1d3',
            'parent_of': '#ffd93d',
        }
        
        # Visualize each component separately
        for idx, component in enumerate(sorted(components, key=len, reverse=True), 1):
            subgraph = G.subgraph(component)
            
            # Create figure with dark background
            fig, ax = plt.subplots(figsize=figsize, dpi=dpi, facecolor='#2b2b2b')
            ax.set_facecolor('#2b2b2b')
            
            # Layout for non-circular organic distribution
            n_nodes = len(component)
            
            logger.debug(f"Cluster {idx}: Using spectral layout for non-circular distribution ({n_nodes} nodes)")
            
            # Use spectral layout to avoid circular patterns
            try:
                pos = nx.spectral_layout(subgraph, scale=4.0)
                # Add post-processing spring adjustment for extra repulsion
                optimal_k = 2.0 / math.sqrt(n_nodes) if n_nodes > 1 else 2.0
                pos = nx.spring_layout(
                    subgraph,
                    pos=pos,
                    k=optimal_k,
                    iterations=50,  # Light adjustment for spacing
                    seed=None
                )
                logger.debug(f"Cluster {idx}: Using spectral+spring layout ({n_nodes} nodes)")
            except:
                # Fallback: random + spring with higher k
                logger.debug(f"Cluster {idx}: Spectral failed, using random + spring")
                pos = nx.random_layout(subgraph)
                optimal_k = 2.0 / math.sqrt(n_nodes) if n_nodes > 1 else 2.0  # Increased from 1.5
                pos = nx.spring_layout(
                    subgraph,
                    pos=pos,
                    k=optimal_k,
                    iterations=100,  # Limited iterations
                    seed=None
                )
            
            # Separate seed and regular nodes
            component_seed_nodes = [n for n in component if subgraph.nodes[n].get('is_seed', False)]
            component_regular_nodes = [n for n in component if not subgraph.nodes[n].get('is_seed', False)]
            
            # Draw regular nodes
            if component_regular_nodes:
                regular_colors = [color_map.get(subgraph.nodes[n].get('node_type', 'user'), '#ffffff') 
                                for n in component_regular_nodes]
                nx.draw_networkx_nodes(
                    subgraph, pos,
                    nodelist=component_regular_nodes,
                    node_color=regular_colors,
                    node_size=180,  # Reduced from 250 to prevent overlap
                    node_shape='o',
                    alpha=0.85,
                    edgecolors='#ffffff',
                    linewidths=1.5,
                    ax=ax
                )
            
            # Draw seed nodes
            if component_seed_nodes:
                seed_colors = [color_map.get(subgraph.nodes[n].get('node_type', 'user'), '#ffffff') 
                              for n in component_seed_nodes]
                nx.draw_networkx_nodes(
                    subgraph, pos,
                    nodelist=component_seed_nodes,
                    node_color=seed_colors,
                    node_size=320,  # Reduced from 450 to prevent overlap
                    node_shape='s',
                    alpha=0.95,
                    edgecolors='#ffffff',
                    linewidths=2.5,
                    ax=ax
                )
            
            # Draw edges with color coding by relationship type
            edge_lists_by_type = {}
            for u, v, data in subgraph.edges(data=True):
                rel_type = data.get('relationship', 'unknown')
                if rel_type not in edge_lists_by_type:
                    edge_lists_by_type[rel_type] = []
                edge_lists_by_type[rel_type].append((u, v))
            
            # Draw each edge type with its own color (straight lines)
            for rel_type, edges in edge_lists_by_type.items():
                edge_color = edge_color_map.get(rel_type, '#ffffff')
                nx.draw_networkx_edges(
                    subgraph, pos,
                    edgelist=edges,
                    edge_color=edge_color,
                    alpha=0.5,
                    arrows=True,
                    arrowsize=15,
                    arrowstyle='->',
                    width=2.0,
                    ax=ax
                )
            
            # Draw labels with smart positioning
            if len(component) <= 30:
                labels_to_show = {n: subgraph.nodes[n].get('label', n)[:25] for n in component}
            else:
                labels_to_show = {n: subgraph.nodes[n].get('label', n)[:20] for n in component_seed_nodes}
            
            if labels_to_show:
                if HAS_ADJUST_TEXT and len(labels_to_show) <= 30:
                    # Use adjustText for smaller clusters
                    texts = []
                    for node, label in labels_to_show.items():
                        x, y = pos[node]
                        text = ax.text(
                            x, y, label,
                            fontsize=8,
                            fontweight='bold',
                            color='#ffffff',
                            ha='center',
                            va='center',
                            bbox=dict(boxstyle='round,pad=0.3', facecolor='#3a3a3a', edgecolor='#ffffff', linewidth=0.5, alpha=0.8)
                        )
                        texts.append(text)
                    
                    # Adjust text positions
                    adjust_text(
                        texts,
                        arrowprops=dict(arrowstyle='->', color='#ffffff', lw=0.8, alpha=0.6),
                        expand_points=(1.5, 1.5),
                        force_text=(0.5, 0.5),
                        force_points=(0.2, 0.2),
                        ax=ax
                    )
                else:
                    # Fallback for large clusters or when adjustText not available
                    nx.draw_networkx_labels(
                        subgraph, pos,
                        labels=labels_to_show,
                        font_size=8,
                        font_weight='bold',
                        font_color='#ffffff',
                        ax=ax
                    )
            
            # Count node types in this cluster
            users_count = sum(1 for n in component if subgraph.nodes[n].get('node_type') == 'user')
            orgs_count = sum(1 for n in component if subgraph.nodes[n].get('node_type') == 'org')
            repos_count = sum(1 for n in component if subgraph.nodes[n].get('node_type') == 'repo')
            
            # Create legend with node types and edge types
            legend_elements = [
                mpatches.Patch(facecolor=color_map['user'], label=f'User ({users_count})', edgecolor='#ffffff', linewidth=1),
                mpatches.Patch(facecolor=color_map['org'], label=f'Organization ({orgs_count})', edgecolor='#ffffff', linewidth=1),
                mpatches.Patch(facecolor=color_map['repo'], label=f'Repository ({repos_count})', edgecolor='#ffffff', linewidth=1),
            ]
            if component_seed_nodes:
                legend_elements.append(
                    mpatches.Patch(facecolor='#666666', edgecolor='#ffffff', linewidth=2, label='Seed Node')
                )
            
            # Add edge type legend
            legend_elements.append(mpatches.Patch(facecolor='none', edgecolor='none', label=''))  # Spacer
            for rel_type in edge_lists_by_type.keys():
                if rel_type in edge_color_map:
                    legend_elements.append(
                        mpatches.Patch(facecolor=edge_color_map[rel_type], label=rel_type.title(), edgecolor='#ffffff', linewidth=1)
                    )
            
            ax.legend(
                handles=legend_elements,
                loc='upper left',
                fontsize=10,
                framealpha=0.9,
                facecolor='#3a3a3a',
                edgecolor='#ffffff',
                labelcolor='#ffffff'
            )
            
            # Title
            ax.set_title(
                f'Cluster {idx} of {len(components)}\n'
                f'{len(component)} nodes • {len(subgraph.edges())} edges',
                fontsize=16,
                fontweight='bold',
                color='#ffffff',
                pad=20
            )
            ax.axis('off')
            
            # Save
            cluster_path = output_dir / f'cluster_{idx:02d}.png'
            plt.tight_layout()
            plt.savefig(cluster_path, dpi=dpi, bbox_inches='tight', facecolor='#2b2b2b')
            plt.close()
            
            logger.info(f"Cluster {idx} visualization saved to {cluster_path} ({len(component)} nodes)")
    
    except Exception as e:
        logger.error(f"Failed to create cluster visualizations: {e}")
        raise
