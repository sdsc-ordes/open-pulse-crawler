"""Infoscience (DSpace 7) platform implementation."""
from .client import InfoscienceClient
from .adapter import InfoscienceAdapter

__all__ = ["InfoscienceClient", "InfoscienceAdapter"]
