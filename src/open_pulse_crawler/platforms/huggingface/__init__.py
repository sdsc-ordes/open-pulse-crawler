"""HuggingFace platform implementation."""
from .client import HuggingFaceHTTPClient
from .adapter import HuggingFaceAdapter

__all__ = ["HuggingFaceHTTPClient", "HuggingFaceAdapter"]
