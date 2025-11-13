# GitHub Dependency Graph API Investigation

**Date:** November 12, 2025  
**Test Repository:** `DeepLabCut/DeepLabCut`  
**Web UI:** https://github.com/DeepLabCut/DeepLabCut/network/dependents

## Summary

GitHub provides **limited programmatic access** to dependency graph data through its API. While the web UI shows comprehensive dependency and dependent information, most of this data is **not available through public REST or GraphQL APIs**.

## What IS Available via API

### 1. ✅ Dependencies (What This Repo Depends On)

#### GraphQL API - Recommended
**Endpoint:** `https://api.github.com/graphql`

**Query:**
```graphql
{
  repository(owner: "OWNER", name: "REPO") {
    dependencyGraphManifests(first: 10) {
      nodes {
        filename
        parseable
        dependencies(first: 100) {
          nodes {
            packageName
            requirements
            hasDependencies
            packageManager
            repository {
              nameWithOwner
              url
            }
          }
        }
      }
    }
  }
}
```

**What You Get:**
- ✅ Manifest files (e.g., `requirements.txt`, `package.json`, `pyproject.toml`, GitHub Actions workflows)
- ✅ All dependencies listed in each manifest
- ✅ Package names and version requirements
- ✅ Package manager (PIP, NPM, ACTIONS, etc.)
- ✅ **GitHub repository link** for dependencies that are on GitHub
- ✅ Pagination support (use `after` cursor)

**Example Output for DeepLabCut:**
```
Found 7 manifests:
  - pyproject.toml (parseable)
  - requirements.txt (20 dependencies)
    - albumentations (PIP) → albumentations-team/albumentations
    - dlclibrary (PIP) → DeepLabCut/DLClibrary
    - einops (PIP) → arogozhnikov/einops
  - .github/workflows/codespall.yml (3 dependencies)
    - actions/checkout (ACTIONS) → actions/checkout
    - codespell-project/actions-codespell (ACTIONS)
```

#### REST API - SBOM (Software Bill of Materials)
**Endpoint:** `GET /repos/{owner}/{repo}/dependency-graph/sbom`

**Response:** SPDX 2.3 format with all packages
```json
{
  "sbom": {
    "spdxVersion": "SPDX-2.3",
    "packages": [
      {
        "name": "peaceiris/actions-gh-pages",
        "SPDXID": "SPDXRef-githubactions-peaceiris-actions-gh-pages-3.9.3-75c946",
        "versionInfo": "3.9.3",
        "externalRefs": [
          {
            "referenceType": "purl",
            "referenceLocator": "pkg:githubactions/peaceiris/actions-gh-pages@3.9.3"
          }
        ]
      }
    ]
  }
}
```

**What You Get:**
- ✅ Complete list of all dependencies (42 packages for DeepLabCut)
- ✅ SPDX identifiers and relationships
- ✅ Package URLs (purl) - e.g., `pkg:pypi/numpy`, `pkg:githubactions/actions/checkout`
- ✅ Version requirements (when available)
- ✅ License information
- ❌ **NO GitHub repository links** (only package identifiers)
- ❌ **NO repository metadata** (stars, forks, description)

**Example Package:**
```json
{
  "name": "torch",
  "SPDXID": "SPDXRef-pypi-torch-75c946",
  "versionInfo": ">= 2.0.0",
  "downloadLocation": "NOASSERTION",
  "externalRefs": [{
    "referenceType": "purl",
    "referenceLocator": "pkg:pypi/torch"
  }]
}
```

**Verdict:** GraphQL is **strongly preferred** for Open Pulse Crawler - provides GitHub repository links and metadata.

### 2. ✅ PyGithub Library Methods

The `PyGithub` library provides these dependency-related methods on `Repository` objects:
- `get_dependabot_alert(alert_number)` - Get a specific Dependabot security alert
- `get_dependabot_alerts()` - List all Dependabot alerts
- `update_dependabot_alert(alert_number, state, dismissed_reason, dismissed_comment)` - Update alert status

**Note:** These are for **security alerts**, not general dependency information.

## What is NOT Available via API

### ❌ Dependents (Who Depends on This Repo)

**Web UI Shows:**
- 30+ repositories that depend on DeepLabCut
- Package information
- Pagination through dependents

**API Status:**
- ❌ No REST API endpoint (`/repos/{owner}/{repo}/dependents` returns 404)
- ❌ No GraphQL field for dependents (only `dependencyGraphManifests` for dependencies)
- ❌ Data only accessible by scraping HTML from `https://github.com/{owner}/{repo}/network/dependents`

**HTML Scraping Notes:**
- Found 30 dependent repositories in HTML with `data-test-id="dg-repo-pkg-dependent"`
- Pagination links available in HTML
- Would require parsing HTML (not recommended, violates ToS, fragile)

## Use Cases for Open Pulse Crawler

### Can Implement:
1. **Dependency Discovery** - Starting from a repository, discover all its dependencies that are GitHub repositories
2. **Dependency Chain Mapping** - Follow dependencies recursively to build a dependency tree
3. **GitHub Actions Discovery** - Identify which GitHub Actions a repo uses
4. **Multi-Manifest Analysis** - Analyze projects with multiple dependency files (requirements.txt, pyproject.toml, etc.)
5. **Package Manager Detection** - Identify which package managers a project uses

### Cannot Implement (without scraping):
1. ❌ **Reverse Dependency Discovery** - Find which repositories depend on a given package
2. ❌ **Impact Analysis** - Determine how many projects would be affected by changes to a package
3. ❌ **Dependent Repository Lists** - Get programmatic list of repositories using a package

## Recommended Integration for Open Pulse Crawler

### Option 1: Enhance Repository Discovery (Recommended)
Add dependency-based discovery to the BFS crawler:

```python
def _process_repository(self, repo_id: str, round_num: int) -> None:
    """Process a repository and discover its dependencies."""
    # Existing code...
    
    # NEW: Add dependency discovery
    dependencies = self._get_repository_dependencies(repo_id)
    for dep in dependencies:
        if dep.repository:  # Only GitHub-hosted dependencies
            self.queue.append(('repo', dep.repository.nameWithOwner, round_num + 1))
```

**Benefits:**
- Discover dependency networks organically
- Map which projects use common libraries
- Track GitHub Actions usage patterns

### Option 2: Dedicated Dependency Analysis
Create separate analysis tools:
- `analyze_dependencies.py` - Fetch and analyze dependency graphs
- Export dependency data alongside existing graph exports
- Visualize dependency trees separately from user/org networks

## Example GraphQL Query for Full Data

```graphql
{
  repository(owner: "DeepLabCut", name: "DeepLabCut") {
    name
    nameWithOwner
    url
    dependencyGraphManifests(first: 10) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        filename
        parseable
        blobPath
        dependencies(first: 100) {
          pageInfo {
            hasNextPage
            endCursor
          }
          nodes {
            packageName
            requirements
            hasDependencies
            packageManager
            repository {
              nameWithOwner
              url
              description
              stargazerCount
              forkCount
            }
          }
        }
      }
    }
  }
}
```

## Rate Limiting Considerations

### GraphQL API
- **Cost:** Each query has a calculated cost based on complexity
- **Limit:** 5,000 points per hour per token
- **Impact:** Dependency queries are relatively cheap (~10-50 points depending on pagination)
- **Recommendation:** Should not significantly impact existing rate limits

### REST API (SBOM)
- **Same limits** as other REST endpoints: 5,000 requests/hour per token
- Each SBOM call counts as 1 request

## Documentation Links

- [GitHub REST API - Dependency Graph](https://docs.github.com/en/rest/dependency-graph)
- [GitHub GraphQL API - Repository.dependencyGraphManifests](https://docs.github.com/en/graphql/reference/objects#repository)
- [SPDX 2.3 Specification](https://spdx.github.io/spdx-spec/v2.3/)

## Conclusion

**YES, you can get dependency information via the API!** ✅

**What you CAN get:**
- All dependencies of a repository (via GraphQL - preferred)
- GitHub repository links for dependencies
- Multiple manifest files per project
- Package manager information

**What you CANNOT get:**
- Dependents (who uses this package) - Web UI only
- Reverse dependency lookup

**Recommendation:** Implement dependency discovery in Open Pulse Crawler using GraphQL API to enhance the relationship graph with dependency edges.

## Parsing SBOM Files

While GraphQL is preferred for real-time API access, SBOM files can be useful for:
- Offline analysis
- Archival purposes
- Compliance/audit requirements
- Comparing dependency changes over time

### SBOM Structure

```python
import json

with open('sbom.json', 'r') as f:
    sbom = json.load(f)

# SBOM contains:
sbom['spdxVersion']      # SPDX version (e.g., "SPDX-2.3")
sbom['packages']         # List of all packages
sbom['relationships']    # Dependency relationships

# Each package has:
pkg = sbom['packages'][0]
pkg['name']              # Package name
pkg['SPDXID']            # Unique identifier
pkg['versionInfo']       # Version requirements (may be "N/A")
pkg['externalRefs']      # Package URLs (purls)
pkg['licenseDeclared']   # License (if available)

# Relationships link packages:
rel = sbom['relationships'][0]
rel['spdxElementId']     # Source package SPDXID
rel['relatedSpdxElement'] # Target package SPDXID
rel['relationshipType']  # Usually "DEPENDS_ON" or "DESCRIBES"
```

### Extracting Useful Information

```python
# Get all PyPI packages
pypi_packages = [
    pkg for pkg in sbom['packages'] 
    if 'pypi' in pkg['SPDXID'].lower()
]

# Get all GitHub Actions
github_actions = [
    pkg for pkg in sbom['packages'] 
    if 'githubactions' in pkg['SPDXID'].lower()
]

# Extract package names and versions
dependencies = []
for pkg in pypi_packages:
    dependencies.append({
        'name': pkg['name'],
        'version': pkg.get('versionInfo', 'N/A'),
        'purl': next(
            (ref['referenceLocator'] for ref in pkg.get('externalRefs', []) 
             if ref.get('referenceType') == 'purl'),
            None
        )
    })
```

### Limitations vs GraphQL

| Feature | SBOM (REST API) | GraphQL API |
|---------|----------------|-------------|
| Package names | ✅ Yes | ✅ Yes |
| Version requirements | ✅ Yes | ✅ Yes |
| Package URLs (purls) | ✅ Yes | ❌ No |
| **GitHub repo URLs** | ❌ **No** | ✅ **Yes** |
| **Repository metadata** | ❌ **No** | ✅ **Yes (stars, forks, description)** |
| License info | ✅ Yes | ❌ No |
| SPDX compliance | ✅ Yes | ❌ No |
| Single request | ✅ Yes | ⚠️ Pagination needed |
| Discover GitHub deps | ❌ **No** | ✅ **Yes** |

**For Open Pulse Crawler:** Use **GraphQL API** to discover GitHub repositories through dependency links.
