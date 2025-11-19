"""Utilities for fetching repository dependencies and dependents."""

import logging
import requests
import re
from typing import List, Optional, Set, Dict, Any
from urllib.parse import urlparse

try:
    from github_dependents_info import GithubDependentsInfo
    GITHUB_DEPENDENTS_INFO_AVAILABLE = True
except ImportError:
    GITHUB_DEPENDENTS_INFO_AVAILABLE = False

logger = logging.getLogger(__name__)

def normalize_github_url(url: str) -> Optional[str]:
    """Extract owner/repo from a GitHub URL."""
    if not url:
        return None
    
    # Handle ssh style
    if url.startswith("git@github.com:"):
        url = url.replace("git@github.com:", "https://github.com/")
    
    parsed = urlparse(url)
    if parsed.netloc != "github.com":
        return None
    
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) >= 2:
        return f"{path_parts[0]}/{path_parts[1]}"
    return None

def resolve_pypi_package(package_name: str) -> Optional[str]:
    """Resolve a PyPI package name to a GitHub repository."""
    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        info = data.get("info", {})
        
        # Check all URLs in metadata
        candidates = []
        if info.get("home_page"):
            candidates.append(info["home_page"])
        
        project_urls = info.get("project_urls") or {}
        if project_urls:
            candidates.extend(project_urls.values())
            
        for url in candidates:
            if url and "github.com" in url:
                repo = normalize_github_url(url)
                if repo:
                    return repo
                    
        return None
    except Exception as e:
        logger.debug(f"Error resolving PyPI package {package_name}: {e}")
        return None

def fetch_dependencies_sbom(repo_full_name: str, token: str) -> List[str]:
    """
    Fetch dependencies from GitHub SBOM API.
    Returns a list of 'owner/repo' strings.
    """
    url = f"https://api.github.com/repos/{repo_full_name}/dependency-graph/sbom"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    dependencies = set()
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.debug(f"Failed to fetch SBOM for {repo_full_name}: {resp.status_code}")
            return []
            
        data = resp.json()
        if "sbom" not in data or "packages" not in data["sbom"]:
            return []
            
        for pkg in data["sbom"]["packages"]:
            # Check external refs first
            found = False
            for ref in pkg.get("externalRefs", []):
                if "github.com" in ref.get("url", ""):
                    repo = normalize_github_url(ref["url"])
                    if repo:
                        dependencies.add(repo)
                        found = True
                        break
            
            if found:
                continue
                
            # If not found, try to resolve based on PURL
            purl = pkg.get("purl", "")
            
            # Try to find PURL in externalRefs if not at top level
            if not purl:
                for ref in pkg.get("externalRefs", []):
                    if ref.get("referenceType") == "purl":
                        purl = ref.get("referenceLocator", "")
                        break
            
            if purl.startswith("pkg:pypi/"):
                # Extract name
                # pkg:pypi/name@version or pkg:pypi/name
                # Remove version part if present
                if "@" in purl:
                    purl_base = purl.split("@")[0]
                else:
                    purl_base = purl
                
                # Extract package name
                match = re.match(r"pkg:pypi/([^/?#]+)", purl_base)
                if match:
                    pkg_name = match.group(1)
                    repo = resolve_pypi_package(pkg_name)
                    if repo:
                        dependencies.add(repo)
                        
    except Exception as e:
        logger.warning(f"Error fetching dependencies for {repo_full_name}: {e}")
        
    return list(dependencies)

def fetch_dependents(repo_full_name: str, min_stars: int = 0, max_dependents: Optional[int] = None) -> List[str]:
    """
    Fetch dependents using github-dependents-info.
    Returns a list of 'owner/repo' strings.
    """
    if not GITHUB_DEPENDENTS_INFO_AVAILABLE:
        logger.warning("github-dependents-info not installed, skipping dependents fetch")
        return []
        
    dependents = []
    try:
        # Initialize with sort_key='stars' to get most important ones
        gh_deps = GithubDependentsInfo(
            repo_full_name, 
            debug=False, 
            sort_key="stars", 
            min_stars=min_stars
        )
        
        # This can be slow
        gh_deps.collect()
        
        if hasattr(gh_deps, 'result') and "packages" in gh_deps.result:
            for pkg_entry in gh_deps.result["packages"]:
                # Handle case where packages might be a list of lists or just a list of dicts
                packages_list = pkg_entry if isinstance(pkg_entry, list) else [pkg_entry]
                
                for pkg in packages_list:
                    if not isinstance(pkg, dict):
                        continue
                        
                    public_deps = pkg.get("public_dependents", [])
                    for dep in public_deps:
                        repo_name = dep.get("name") # This is usually 'owner/repo'
                        if repo_name:
                            dependents.append(repo_name)
                            if max_dependents is not None and len(dependents) >= max_dependents:
                                break
                    if max_dependents is not None and len(dependents) >= max_dependents:
                        break
                if max_dependents is not None and len(dependents) >= max_dependents:
                    break
                    
    except Exception as e:
        logger.warning(f"Error fetching dependents for {repo_full_name}: {e}")
        
    return dependents
