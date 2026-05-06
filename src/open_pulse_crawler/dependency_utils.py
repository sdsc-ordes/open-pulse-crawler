"""Utilities for fetching repository dependencies and dependents."""

import logging
import requests
import re
from typing import List, Optional, Set, Dict, Any
from urllib.parse import urlparse

try:
    from github_dependents_info import GithubDependentsInfo
    from bs4 import BeautifulSoup
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

def fetch_dependencies_sbom(repo_full_name: str, token: str) -> Optional[List[str]]:
    """
    Fetch dependencies from GitHub SBOM API.
    Returns a list of 'owner/repo' strings, or None if fetch failed.
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
            # If dependency graph is disabled (422), cache as empty.
            # For other errors (403, 404, 5xx), return None to avoid caching.
            if resp.status_code == 422:
                return []
            return None
            
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
            
            # Also check if the package name itself looks like a GitHub repo (owner/repo)
            # This is common for some package managers or direct git dependencies
            if not found:
                name = pkg.get("name", "")
                if "/" in name and not name.startswith("@"):
                    # Potential GitHub repo format "owner/repo"
                    # Verify it's not just a scoped package name like @types/node
                    parts = name.split("/")
                    if len(parts) == 2:
                        # It's a simple heuristic, but might catch some direct deps
                        # We could verify it exists, but that's expensive.
                        # Let's assume if it's in SBOM and looks like owner/repo, it might be one.
                        # However, without a URL, it's risky.
                        pass

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
                    # Try to resolve PyPI package to GitHub repo
                    repo = resolve_pypi_package(pkg_name)
                    if repo:
                        dependencies.add(repo)
                    else:
                        # Fallback: if we can't resolve to a repo, we can't add it to the graph
                        # as a node, but we could log it or handle it differently if needed.
                        # For now, we only track dependencies that are GitHub repositories.
                        pass
            
            # Handle other package types if needed (e.g., npm, maven)
            # For now, we focus on PyPI as it's the most common in this context
            
    except Exception as e:
        logger.warning(f"Error fetching dependencies for {repo_full_name}: {e}")
        return None
        
    return list(dependencies)

def fetch_dependents(repo_full_name: str, min_stars: int = 0, max_dependents: Optional[int] = None) -> Optional[List[str]]:
    """
    Fetch dependents using github-dependents-info.
    Returns a list of 'owner/repo' strings, or None if fetch failed.
    """
    if not GITHUB_DEPENDENTS_INFO_AVAILABLE:
        logger.warning("github-dependents-info not installed, skipping dependents fetch")
        return None
        
    dependents = []
    try:
        # Define a subclass that supports max_dependents limit during collection
        class LimitedGithubDependentsInfo(GithubDependentsInfo):
            def __init__(self, *args, **kwargs):
                self.max_dependents = kwargs.pop('max_dependents', None)
                super().__init__(*args, **kwargs)

            def collect(self):
                if self.overwrite_progress or not self.load_progress():
                    self.compute_packages()
                    self.save_progress_packages_list()

                total_collected = 0

                for package in self.packages:
                    if "public_dependents" in package:
                        continue

                    nextExists = True
                    result = []

                    if package["id"] is not None:
                        url = self.url_init + "?package_id=" + package["id"]
                        if self.debug is True:
                            logging.info("Package " + package["name"] + ": browsing " + url + " ...")
                    else:
                        url = self.url_init + ""
                        if self.debug is True:
                            logging.info("Package " + self.repo + ": browsing" + url + " ...")
                    package["url"] = url
                    package["public_dependent_stars"] = 0
                    page_number = 1

                    r = self.requests_retry_session().get(url)
                    soup = BeautifulSoup(r.content, "html.parser")
                    svg_item = soup.find("svg", {"class": "octicon-code-square"})
                    if svg_item is not None:
                        a_around_svg = svg_item.parent
                        total_dependents = self.get_int(
                            a_around_svg.text.replace("Repositories", "").replace("Repository", "").strip()
                        )
                    else:
                        total_dependents = 0

                    while nextExists:
                        r = self.requests_retry_session().get(url)
                        soup = BeautifulSoup(r.content, "html.parser")
                        total_public_stars = 0

                        for t in soup.findAll("div", {"class": "Box-row"}):
                            result_item = {
                                "name": "{}/{}".format(
                                    t.find("a", {"data-repository-hovercards-enabled": ""}).text,
                                    t.find("a", {"data-hovercard-type": "repository"}).text,
                                ),
                                "stars": self.get_int(
                                    t.find("svg", {"class": "octicon-star"}).parent.text.strip().replace(",", "")
                                ),
                            }
                            image = t.findAll("img", {"class": "avatar"})
                            if len(image) > 0 and image[0].attrs and "src" in image[0].attrs:
                                result_item["img"] = image[0].attrs["src"]
                            if "/" in result_item["name"]:
                                splits = str(result_item["name"]).split("/")
                                result_item["owner"] = splits[0]
                                result_item["repo_name"] = splits[1]
                            if self.min_stars is not None and result_item["stars"] < self.min_stars:
                                continue
                            
                            result += [result_item]
                            total_public_stars += result_item["stars"]
                            total_collected += 1

                            if self.max_dependents is not None and total_collected >= self.max_dependents:
                                nextExists = False
                                break

                        if not nextExists:
                            break

                        nextExists = False
                        paginate_container = soup.find("div", {"class": "paginate-container"})
                        if paginate_container is not None:
                            for u in paginate_container.findAll("a"):
                                if u.text == "Next":
                                    nextExists = True
                                    url = u["href"]
                                    page_number = page_number + 1
                                    if self.debug is True:
                                        logging.info("  - browsing page " + str(page_number))

                    if self.sort_key == "stars":
                        result = sorted(result, key=lambda d: d[self.sort_key], reverse=True)
                    else:
                        result = sorted(result, key=lambda d: d[self.sort_key])
                    if self.debug is True:
                        for r in result:
                            logging.info(r)

                    total_public_dependents = len(result)
                    package["public_dependents"] = result
                    package["public_dependents_number"] = total_public_dependents
                    package["public_dependent_stars"] = total_public_stars
                    package["private_dependents_number"] = total_dependents - total_public_dependents
                    package["total_dependents_number"] = total_dependents if total_dependents > 0 else total_public_dependents

                    package["badges"] = {}
                    package["badges"]["total"] = self.build_badge(
                        "Used%20by", package["total_dependents_number"], url=package["url"]
                    )
                    package["badges"]["public"] = self.build_badge(
                        "Used%20by%20(public)", package["public_dependents_number"], url=package["url"]
                    )
                    package["badges"]["private"] = self.build_badge(
                        "Used%20by%20(private)", package["private_dependents_number"], url=package["url"]
                    )
                    package["badges"]["stars"] = self.build_badge(
                        "Used%20by%20(stars)", package["public_dependent_stars"], url=package["url"]
                    )

                    self.all_public_dependent_repos += result
                    self.total_sum += package["total_dependents_number"]
                    self.total_public_sum += package["public_dependents_number"]
                    self.total_private_sum += package["private_dependents_number"]
                    self.total_stars_sum += package["public_dependent_stars"]

                    if self.debug is True:
                        logging.info("Total for package: " + str(total_public_dependents))
                        logging.info("")
                    self.save_progress(package)

                    if self.max_dependents is not None and total_collected >= self.max_dependents:
                        break

                self.all_public_dependent_repos = list({v["name"]: v for v in self.all_public_dependent_repos}.values())

                # Use .get() with a default so packages that were skipped by
                # the early-exit on max_dependents (and therefore never had
                # public_dependent_stars set on line 206) don't crash the
                # sort — they simply sort to the bottom.
                if self.sort_key == "stars":
                    self.packages = sorted(
                        self.packages,
                        key=lambda d: d.get("public_dependent_stars", 0),
                        reverse=True,
                    )
                    self.all_public_dependent_repos = sorted(
                        self.all_public_dependent_repos,
                        key=lambda d: d.get("stars", 0),
                        reverse=True,
                    )
                else:
                    self.packages = sorted(self.packages, key=lambda d: d.get("name", ""))
                    self.all_public_dependent_repos = sorted(
                        self.all_public_dependent_repos, key=lambda d: d.get("name", "")
                    )

                doc_url_to_use = "https://github.com/nvuillam/github-dependents-info"
                if self.doc_url is not None:
                    doc_url_to_use = self.doc_url
                elif self.markdown_file is not None:
                    repo_url_part = self.outputrepo if "/" in self.outputrepo else self.repo
                    doc_url_to_use = f"https://github.com/{repo_url_part}/blob/main/{self.markdown_file}"
                self.badges["total_doc_url"] = self.build_badge("Used%20by", self.total_sum, url=doc_url_to_use)

                self.badges["total"] = self.build_badge("Used%20by", self.total_sum)
                self.badges["public"] = self.build_badge("Used%20by%20(public)", self.total_public_sum)

        # Initialize with sort_key='stars' to get most important ones
        gh_deps = LimitedGithubDependentsInfo(
            repo_full_name, 
            debug=False, 
            sort_key="stars", 
            min_stars=min_stars,
            max_dependents=max_dependents
        )
        
        # This can be slow
        # Suppress specific warning from github-dependents-info about integer parsing
        # See https://github.com/nvuillam/github-dependents-info/issues/657
        class WarningFilter(logging.Filter):
            def filter(self, record):
                return "Unable to get integer from" not in record.getMessage()

        root_logger = logging.getLogger()
        warning_filter = WarningFilter()
        root_logger.addFilter(warning_filter)
        
        try:
            gh_deps.collect()
        finally:
            root_logger.removeFilter(warning_filter)
        
        # Our LimitedGithubDependentsInfo override populates ``self.packages``
        # in place — each package dict gets ``public_dependents`` set on
        # line 276. The parent class's ``self.result`` is never built by our
        # override, so iterate ``packages`` directly.
        for pkg in getattr(gh_deps, "packages", []) or []:
            if not isinstance(pkg, dict):
                continue
            public_deps = pkg.get("public_dependents", []) or []
            for dep in public_deps:
                if not isinstance(dep, dict):
                    continue
                repo_name = dep.get("name")  # 'owner/repo'
                if repo_name:
                    dependents.append(repo_name)
                    if max_dependents is not None and len(dependents) >= max_dependents:
                        break
            if max_dependents is not None and len(dependents) >= max_dependents:
                break
                    
    except Exception as e:
        logger.warning(f"Error fetching dependents for {repo_full_name}: {e}")
        return None
        
    return dependents
