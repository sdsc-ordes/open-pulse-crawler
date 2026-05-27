"""Input/Output handlers for the crawler."""

import csv
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

from .models import GraphData
from .node_id import repo_url, team_url, user_url

logger = logging.getLogger(__name__)


def parse_seed_file(file_path: Path) -> List[str]:
    """Parse a seed file (one seed per line, ``#`` lines and blanks ignored).

    Returns the raw seed strings — normalization to canonical URLs happens
    later in ``GitHubCrawler._parse_seed`` so we accept the same mix of
    login / ``owner/repo`` / URL forms here that the CLI accepts.
    """
    seeds: List[str] = []
    try:
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    seeds.append(line)
        logger.info(f"Loaded {len(seeds)} seeds from {file_path}")
    except Exception as e:
        logger.error(f"Failed to read seed file {file_path}: {e}")
        raise

    return seeds


def export_to_json(graph: GraphData, output_path: Path):
    """Export graph data to JSON format.

    The output dict is keyed by canonical URL (e.g.
    ``users["https://github.com/torvalds"] = {...}``). See models for
    the schema_version field that travels with the graph.
    """
    try:
        data = graph.model_dump()
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Exported graph to JSON: {output_path}")
    except Exception as e:
        logger.error(f"Failed to export to JSON: {e}")
        raise


def _user_url_lookup(graph: GraphData, login: str) -> Optional[str]:
    """Return the canonical URL of a user/org with this login if in the graph.

    Edge list fields store bare logins (see models.UserModel); we translate
    them to URLs at export time and only emit edges for endpoints that are
    actually in the graph. The lookup checks users first, then orgs, since
    the crawler's user-or-org disambiguation may leave a login in either
    bucket depending on which API responded.
    """
    candidate = user_url(login)
    if candidate in graph.users:
        return candidate
    if candidate in graph.orgs:
        return candidate
    return None


def _repo_url_lookup(graph: GraphData, full_name: str) -> Optional[str]:
    """Return the canonical URL of a repo with this full_name if in the graph."""
    candidate = repo_url(full_name)
    return candidate if candidate in graph.repos else None


def export_to_csv(graph: GraphData, output_path: Path, seed_nodes: Set[str]):
    """Export graph edges to CSV (source/target are canonical URLs).

    CSV columns: ``source, target, property, source_type, target_type``.
    Both endpoints of every emitted edge are guaranteed to exist in the
    graph — edges to never-explored nodes are silently dropped.

    ``seed_nodes`` carries URLs (the crawler stores URLs internally).
    """
    edges: List[Dict[str, str]] = []

    # ── Users ───────────────────────────────────────────────────────────
    for user in graph.users.values():
        src = user.url

        # User -> authored repositories (owner_of)
        for repo_name in user.authored_repositories:
            t = _repo_url_lookup(graph, repo_name)
            if t is None:
                # Authored repos can be emitted even if the repo node is
                # only discovered (not in graph). Build the URL anyway so
                # the edge endpoint is consistent.
                t = repo_url(repo_name)
            edges.append({
                'source': src,
                'target': t,
                'property': 'owner_of',
                'source_type': 'user',
                'target_type': 'repo',
            })

        # User -> forked repositories (contributor_of)
        for repo_name in user.forked_repositories:
            t = _repo_url_lookup(graph, repo_name) or repo_url(repo_name)
            edges.append({
                'source': src,
                'target': t,
                'property': 'contributor_of',
                'source_type': 'user',
                'target_type': 'repo',
            })

        # Follow relationships — only between users both in the graph.
        for followed_login in user.following:
            t = _user_url_lookup(graph, followed_login)
            if t is not None:
                edges.append({
                    'source': src,
                    'target': t,
                    'property': 'follows',
                    'source_type': 'user',
                    'target_type': 'user',
                })
        for follower_login in user.followers:
            s = _user_url_lookup(graph, follower_login)
            if s is not None:
                edges.append({
                    'source': s,
                    'target': src,
                    'property': 'follows',
                    'source_type': 'user',
                    'target_type': 'user',
                })

        # Starred / watched — only when the repo is in the graph.
        for repo_name in user.starred_repositories:
            t = _repo_url_lookup(graph, repo_name)
            if t is not None:
                edges.append({
                    'source': src,
                    'target': t,
                    'property': 'starred',
                    'source_type': 'user',
                    'target_type': 'repo',
                })
        for repo_name in user.watched_repositories:
            t = _repo_url_lookup(graph, repo_name)
            if t is not None:
                edges.append({
                    'source': src,
                    'target': t,
                    'property': 'watching',
                    'source_type': 'user',
                    'target_type': 'repo',
                })

    # ── Organizations ───────────────────────────────────────────────────
    for org in graph.orgs.values():
        src = org.url

        for repo_name in org.authored_repositories:
            t = _repo_url_lookup(graph, repo_name) or repo_url(repo_name)
            edges.append({
                'source': src,
                'target': t,
                'property': 'owner_of',
                'source_type': 'org',
                'target_type': 'repo',
            })

        for repo_name in org.forked_repositories:
            t = _repo_url_lookup(graph, repo_name) or repo_url(repo_name)
            edges.append({
                'source': src,
                'target': t,
                'property': 'contributor_of',
                'source_type': 'org',
                'target_type': 'repo',
            })

    # ── Repositories ────────────────────────────────────────────────────
    for repo in graph.repos.values():
        tgt = repo.url

        # Contributors -> repo (or owner_of when contributor is the owner).
        for contributor_login in repo.contributors:
            s = _user_url_lookup(graph, contributor_login)
            if s is None:
                continue  # Contributor never made it into the graph.

            relationship = (
                'owner_of'
                if contributor_login == repo.owner
                else 'contributor_of'
            )
            contributor_type = 'org' if s in graph.orgs else 'user'

            edges.append({
                'source': s,
                'target': tgt,
                'property': relationship,
                'source_type': contributor_type,
                'target_type': 'repo',
            })

        # Fork parentage — only when the parent is in the graph.
        if repo.is_fork and repo.forked_from:
            s = _repo_url_lookup(graph, repo.forked_from)
            if s is not None:
                edges.append({
                    'source': s,
                    'target': tgt,
                    'property': 'parent_of',
                    'source_type': 'repo',
                    'target_type': 'repo',
                })

        # Dependencies / dependents — emit unconditionally; the dependency
        # graph itself defines the relationship even if the other repo
        # isn't yet a node.
        for dep_name in repo.dependencies:
            t = _repo_url_lookup(graph, dep_name) or repo_url(dep_name)
            edges.append({
                'source': tgt,
                'target': t,
                'property': 'depends_on',
                'source_type': 'repo',
                'target_type': 'repo',
            })
        for dep_name in repo.dependents:
            s = _repo_url_lookup(graph, dep_name) or repo_url(dep_name)
            edges.append({
                'source': s,
                'target': tgt,
                'property': 'depends_on',
                'source_type': 'repo',
                'target_type': 'repo',
            })

        # Issue / PR activity — only when the user is in the graph.
        _activity_pairs = (
            ('issue_author', repo.issue_authors),
            ('pr_author', repo.pr_authors),
            ('commented_on', repo.commenters),
            ('pr_reviewer', repo.pr_reviewers),
        )
        for property_name, login_list in _activity_pairs:
            for user_login in login_list:
                s = _user_url_lookup(graph, user_login)
                if s is None:
                    continue
                edges.append({
                    'source': s,
                    'target': tgt,
                    'property': property_name,
                    'source_type': 'user',
                    'target_type': 'repo',
                })

    # ── Teams ───────────────────────────────────────────────────────────
    for team in graph.teams.values():
        team_node = team.url

        # Team is contained by an org — both must be in the graph.
        if team.org:
            org_node = _user_url_lookup(graph, team.org)
            if org_node is not None:
                edges.append({
                    'source': org_node,
                    'target': team_node,
                    'property': 'has_team',
                    'source_type': 'org',
                    'target_type': 'team',
                })

        # Team access to repositories.
        for repo_name in team.repositories:
            t = _repo_url_lookup(graph, repo_name)
            if t is not None:
                edges.append({
                    'source': team_node,
                    'target': t,
                    'property': 'has_access',
                    'source_type': 'team',
                    'target_type': 'repo',
                })

        # Nested teams: parent -> child. `team.parent` holds ``org/slug``.
        if team.parent:
            try:
                org_slug = team.parent.split('/', 1)
                parent_url = team_url(org_slug[0], org_slug[1])
            except (ValueError, IndexError):
                parent_url = None
            if parent_url is not None and parent_url in graph.teams:
                edges.append({
                    'source': parent_url,
                    'target': team_node,
                    'property': 'parent_of',
                    'source_type': 'team',
                    'target_type': 'team',
                })

    # Deduplicate edges to prevent double-counting.
    unique_edges = {tuple(sorted(d.items())) for d in edges}
    edges = [dict(t) for t in unique_edges]

    # Write to CSV.
    try:
        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(
                f,
                fieldnames=['source', 'target', 'property', 'source_type', 'target_type'],
            )
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
    """Export node data to CSV (the ``id`` column is the canonical URL).

    CSV columns: ``id, name, type, is_seed, is_explored, exploration_timestamp``.

    ``seed_nodes`` and ``discovered_nodes`` keys are URLs (the crawler
    tracks both as URLs internally).
    """
    nodes: List[Dict] = []
    processed_ids: Set[str] = set()

    for user in graph.users.values():
        nodes.append({
            'id': user.url,
            'name': user.name or user.login,
            'type': 'user',
            'is_seed': user.url in seed_nodes,
            'is_explored': user.is_explored,
            'exploration_timestamp': user.exploration_timestamp,
        })
        processed_ids.add(user.url)

    for org in graph.orgs.values():
        nodes.append({
            'id': org.url,
            'name': org.name or org.login,
            'type': 'org',
            'is_seed': org.url in seed_nodes,
            'is_explored': org.is_explored,
            'exploration_timestamp': org.exploration_timestamp,
        })
        processed_ids.add(org.url)

    for repo in graph.repos.values():
        nodes.append({
            'id': repo.url,
            'name': repo.name or repo.full_name,
            'type': 'repo',
            'is_seed': repo.url in seed_nodes,
            'is_explored': repo.is_explored,
            'exploration_timestamp': repo.exploration_timestamp,
        })
        processed_ids.add(repo.url)

    for team in graph.teams.values():
        nodes.append({
            'id': team.url,
            'name': team.name or team.full_name,
            'type': 'team',
            'is_seed': team.url in seed_nodes,
            'is_explored': team.is_explored,
            'exploration_timestamp': team.exploration_timestamp,
        })
        processed_ids.add(team.url)

    # Discovered but unexplored nodes (their ID is already a URL).
    if discovered_nodes:
        for node_id, (node_type, _, _) in discovered_nodes.items():
            if node_id not in processed_ids:
                nodes.append({
                    'id': node_id,
                    'name': node_id,
                    'type': node_type,
                    'is_seed': node_id in seed_nodes,
                    'is_explored': False,
                    'exploration_timestamp': None,
                })
                processed_ids.add(node_id)

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
