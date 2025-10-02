"""Core crawler logic implementing BFS strategy."""

import logging
from typing import List, Set, Dict, Tuple, Optional
from pathlib import Path
import json
from collections import deque
from datetime import datetime
from tqdm import tqdm

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
            is_cached = isinstance(user_obj, dict)
            
            if is_cached:
                # Check if this is actually an organization (from cached data)
                if user_obj.get('type') == 'Organization':
                    logger.debug(f"{username} is an organization, not a user")
                    return None
                    
                user = UserModel(
                    login=user_obj['login'],
                    name=user_obj.get('name', ''),
                    id=user_obj.get('id', 0),
                    type=GitHubItemType.USER
                )
                
                # Use cached repos data if available
                cached_repos = user_obj.get('repos', [])
                for repo_data in cached_repos:
                    if repo_data.get('fork'):
                        user.forked_repositories.append(repo_data['full_name'])
                    else:
                        user.authored_repositories.append(repo_data['full_name'])
                    
                    # Add repos to queue for next round
                    if repo_data['full_name'] not in self.visited:
                        self.queue.append(('repo', repo_data['full_name'], self.current_round + 1))
                
                # Use cached organizations data if available
                cached_orgs = user_obj.get('orgs', [])
                for org_login in cached_orgs:
                    # Add organizations to queue
                    if org_login not in self.visited:
                        self.queue.append(('org', org_login, self.current_round + 1))
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
                
                # Get user's repositories from live API
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
                
                # Get user's organization memberships from live API
                try:
                    orgs = self.client._make_request(user_obj.get_orgs)
                    for org in orgs:
                        # Add organizations to queue
                        if org.login not in self.visited:
                            self.queue.append(('org', org.login, self.current_round + 1))
                except Exception as e:
                    logger.warning(f"Failed to get organizations for user {username}: {e}")
            
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
            
            is_cached = isinstance(org_obj, dict)
            
            if is_cached:
                org = OrgModel(
                    login=org_obj['login'],
                    name=org_obj.get('name', ''),
                    id=org_obj.get('id', 0),
                    type=GitHubItemType.ORGANIZATION
                )
                
                # Use cached members data if available
                cached_members = org_obj.get('members', [])
                for member_login in cached_members:
                    org.members.append(member_login)
                    
                    # Add members to queue
                    if member_login not in self.visited:
                        self.queue.append(('user', member_login, self.current_round + 1))
                
                # Use cached repos data if available
                cached_repos = org_obj.get('repos', [])
                for repo_data in cached_repos:
                    if repo_data.get('fork'):
                        org.forked_repositories.append(repo_data['full_name'])
                    else:
                        org.authored_repositories.append(repo_data['full_name'])
                    
                    # Add repos to queue
                    if repo_data['full_name'] not in self.visited:
                        self.queue.append(('repo', repo_data['full_name'], self.current_round + 1))
            else:
                org = OrgModel(
                    login=org_obj.login,
                    name=org_obj.name or '',
                    id=org_obj.id,
                    type=GitHubItemType.ORGANIZATION
                )
                
                # Get organization members from live API
                try:
                    members = self.client._make_request(org_obj.get_members)
                    for member in members:
                        org.members.append(member.login)
                        
                        # Add members to queue
                        if member.login not in self.visited:
                            self.queue.append(('user', member.login, self.current_round + 1))
                except Exception as e:
                    logger.warning(f"Failed to get members for org {org_name}: {e}")
                
                # Get organization repositories from live API
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
            
            is_cached = isinstance(repo_obj, dict)
            
            if is_cached:
                repo = RepoModel(
                    full_name=repo_obj['full_name'],
                    name=repo_obj.get('name', ''),
                    id=repo_obj.get('id', 0),
                    type=GitHubItemType.REPOSITORY,
                    owner=repo_obj.get('owner', ''),
                    is_fork=repo_obj.get('is_fork', False),
                    forked_from=repo_obj.get('parent')
                )
                
                # Add owner to queue from cached data
                owner_login = repo_obj.get('owner', '')
                owner_type_str = repo_obj.get('owner_type', 'User')
                if owner_login and owner_login not in self.visited:
                    owner_type = 'org' if owner_type_str == 'Organization' else 'user'
                    self.queue.append((owner_type, owner_login, self.current_round + 1))
                
                # Use cached contributors data if available
                cached_contributors = repo_obj.get('contributors', [])
                for contributor_login in cached_contributors:
                    repo.contributors.append(contributor_login)
                    
                    # Add contributors to queue
                    if contributor_login not in self.visited:
                        self.queue.append(('user', contributor_login, self.current_round + 1))
                
                # If it's a fork, add parent to queue
                if repo.is_fork and repo.forked_from and repo.forked_from not in self.visited:
                    self.queue.append(('repo', repo.forked_from, self.current_round + 1))
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
                
                # Add owner to queue from live API
                if repo_obj.owner.login not in self.visited:
                    owner_type = 'org' if repo_obj.owner.type == 'Organization' else 'user'
                    self.queue.append((owner_type, repo_obj.owner.login, self.current_round + 1))
                
                # Get contributors from live API (limited to avoid too many API calls)
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
    
    def crawl(self, show_progress: bool = True):
        """Execute the BFS crawl with progress tracking.
        
        Args:
            show_progress: Whether to show progress bars (default: True)
        """
        start_time = datetime.now()
        start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"Starting crawl at {start_time_str} for {self.max_rounds} rounds")
        if show_progress:
            print(f"\n🚀 Crawl started at {start_time_str}")
            print(f"📊 Target: {self.max_rounds} rounds\n")
        
        # Create overall progress bar for rounds
        rounds_pbar = tqdm(
            total=self.max_rounds,
            desc="Overall Progress",
            unit="round",
            position=0,
            disable=not show_progress,
            initial=self.current_round
        )
        
        try:
            while self.queue and self.current_round < self.max_rounds:
                # Start new round
                round_start_time = __import__('time').time()
                nodes_in_round = []
                
                # Count nodes to process in this round
                nodes_this_round = sum(1 for _, _, node_round in self.queue if node_round == self.current_round)
                
                # Get current time for round
                round_time_str = datetime.now().strftime("%H:%M:%S")
                
                # Create progress bar for current round
                round_pbar = tqdm(
                    total=nodes_this_round,
                    desc=f"Round {self.current_round} [{round_time_str}]",
                    unit="node",
                    position=1,
                    leave=False,
                    disable=not show_progress
                )
                
                # Process all nodes in current round
                while self.queue:
                    node_type, identifier, node_round = self.queue[0]
                    
                    if node_round > self.current_round:
                        # Reached next round
                        break
                    
                    self.queue.popleft()
                    
                    # Skip if already visited
                    if identifier in self.visited:
                        round_pbar.update(1)
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
                    
                    # Update progress
                    round_pbar.update(1)
                
                round_pbar.close()
            
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
                
                # Count items in queue by type
                queued_users = sum(1 for t, _, _ in self.queue if t in ['user', 'user_or_org'])
                queued_orgs = sum(1 for t, _, _ in self.queue if t == 'org')
                queued_repos = sum(1 for t, _, _ in self.queue if t == 'repo')
                
                round_stat = {
                    'round': self.current_round,
                    'nodes_processed': len(nodes_in_round),
                    'users_found': users_this_round,
                    'orgs_found': orgs_this_round,
                    'repos_found': repos_this_round,
                    'time_seconds': round_time,
                    'queue_size': len(self.queue),
                    'queued_users': queued_users,
                    'queued_orgs': queued_orgs,
                    'queued_repos': queued_repos,
                }
                self.round_stats.append(round_stat)
                
                # Update overall progress bar with statistics
                rounds_pbar.set_postfix({
                    'nodes': len(nodes_in_round),
                    'users': users_this_round,
                    'orgs': orgs_this_round,
                    'repos': repos_this_round,
                    'queue': f"{len(self.queue)} ({queued_users}u/{queued_orgs}o/{queued_repos}r)"
                })
                rounds_pbar.update(1)
                
                logger.info(f"Round {self.current_round} completed: "
                           f"{round_stat['nodes_processed']} nodes processed in {round_time:.1f}s, "
                           f"{round_stat['queue_size']} nodes in queue "
                           f"({queued_users} users, {queued_orgs} orgs, {queued_repos} repos)")
                
                self.current_round += 1
                
                # Save state after each round
                if self.state_file:
                    self.save_state()
        finally:
            rounds_pbar.close()
        
        end_time = datetime.now()
        end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
        duration = end_time - start_time
        
        # Format duration in human-readable format
        total_seconds = int(duration.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        
        if hours > 0:
            duration_str = f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            duration_str = f"{minutes}m {seconds}s"
        else:
            duration_str = f"{seconds}s"
        
        logger.info(f"Crawl completed after {self.current_round} rounds")
        logger.info(f"Ended at {end_time_str} (Duration: {duration_str})")
        logger.info(f"Total nodes: {len(self.visited)}")
        logger.info(f"Users: {len(self.graph.users)}, Orgs: {len(self.graph.orgs)}, "
                   f"Repos: {len(self.graph.repos)}")
        
        if show_progress:
            print(f"\n✅ Crawl completed at {end_time_str}")
            print(f"⏱️  Total duration: {duration_str}")
            print(f"📦 Collected: {len(self.graph.users)} users, {len(self.graph.orgs)} orgs, {len(self.graph.repos)} repos\n")
    
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
