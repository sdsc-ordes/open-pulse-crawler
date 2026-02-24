# Plan: Dependency and Dependent Graph Expansion

## Goal
Expand the crawler to optionally discover and traverse repository relationships based on:
1.  **Dependencies**: Repositories that the current repository depends on (via SBOM).
2.  **Dependents**: Repositories that depend on the current repository (via GitHub "Used by" graph).

## 1. CLI Interface Changes
Update `src/open_pulse_crawler/cli.py` to add new flags to the `crawl` command:
- `--crawl-dependencies / --no-crawl-dependencies`: Enable/disable crawling of dependencies (default: False).
- `--crawl-dependents / --no-crawl-dependents`: Enable/disable crawling of dependents (default: False).
- `--min-stars`: Filter dependents/dependencies by star count (useful to reduce noise).

## 2. Data Model Updates
`RepoModel` in `src/open_pulse_crawler/models.py` has already been updated to include:
- `dependencies`: List[str] (full names of repositories this repo depends on)
- `dependents`: List[str] (full names of repositories that depend on this repo)

## 3. Implementation Details

### A. Dependency Crawling (Downstream)
**Source**: GitHub API SBOM endpoint (`GET /repos/{owner}/{repo}/dependency-graph/sbom`)

**Logic**:
1.  Fetch SBOM for the repository.
2.  Parse `packages` list.
3.  Extract Package URLs (PURLs).
4.  **Resolution Strategy**:
    - **PyPI**: Query `https://pypi.org/pypi/{package}/json` to find the Source Code / GitHub URL.
    - **NPM/Others**: Future extension. For now, log warning or skip.
5.  Normalize resolved URLs to `owner/repo` format.
6.  Add to `RepoModel.dependencies`.
7.  Add to BFS queue if not visited.

### B. Dependent Crawling (Upstream)
**Source**: `github-dependents-info` library (scrapes GitHub "Used by" page).

**Logic**:
1.  Initialize `GithubDependentsInfo` with the repository.
2.  Call `collect()`.
3.  Extract `public_dependents` from the result.
4.  Normalize to `owner/repo`.
5.  Add to `RepoModel.dependents`.
6.  Add to BFS queue if not visited.

### C. Crawler Integration
Modify `GitHubCrawler._process_repository` in `src/open_pulse_crawler/crawler.py`:
- Check flags `crawl_dependencies` and `crawl_dependents`.
- Execute respective fetching logic.
- Add discovered nodes to `queue` with `item_type='repo'`.
- Ensure thread safety when adding to queue/graph.

## 4. Visualization
Update `src/open_pulse_crawler/visualization.py`:
- Add new edge types/colors for:
    - `depends_on` (Repo A -> Repo B)
    - `depended_on_by` (Repo B -> Repo A)
- Consider using dashed lines or distinct colors (e.g., Orange for dependencies, Purple for dependents).

## 5. Testing Plan
1.  **Unit Tests**:
    - Test PURL resolution logic (mock PyPI response).
    - Test SBOM parsing.
2.  **Integration Test**:
    - Run against `DeepLabCut/DeepLabCut` with `--crawl-dependencies` and verify `networkx` or `pandas` repos are found.
    - Run with `--crawl-dependents` and verify known dependents are found.

## 6. Dependencies
- `github-dependents-info`: Already installed.
- `requests`: Already used.
- `packaging`: For version parsing if needed (standard).

## Notes
- **Rate Limiting**: SBOM API uses GitHub rate limits. PyPI has its own (generous) limits. `github-dependents-info` scrapes and might be slow/rate-limited by GitHub HTML access.
- **Performance**: Dependent crawling can be slow. Should probably limit the number of dependents fetched (e.g., top 10 by stars).
