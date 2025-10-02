"""GitHub API client with multi-token support and caching."""

import os
import time
import logging
import random
import threading
from typing import List, Optional, Dict, Any
from pathlib import Path
import json
from github import Github, GithubException, RateLimitExceededException
from github.Repository import Repository
from github.NamedUser import NamedUser
from github.Organization import Organization
import hashlib

logger = logging.getLogger(__name__)


class APICache:
    """Simple file-based cache for API responses."""
    
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_cache_key(self, endpoint: str, params: str) -> str:
        """Generate cache key from endpoint and params."""
        content = f"{endpoint}:{params}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def get(self, endpoint: str, params: str = "") -> Optional[Any]:
        """Get cached response."""
        key = self._get_cache_key(endpoint, params)
        cache_file = self.cache_dir / f"{key}.json"
        
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read cache file {cache_file}: {e}")
                return None
        return None
    
    def set(self, endpoint: str, params: str, data: Any):
        """Store response in cache."""
        key = self._get_cache_key(endpoint, params)
        cache_file = self.cache_dir / f"{key}.json"
        
        try:
            with open(cache_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Failed to write cache file {cache_file}: {e}")


class GitHubClient:
    """GitHub API client with multi-token support and rate limiting."""
    
    def __init__(
        self, 
        tokens: List[str], 
        cache_dir: Optional[Path] = None,
        request_delay: float = 0.0,
        max_concurrent_requests: int = 5,
        rate_limit_buffer: int = 50
    ):
        """
        Initialize GitHub client.
        
        Args:
            tokens: List of GitHub personal access tokens
            cache_dir: Directory for caching API responses
            request_delay: Minimum delay (in seconds) between API requests (default: 0.0)
            max_concurrent_requests: Maximum number of concurrent API requests (default: 5)
            rate_limit_buffer: Number of requests to keep as buffer before waiting (default: 50)
        """
        if not tokens:
            raise ValueError("At least one GitHub token is required")
        
        self.tokens = tokens
        self.current_token_idx = 0
        self.clients = [Github(token) for token in tokens]
        self.current_client = self.clients[0]
        
        # Rate limiting configuration
        self.request_delay = request_delay
        self.rate_limit_buffer = rate_limit_buffer
        self.semaphore = threading.Semaphore(max_concurrent_requests)
        self.last_request_time = 0
        self.request_lock = threading.Lock()
        
        # Cache setup
        self.cache = APICache(cache_dir) if cache_dir else None
        
        # Statistics
        self.stats = {
            'api_calls': 0,
            'cache_hits': 0,
            'rate_limit_waits': 0,
            'token_switches': 0,
            'throttle_waits': 0,
        }
        
        logger.info(f"Initialized GitHub client with {len(tokens)} token(s)")
        logger.info(f"Rate limiting: delay={request_delay}s, max_concurrent={max_concurrent_requests}, buffer={rate_limit_buffer}")
        
        # Log initial rate limit status
        self._log_rate_limit_status()
    
    def _log_rate_limit_status(self):
        """Log current rate limit status for all tokens."""
        try:
            for idx, client in enumerate(self.clients):
                rate_limit = client.get_rate_limit()
                core = rate_limit.resources.core
                logger.info(
                    f"Token {idx + 1}/{len(self.clients)}: "
                    f"{core.remaining}/{core.limit} requests remaining, "
                    f"resets at {core.reset.strftime('%H:%M:%S')}"
                )
        except Exception as e:
            logger.warning(f"Could not retrieve rate limit status: {e}")
    
    def _rotate_token(self):
        """Rotate to the next available token."""
        old_idx = self.current_token_idx
        self.current_token_idx = (self.current_token_idx + 1) % len(self.clients)
        self.current_client = self.clients[self.current_token_idx]
        self.stats['token_switches'] += 1
        logger.info(f"Switched from token {old_idx + 1} to token {self.current_token_idx + 1}/{len(self.clients)}")
        
        # Log new token's rate limit status
        try:
            rate_limit = self.current_client.get_rate_limit()
            core = rate_limit.resources.core
            logger.info(f"New token has {core.remaining}/{core.limit} requests remaining")
        except Exception as e:
            logger.warning(f"Could not check new token's rate limit: {e}")
    
    def _check_rate_limit(self) -> bool:
        """
        Check rate limit and wait if necessary.
        Uses configured buffer to proactively manage rate limits.
        
        Returns:
            True if rate limit is OK, False if all tokens are exhausted
        """
        try:
            rate_limit = self.current_client.get_rate_limit()
            core = rate_limit.resources.core  # Fixed: use resources.core instead of core
            
            remaining = core.remaining
            limit = core.limit
            reset_time = core.reset.timestamp()
            
            logger.debug(
                f"Rate limit check: {remaining}/{limit} requests remaining, "
                f"resets in {max(0, reset_time - time.time()):.0f}s"
            )
            
            if remaining < self.rate_limit_buffer:
                logger.warning(
                    f"Rate limit low: {remaining}/{limit} remaining (buffer: {self.rate_limit_buffer})"
                )
                
                # Try rotating to another token if available
                if len(self.clients) > 1:
                    initial_idx = self.current_token_idx
                    best_token_idx = initial_idx
                    best_remaining = remaining
                    
                    # Check all tokens to find the one with most remaining requests
                    for _ in range(len(self.clients)):
                        self._rotate_token()
                        try:
                            new_rate_limit = self.current_client.get_rate_limit()
                            new_remaining = new_rate_limit.resources.core.remaining
                            
                            if new_remaining > best_remaining:
                                best_remaining = new_remaining
                                best_token_idx = self.current_token_idx
                            
                            # If we found a token with sufficient requests, use it
                            if new_remaining >= self.rate_limit_buffer:
                                logger.info(
                                    f"Switched to token {self.current_token_idx + 1} "
                                    f"with {new_remaining} requests remaining"
                                )
                                return True
                        except Exception as e:
                            logger.warning(f"Error checking token {self.current_token_idx + 1}: {e}")
                    
                    # Switch to the best token we found
                    if best_token_idx != self.current_token_idx:
                        self.current_token_idx = best_token_idx
                        self.current_client = self.clients[best_token_idx]
                        logger.info(
                            f"Using token {best_token_idx + 1} with {best_remaining} requests "
                            f"(best available)"
                        )
                    
                    # If the best token still doesn't have enough, wait
                    if best_remaining < self.rate_limit_buffer:
                        remaining = best_remaining
                    else:
                        return True
                
                # Calculate wait time with small buffer
                wait_time = max(0, reset_time - time.time()) + 5
                logger.warning(
                    f"All tokens low on requests. Waiting {wait_time:.0f}s for rate limit reset..."
                )
                self.stats['rate_limit_waits'] += 1
                time.sleep(wait_time)
                
                # Verify rate limit has reset
                rate_limit = self.current_client.get_rate_limit()
                new_remaining = rate_limit.resources.core.remaining
                logger.info(f"Rate limit reset. Now have {new_remaining} requests available")
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking rate limit: {e}")
            # On error, add a small delay and continue
            time.sleep(2)
            return True
    
    def _apply_request_delay(self):
        """Apply configured delay between requests to avoid bursting."""
        if self.request_delay > 0:
            with self.request_lock:
                current_time = time.time()
                time_since_last = current_time - self.last_request_time
                
                if time_since_last < self.request_delay:
                    sleep_time = self.request_delay - time_since_last
                    logger.debug(f"Throttling: sleeping for {sleep_time:.3f}s")
                    time.sleep(sleep_time)
                    self.stats['throttle_waits'] += 1
                
                self.last_request_time = time.time()
    
    def _make_request(self, func, *args, max_retries: int = 3, **kwargs):
        """
        Make an API request with retry logic, rate limit handling, and throttling.
        Uses semaphore to limit concurrent requests.
        
        Args:
            func: Function to call
            max_retries: Maximum number of retry attempts
            *args, **kwargs: Arguments to pass to the function
        
        Returns:
            Result of the function call
        """
        with self.semaphore:  # Limit concurrent requests
            self.stats['api_calls'] += 1
            
            for attempt in range(max_retries):
                try:
                    # Check rate limit before making request
                    self._check_rate_limit()
                    
                    # Apply throttling delay
                    self._apply_request_delay()
                    
                    # Make the actual request
                    result = func(*args, **kwargs)
                    return result
                    
                except RateLimitExceededException as e:
                    logger.warning(
                        f"Rate limit exceeded (attempt {attempt + 1}/{max_retries}). "
                        f"This shouldn't happen with our proactive checking."
                    )
                    if attempt < max_retries - 1:
                        self._check_rate_limit()
                    else:
                        logger.error("Rate limit exceeded after all retries")
                        raise
                        
                except GithubException as e:
                    if e.status == 404:
                        logger.debug(f"Resource not found: {args}")
                        return None
                    elif e.status in [403, 429]:  # Forbidden or Too Many Requests
                        # Calculate backoff with jitter
                        base_delay = 2 ** attempt
                        jitter = random.uniform(0, 0.1 * base_delay)
                        backoff_time = base_delay + jitter
                        
                        logger.warning(
                            f"Rate limit or access denied (attempt {attempt + 1}/{max_retries}). "
                            f"Backing off for {backoff_time:.1f}s"
                        )
                        
                        if attempt < max_retries - 1:
                            time.sleep(backoff_time)
                            # Force rate limit check
                            self._check_rate_limit()
                        else:
                            logger.error(f"Failed after {max_retries} retries: {e.status} - {e.data}")
                            raise
                    elif e.status == 422:
                        logger.warning(f"Validation error: {e.data}")
                        return None
                    else:
                        logger.error(f"GitHub API error: {e.status} - {e.data}")
                        if attempt < max_retries - 1:
                            backoff_time = 2 ** attempt + random.uniform(0, 1)
                            time.sleep(backoff_time)
                        else:
                            raise
                            
                except Exception as e:
                    logger.error(f"Unexpected error in API request: {e}", exc_info=True)
                    if attempt < max_retries - 1:
                        backoff_time = 2 ** attempt + random.uniform(0, 1)
                        logger.info(f"Retrying after {backoff_time:.1f}s...")
                        time.sleep(backoff_time)
                    else:
                        logger.error(f"Failed after {max_retries} retries")
                        raise
            
            return None
    
    def get_user(self, username: str) -> Optional[NamedUser]:
        """Get user by username with caching."""
        cache_key = f"user:{username}"
        
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached:
                self.stats['cache_hits'] += 1
                # Return a simplified dict instead of full object for cached data
                return cached
        
        try:
            user = self._make_request(self.current_client.get_user, username)
            if user and self.cache:
                # Fetch and cache repos along with basic user info
                repos_data = []
                try:
                    repos = self._make_request(user.get_repos)
                    repos_data = [{'full_name': r.full_name, 'fork': r.fork} for r in repos]
                except Exception as e:
                    logger.warning(f"Failed to get repos for caching user {username}: {e}")
                
                # Fetch and cache user's organization memberships
                orgs_data = []
                try:
                    orgs = self._make_request(user.get_orgs)
                    orgs_data = [org.login for org in orgs]
                except Exception as e:
                    logger.warning(f"Failed to get organizations for caching user {username}: {e}")
                
                # Cache basic user info + repos + orgs
                user_data = {
                    'login': user.login,
                    'name': user.name or '',
                    'id': user.id,
                    'type': user.type,
                    'repos': repos_data,
                    'orgs': orgs_data,
                }
                self.cache.set(cache_key, '', user_data)
            return user
        except Exception as e:
            logger.error(f"Failed to get user {username}: {e}")
            return None
    
    def get_organization(self, org_name: str) -> Optional[Organization]:
        """Get organization by name with caching."""
        cache_key = f"org:{org_name}"
        
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached:
                self.stats['cache_hits'] += 1
                return cached
        
        try:
            org = self._make_request(self.current_client.get_organization, org_name)
            if org and self.cache:
                # Fetch and cache members and repos along with basic org info
                members_data = []
                repos_data = []
                try:
                    members = self._make_request(org.get_members)
                    members_data = [m.login for m in members]
                except Exception as e:
                    logger.warning(f"Failed to get members for caching org {org_name}: {e}")
                
                try:
                    repos = self._make_request(org.get_repos)
                    repos_data = [{'full_name': r.full_name, 'fork': r.fork} for r in repos]
                except Exception as e:
                    logger.warning(f"Failed to get repos for caching org {org_name}: {e}")
                
                org_data = {
                    'login': org.login,
                    'name': org.name or '',
                    'id': org.id,
                    'type': 'Organization',
                    'members': members_data,
                    'repos': repos_data,
                }
                self.cache.set(cache_key, '', org_data)
            return org
        except Exception as e:
            logger.error(f"Failed to get organization {org_name}: {e}")
            return None
    
    def get_repository(self, repo_full_name: str) -> Optional[Repository]:
        """Get repository by full name with caching."""
        cache_key = f"repo:{repo_full_name}"
        
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached:
                self.stats['cache_hits'] += 1
                return cached
        
        try:
            repo = self._make_request(self.current_client.get_repo, repo_full_name)
            if repo and self.cache:
                # Fetch and cache contributors along with basic repo info
                contributors_data = []
                try:
                    contributors = self._make_request(repo.get_contributors)
                    # Limit to top 10 contributors for caching
                    contributors_data = [c.login for i, c in enumerate(contributors) if i < 10]
                except Exception as e:
                    logger.warning(f"Failed to get contributors for caching repo {repo_full_name}: {e}")
                
                repo_data = {
                    'full_name': repo.full_name,
                    'name': repo.name,
                    'id': repo.id,
                    'owner': repo.owner.login,
                    'owner_type': repo.owner.type,
                    'is_fork': repo.fork,
                    'parent': repo.parent.full_name if repo.parent else None,
                    'contributors': contributors_data,
                }
                self.cache.set(cache_key, '', repo_data)
            return repo
        except Exception as e:
            logger.error(f"Failed to get repository {repo_full_name}: {e}")
            return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get client statistics including rate limit info for all tokens."""
        stats = self.stats.copy()
        
        # Add current rate limit info for all tokens
        rate_limits = []
        try:
            for idx, client in enumerate(self.clients):
                rate_limit = client.get_rate_limit()
                core = rate_limit.resources.core  # Fixed: use resources.core
                rate_limits.append({
                    'token_index': idx + 1,
                    'remaining': core.remaining,
                    'limit': core.limit,
                    'reset': core.reset.isoformat(),
                    'is_current': idx == self.current_token_idx,
                })
        except Exception as e:
            logger.warning(f"Could not retrieve rate limit stats: {e}")
        
        stats['rate_limits'] = rate_limits
        stats['efficiency'] = {
            'cache_hit_rate': (
                self.stats['cache_hits'] / max(1, self.stats['api_calls']) * 100
            ),
            'requests_per_wait': (
                self.stats['api_calls'] / max(1, self.stats['rate_limit_waits'])
            ),
        }
        
        return stats
