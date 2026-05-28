"""GitLab platform implementation: thin python-gitlab wrapper."""

from .client import GitLabClient  # noqa: F401 — public re-export

__all__ = ["GitLabClient"]
