"""Input/Output handlers for the crawler."""

import csv
import json
import logging
from pathlib import Path
from typing import List, Set, Dict, Optional, Tuple

from .models import GraphData, GitHubItemType

logger = logging.getLogger(__name__)


def parse_seed_file(file_path: Path) -> List[str]:
    """
    Parse seed file containing initial nodes.
    
    Args:
        file_path: Path to seed file (one seed per line)
    
    Returns:
        List of seed identifiers
    """
    seeds = []
    try:
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):  # Skip empty lines and comments
                    seeds.append(line)
        logger.info(f"Loaded {len(seeds)} seeds from {file_path}")
    except Exception as e:
        logger.error(f"Failed to read seed file {file_path}: {e}")
        raise
    
    return seeds


def export_to_json(graph: GraphData, output_path: Path):
    """
    Export graph data to JSON format.
    
    Args:
        graph: GraphData to export
        output_path: Path to output JSON file
    """
    try:
        data = graph.model_dump()
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Exported graph to JSON: {output_path}")
    except Exception as e:
        logger.error(f"Failed to export to JSON: {e}")
        raise


def export_to_csv(graph: GraphData, output_path: Path, seed_nodes: Set[str]):
    """
    Export graph data to CSV format with edges.
    
    CSV format: source,target,property,source_type,target_type
    
    Args:
        graph: GraphData to export
        output_path: Path to output CSV file
        seed_nodes: Set of initial seed node identifiers
    """
    edges = []
    
    # Process users
    for user in graph.users.values():
        # User -> authored repositories (owner_of)
        for repo_name in user.authored_repositories:
            edges.append({
                'source': user.login,
                'target': repo_name,
                'property': 'owner_of',
                'source_type': 'user',
                'target_type': 'repo',
            })

        # User -> forked repositories (fork_of - reverse direction)
        for repo_name in user.forked_repositories:
            # The user created a fork, so user -> repo "contributor_of"
            # and we'll add repo -> parent repo later
            edges.append({
                'source': user.login,
                'target': repo_name,
                'property': 'contributor_of',
                'source_type': 'user',
                'target_type': 'repo',
            })

        # Follow relationships — only emit edges between users both in the graph.
        for followed_login in user.following:
            if followed_login in graph.users:
                edges.append({
                    'source': user.login,
                    'target': followed_login,
                    'property': 'follows',
                    'source_type': 'user',
                    'target_type': 'user',
                })
        for follower_login in user.followers:
            if follower_login in graph.users:
                edges.append({
                    'source': follower_login,
                    'target': user.login,
                    'property': 'follows',
                    'source_type': 'user',
                    'target_type': 'user',
                })

        # Starred and watched repositories — only emit when the repo is in the graph.
        for repo_name in user.starred_repositories:
            if repo_name in graph.repos:
                edges.append({
                    'source': user.login,
                    'target': repo_name,
                    'property': 'starred',
                    'source_type': 'user',
                    'target_type': 'repo',
                })
        for repo_name in user.watched_repositories:
            if repo_name in graph.repos:
                edges.append({
                    'source': user.login,
                    'target': repo_name,
                    'property': 'watching',
                    'source_type': 'user',
                    'target_type': 'repo',
                })
    
    # Process organizations
    for org in graph.orgs.values():
        # Org -> authored repositories (owner_of)
        for repo_name in org.authored_repositories:
            edges.append({
                'source': org.login,
                'target': repo_name,
                'property': 'owner_of',
                'source_type': 'org',
                'target_type': 'repo',
            })
        
        # Org -> forked repositories (contributor_of)
        for repo_name in org.forked_repositories:
            edges.append({
                'source': org.login,
                'target': repo_name,
                'property': 'contributor_of',
                'source_type': 'org',
                'target_type': 'repo',
            })
    
    # Process repositories
    for repo in graph.repos.values():
        # Contributors -> repo
        for contributor_login in repo.contributors:
            # Only include edge if contributor is also in the graph (explored)
            if contributor_login not in graph.users and contributor_login not in graph.orgs:
                continue
            
            # Check if contributor is the owner
            if contributor_login == repo.owner:
                relationship = 'owner_of'
            else:
                relationship = 'contributor_of'
            
            # Determine contributor type
            contributor_type = 'user'
            if contributor_login in graph.orgs:
                contributor_type = 'org'
            
            edges.append({
                'source': contributor_login,
                'target': repo.full_name,
                'property': relationship,
                'source_type': contributor_type,
                'target_type': 'repo',
            })
        
        # Fork relationships (parent repo -> forked repo)
        # Only include if parent repo is also in the graph (explored)
        if repo.is_fork and repo.forked_from and repo.forked_from in graph.repos:
            edges.append({
                'source': repo.forked_from,
                'target': repo.full_name,
                'property': 'parent_of',
                'source_type': 'repo',
                'target_type': 'repo',
            })

        # Dependencies (repo -> dependency)
        for dep_name in repo.dependencies:
            edges.append({
                'source': repo.full_name,
                'target': dep_name,
                'property': 'depends_on',
                'source_type': 'repo',
                'target_type': 'repo',
            })

        # Dependents (dependent -> repo)
        for dep_name in repo.dependents:
            edges.append({
                'source': dep_name,
                'target': repo.full_name,
                'property': 'depends_on',
                'source_type': 'repo',
                'target_type': 'repo',
            })

        # Issue / PR activity — only emit when the user is in the graph.
        _activity_pairs = (
            ('issue_author', repo.issue_authors),
            ('pr_author', repo.pr_authors),
            ('commented_on', repo.commenters),
            ('pr_reviewer', repo.pr_reviewers),
        )
        for property_name, login_list in _activity_pairs:
            for user_login in login_list:
                if user_login in graph.users:
                    edges.append({
                        'source': user_login,
                        'target': repo.full_name,
                        'property': property_name,
                        'source_type': 'user',
                        'target_type': 'repo',
                    })

    # Process teams
    for team in graph.teams.values():
        # Team is contained by an org.
        if team.org and team.org in graph.orgs:
            edges.append({
                'source': team.org,
                'target': team.full_name,
                'property': 'has_team',
                'source_type': 'org',
                'target_type': 'team',
            })

        # Team access to repositories.
        for repo_name in team.repositories:
            if repo_name in graph.repos:
                edges.append({
                    'source': team.full_name,
                    'target': repo_name,
                    'property': 'has_access',
                    'source_type': 'team',
                    'target_type': 'repo',
                })

        # Nested teams: parent -> child.
        if team.parent and team.parent in graph.teams:
            edges.append({
                'source': team.parent,
                'target': team.full_name,
                'property': 'parent_of',
                'source_type': 'team',
                'target_type': 'team',
            })

    # Deduplicate edges to prevent double-counting (e.g. if A depends on B, and both are in graph)
    # Convert list of dicts to set of frozen items, then back to list of dicts
    unique_edges = {tuple(sorted(d.items())) for d in edges}
    edges = [dict(t) for t in unique_edges]

    # Write to CSV
    try:
        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['source', 'target', 'property', 'source_type', 'target_type'])
            writer.writeheader()
            writer.writerows(edges)
        
        logger.info(f"Exported {len(edges)} edges to CSV: {output_path}")
    except Exception as e:
        logger.error(f"Failed to export to CSV: {e}")
        raise


def export_nodes_csv(
    graph: GraphData,
    output_path: Path,
    seed_nodes: Set[str],
    discovered_nodes: Optional[Dict[str, tuple]] = None,
):
    """
    Export node data to CSV format.

    CSV format: id,name,type,is_seed,is_explored,exploration_timestamp

    Args:
        graph: GraphData to export
        output_path: Path to output CSV file
        seed_nodes: Set of initial seed node identifiers
        discovered_nodes: Optional dict of discovered but unexplored nodes
    """
    nodes = []
    processed_ids = set()

    # Add users
    for user in graph.users.values():
        nodes.append({
            'id': user.login,
            'name': user.name or user.login,
            'type': 'user',
            'is_seed': user.login in seed_nodes,
            'is_explored': user.is_explored,
            'exploration_timestamp': user.exploration_timestamp,
        })
        processed_ids.add(user.login)

    # Add orgs
    for org in graph.orgs.values():
        nodes.append({
            'id': org.login,
            'name': org.name or org.login,
            'type': 'org',
            'is_seed': org.login in seed_nodes,
            'is_explored': org.is_explored,
            'exploration_timestamp': org.exploration_timestamp,
        })
        processed_ids.add(org.login)

    # Add repos
    for repo in graph.repos.values():
        nodes.append({
            'id': repo.full_name,
            'name': repo.name or repo.full_name,
            'type': 'repo',
            'is_seed': repo.full_name in seed_nodes,
            'is_explored': repo.is_explored,
            'exploration_timestamp': repo.exploration_timestamp,
        })
        processed_ids.add(repo.full_name)

    # Add teams
    for team in graph.teams.values():
        nodes.append({
            'id': team.full_name,
            'name': team.name or team.full_name,
            'type': 'team',
            'is_seed': team.full_name in seed_nodes,
            'is_explored': team.is_explored,
            'exploration_timestamp': team.exploration_timestamp,
        })
        processed_ids.add(team.full_name)

    # Add discovered but unexplored nodes
    if discovered_nodes:
        for node_id, (node_type, _, _) in discovered_nodes.items():
            if node_id not in processed_ids:
                nodes.append({
                    'id': node_id,
                    'name': node_id,  # We don't have the name for unexplored nodes
                    'type': node_type,
                    'is_seed': node_id in seed_nodes,
                    'is_explored': False,
                    'exploration_timestamp': None,
                })
                processed_ids.add(node_id)

    # Write to CSV
    try:
        with open(output_path, 'w', newline='') as f:
            fieldnames = ['id', 'name', 'type', 'is_seed', 'is_explored', 'exploration_timestamp']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(nodes)
        
        logger.info(f"Exported {len(nodes)} nodes to CSV: {output_path}")
    except Exception as e:
        logger.error(f"Failed to export nodes to CSV: {e}")
        raise
