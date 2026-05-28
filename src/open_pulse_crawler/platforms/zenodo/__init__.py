"""Zenodo platform implementation."""
from .adapter import ZenodoAdapter
from .client import ZenodoClient

__all__ = ["ZenodoAdapter", "ZenodoClient"]
