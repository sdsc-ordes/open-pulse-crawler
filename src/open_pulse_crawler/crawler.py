"""Core crawler logic implementing BFS strategy."""

import logging
from typing import List, Set, Dict, Tuple, Optional
from pathlib import Path
import json
from collections import deque

from .models import (
    GraphData, UserModel, OrgModel, RepoModel,
    GitHubItemType
)
from .github_client import GitHubClient

logger = logging.getLogger(__name__)


class GitHubCrawler:
    """BFS crawler for GitHub entities."""
    
    def __init__(
        self,
        client: GitHubClient,
        max_rounds: int = 3,
        state_file: Optional[Path] = None
    ):
        """
        Initialize the crawler.
        
        Args:
            client: GitHub API client
            max_rounds: Maximum number of BFS rounds
            state_file: File to save/load crawler state
        """
        self.client = client
        self.max_rounds = max_rounds
        self.state_file = state_file
        
        # Graph data
        self.graph = GraphData()
        
        # BFS tracking
        self.current_round = 0
        self.visited: Set[str] = set()
        self.seed_nodes: Set[str] = set()
        self.queue: deque = deque()
        
        # Statistics per round
        self.round_stats: List[Dict] = []
    
    def save_state(self):
        """Save crawler state to file."""
        if not self.state_file:
            return
        
        state = {
            'current_round': self.current_round,
            'visited': list(self.visited),
            'seed_nodes': list(self.seed_nodes),
            'queue': list(self.queue),
            'graph': self.graph.model_dump(),
            'round_stats': self.round_stats,
        }
        
        try:
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
            logger.info(f"State saved to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def load_state(self) -> bool:
        """
        Load crawler state from file.
        
        Returns:
            True if state was loaded successfully
        """
        if not self.state_file or not self.state_file.exists():
            return False
        
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            self.current_round = state.get('current_round', 0)
            self.visited = set(state.get('visited', []))
            self.seed_nodes = set(state.get('seed_nodes', []))
            self.queue = deque(state.get('queue', []))
            self.graph = GraphData(**state.get('graph', {}))
            self.round_stats = state.get('round_stats', [])
            
            logger.info(f"State loaded from {self.state_file}")
            logger.info(f"Resuming from round {self.current_round}")
            return True
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return False
    
    def _parse_seed(self, seed: str) -> Tuple[str, str]:
        """
        Parse seed input to determine type and identifier.
        
        Args:
            seed: Can be username, org/repo, or full GitHub URL
        
        Returns:
            Tuple of (type, identifier) where type is 'user', 'org', or 'repo'
        """
        seed = seed.strip()
        
        # Remove https://github.com/ prefix if present
        if seed.startswith('https://github.com/'):
            seed = seed[19:].rstrip('/')
        elif seed.startswith('http://github.com/'):
            seed = seed[18:].rstrip('/')
        
        # Check if it's a repo (contains /)
        if '/' in seed:
            return ('repo', seed)
        else:
            # Could be user or org, we'll check later
            return ('user_or_org', seed)
    
    def add_seeds(self, seeds: List[str]):
        """Add initial seed nodes to the queue."""
        for seed in seeds:
            seed_type, identifier = self._parse_seed(seed)
            
            if identifier not in self.visited:
                self.queue.append((seed_type, identifier, 0))  # (type, id, round)
                self.seed_nodes.add(identifier)
        
        logger.info(f"Added {len(seeds)} seed nodes")
    
    def _process_user(self, username: str) -> Optional[UserModel]:
        """Process a user and return UserModel."""
        try:
            user_obj = self.client.get_user(username)
            if not user_obj:
                return None
            
            # Handle cached data (dict) vs live API object
            if isinstance(user_obj, dict):
                user = UserModel(
                    login=user_obj['login'],
                    name=user_obj.get('name', ''),
                    id=user_obj.get('id', 0),
                    type=GitHubItemType.USER
                )
            else:
                # Check if this is actually an organization
                if user_obj.type == 'Organization':
                    logger.debug(f"{username} is an organization, not a user")
                    return None
                
                user = UserModel(
                    login=user_obj.login,
                    name=user_obj.name or '',
                    id=user_obj.id,
                    type=GitHubItemType.USER if user_obj.type == 'User' else GitHubItemType.BOT
                )
                
                # Get user's repositories
                try:
                    repos = self.client._make_request(user_obj.get_repos)
                    for repo in repos:
                        if repo.fork:
                            user.forked_repositories.append(repo.full_name)
                        else:
                            user.authored_repositories.append(repo.full_name)
                        
                        # Add repos to queue for next round
                        if repo.full_name not in self.visited:
                            self.queue.append(('repo', repo.full_name, self.current_round + 1))
                except Exception as e:
                    logger.warning(f"Failed to get repos for user {username}: {e}")
            
            return user
        except Exception as e:
            logger.error(f"Error processing user {username}: {e}")
            return None
    
    def _process_organization(self, org_name: str) -> Optional[OrgModel]:
        """Process an organization and return OrgModel."""
        try:
            org_obj = self.client.get_organization(org_name)
            if not org_obj:
                return None
            
            if isinstance(org_obj, dict):
                org = OrgModel(
                    login=org_obj['login'],
                    name=org_obj.get('name', ''),
                    id=org_obj.get('id', 0),
                    type=GitHubItemType.ORGANIZATION
                )
            else:
                org = OrgModel(
                    login=org_obj.login,
                    name=org_obj.name or '',
                    id=org_obj.id,
                    type=GitHubItemType.ORGANIZATION
                )
                
                # Get organization members
                try:
                    members = self.client._make_request(org_obj.get_members)
                    for member in members:
                        org.members.append(member.login)
                        
                        # Add members to queue
                        if member.login not in self.visited:
                            self.queue.append(('user', member.login, self.current_round + 1))
                except Exception as e:
                    logger.warning(f"Failed to get members for org {org_name}: {e}")
                
                # Get organization repositories
                try:
                    repos = self.client._make_request(org_obj.get_repos)
                    for repo in repos:
                        if repo.fork:
                            org.forked_repositories.append(repo.full_name)
                        else:
                            org.authored_repositories.append(repo.full_name)
                        
                        # Add repos to queue
                        if repo.full_name not in self.visited:
                            self.queue.append(('repo', repo.full_name, self.current_round + 1))
                except Exception as e:
                    logger.warning(f"Failed to get repos for org {org_name}: {e}")
            
            return org
        except Exception as e:
            logger.error(f"Error processing organization {org_name}: {e}")
            return None
    
    def _process_repository(self, repo_full_name: str) -> Optional[RepoModel]:
        """Process a repository and return RepoModel."""
        try:
            repo_obj = self.client.get_repository(repo_full_name)
            if not repo_obj:
                return None
            
            if isinstance(repo_obj, dict):
                repo = RepoModel(
                    full_name=repo_obj['full_name'],
                    name=repo_obj.get('name', ''),
                    id=repo_obj.get('id', 0),
                    type=GitHubItemType.REPOSITORY,
                    owner=repo_obj.get('owner', ''),
                    is_fork=repo_obj.get('is_fork', False),
                    forked_from=repo_obj.get('parent')
                )
            else:
                repo = RepoModel(
                    full_name=repo_obj.full_name,
                    name=repo_obj.name,
                    id=repo_obj.id,
                    type=GitHubItemType.REPOSITORY,
                    owner=repo_obj.owner.login,
                    is_fork=repo_obj.fork,
                    forked_from=repo_obj.parent.full_name if repo_obj.parent else None
                )
                
                # Add owner to queue
                if repo_obj.owner.login not in self.visited:
                    owner_type = 'org' if repo_obj.owner.type == 'Organization' else 'user'
                    self.queue.append((owner_type, repo_obj.owner.login, self.current_round + 1))
                
                # Get contributors (limited to avoid too many API calls)
                try:
                    contributors = self.client._make_request(repo_obj.get_contributors)
                    for i, contributor in enumerate(contributors):
                        if i >= 10:  # Limit to top 10 contributors
                            break
                        repo.contributors.append(contributor.login)
                        
                        # Add contributors to queue
                        if contributor.login not in self.visited:
                            self.queue.append(('user', contributor.login, self.current_round + 1))
                except Exception as e:
                    logger.warning(f"Failed to get contributors for repo {repo_full_name}: {e}")
                
                # If it's a fork, add parent to queue
                if repo.is_fork and repo.forked_from and repo.forked_from not in self.visited:
                    self.queue.append(('repo', repo.forked_from, self.current_round + 1))
            
            return repo
        except Exception as e:
            logger.error(f"Error processing repository {repo_full_name}: {e}")
            return None
    
    def crawl(self):
        """Execute the BFS crawl."""
        logger.info(f"Starting crawl for {self.max_rounds} rounds")
        
        while self.queue and self.current_round < self.max_rounds:
            # Start new round
            round_start_time = __import__('time').time()
            nodes_in_round = []
            
            # Process all nodes in current round
            while self.queue:
                node_type, identifier, node_round = self.queue[0]
                
                if node_round > self.current_round:
                    # Reached next round
                    break
                
                self.queue.popleft()
                
                # Skip if already visited
                if identifier in self.visited:
                    continue
                
                self.visited.add(identifier)
                nodes_in_round.append((node_type, identifier))
                
                # Process node based on type
                if node_type == 'user_or_org':
                    # Try as user first
                    user = self._process_user(identifier)
                    if user:
                        self.graph.add_user(user)
                    else:
                        # Try as organization
                        org = self._process_organization(identifier)
                        if org:
                            self.graph.add_org(org)
                
                elif node_type == 'user':
                    user = self._process_user(identifier)
                    if user:
                        self.graph.add_user(user)
                
                elif node_type == 'org':
                    org = self._process_organization(identifier)
                    if org:
                        self.graph.add_org(org)
                
                elif node_type == 'repo':
                    repo = self._process_repository(identifier)
                    if repo:
                        self.graph.add_repo(repo)
            
            # Round statistics - count actual entities found, not queued types
            round_time = __import__('time').time() - round_start_time
            
            # Count entities actually added in this round
            current_users = len(self.graph.users)
            current_orgs = len(self.graph.orgs)
            current_repos = len(self.graph.repos)
            
            # Calculate what was found in this round
            users_this_round = current_users - sum(rs.get('users_found', 0) for rs in self.round_stats)
            orgs_this_round = current_orgs - sum(rs.get('orgs_found', 0) for rs in self.round_stats)
            repos_this_round = current_repos - sum(rs.get('repos_found', 0) for rs in self.round_stats)
            
            round_stat = {
                'round': self.current_round,
                'nodes_processed': len(nodes_in_round),
                'users_found': users_this_round,
                'orgs_found': orgs_this_round,
                'repos_found': repos_this_round,
                'time_seconds': round_time,
                'queue_size': len(self.queue),
            }
            self.round_stats.append(round_stat)
            
            logger.info(f"Round {self.current_round} completed: "
                       f"{round_stat['nodes_processed']} nodes processed in {round_time:.1f}s, "
                       f"{round_stat['queue_size']} nodes in queue")
            
            self.current_round += 1
            
            # Save state after each round
            if self.state_file:
                self.save_state()
        
        logger.info(f"Crawl completed after {self.current_round} rounds")
        logger.info(f"Total nodes: {len(self.visited)}")
        logger.info(f"Users: {len(self.graph.users)}, Orgs: {len(self.graph.orgs)}, "
                   f"Repos: {len(self.graph.repos)}")
    
    def get_statistics(self) -> Dict:
        """Get crawler statistics."""
        return {
            'rounds_completed': self.current_round,
            'total_nodes': len(self.visited),
            'users': len(self.graph.users),
            'organizations': len(self.graph.orgs),
            'repositories': len(self.graph.repos),
            'round_stats': self.round_stats,
            'api_stats': self.client.get_stats(),
        }
