"""GitHub platform implementation: REST + GraphQL clients."""

from .client import (  # noqa: F401 — public re-exports
    CACHE_DIR_ENV,
    CACHE_TTL_ENV,
    DEFAULT_CACHE_DIR,
    DEFAULT_CACHE_TTL_DAYS,
    APICache,
    GitHubClient,
    resolve_cache_dir,
    resolve_cache_ttl,
)
from .graphql import GitHubGraphQLClient  # noqa: F401
