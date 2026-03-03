"""Bearer-token authentication helpers for the Open Pulse Crawler API."""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer()


def _get_api_token() -> str:
    """Return the configured API_TOKEN or raise 503 if unset."""
    token = os.environ.get("API_TOKEN", "")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API_TOKEN is not configured on the server",
        )
    return token


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """Dependency that validates the Bearer token against the ``API_TOKEN`` env var.

    Uses :func:`secrets.compare_digest` for constant-time comparison.
    """
    api_token = _get_api_token()
    if not secrets.compare_digest(credentials.credentials, api_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )
    return credentials.credentials
