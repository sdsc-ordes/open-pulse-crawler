# Dependencies vs Dependents - Important Distinction

## The Confusion

When looking at GitHub's dependency graph page (e.g., https://github.com/DeepLabCut/DeepLabCut/network/dependents), you see **142 repositories**. However, the API cannot access this data!

## Key Terminology

### ⬇️ Dependencies (What You Depend On)
**Definition:** Packages and repositories that THIS repository uses/requires.

**Example for DeepLabCut:**
- `pytorch/pytorch` - DeepLabCut uses PyTorch
- `numpy/numpy` - DeepLabCut uses NumPy
- `actions/checkout` - DeepLabCut's workflows use this GitHub Action

**API Access:** ✅ **YES - Available via GraphQL API**

**Count:** 60 dependencies total (57 are GitHub repos)

### ⬆️ Dependents (Who Depends On You)
**Definition:** Repositories that use/require THIS repository.

**Example for DeepLabCut:**
- 142 repositories use DeepLabCut as a dependency
- These are shown on the web UI at `/network/dependents`
- Could include forks, projects that import DeepLabCut, etc.

**API Access:** ❌ **NO - NOT available via any API**

**Count:** 142 dependents (web UI only)

## Visual Diagram

```
                    DEPENDENTS (142 repos)
                           ↓
                    [Repo A depends on DeepLabCut]
                    [Repo B depends on DeepLabCut]
                    [Repo C depends on DeepLabCut]
                           ↓
                    ┌──────────────┐
                    │  DeepLabCut  │ ← This is what we're analyzing
                    └──────────────┘
                           ↓
                    DEPENDENCIES (60 packages)
                           ↓
                    [DeepLabCut depends on PyTorch]
                    [DeepLabCut depends on NumPy]
                    [DeepLabCut depends on Pandas]
```

## What the API Provides

### GraphQL API: `dependencyGraphManifests`
**Returns:** ⬇️ Dependencies (what the repo uses)

```graphql
{
  repository(owner: "DeepLabCut", name: "DeepLabCut") {
    dependencyGraphManifests {
      nodes {
        dependencies {
          nodes {
            packageName        # e.g., "torch"
            repository {
              nameWithOwner    # e.g., "pytorch/pytorch"
            }
          }
        }
      }
    }
  }
}
```

**Result:** 60 dependencies, including:
- 27 Python packages (requirements.txt)
- 25 Python packages (setup.py)
- 7 GitHub Actions
- Most have GitHub repository URLs

### What's NOT Available
**Query:** ⬆️ Dependents (who uses this repo)

There is **NO GraphQL field** or **REST endpoint** to get:
- List of repositories that depend on DeepLabCut
- Number of dependents
- Who is using your package

**Only available via:** Web scraping (not recommended, violates ToS)

## Why This Matters for Open Pulse Crawler

### ✅ What We CAN Do

1. **Forward Dependency Discovery**
   - Start with DeepLabCut
   - Discover it uses PyTorch
   - Discover PyTorch uses other repos
   - Build dependency chains

2. **GitHub Actions Mapping**
   - See which GitHub Actions a repo uses
   - Track common actions across projects
   - Build action dependency networks

3. **Technology Stack Analysis**
   - Identify Python/Node/etc dependencies
   - See what libraries are popular
   - Track version requirements

### ❌ What We CANNOT Do

1. **Reverse Dependency Discovery**
   - Cannot find who uses DeepLabCut
   - Cannot measure impact (how many projects would break)
   - Cannot build "popularity" metrics based on usage

2. **Influence Analysis**
   - Cannot see which projects your package affects
   - Cannot track downstream users

## Practical Example

### Scenario: Analyzing DeepLabCut

**What we get from API:**
```
DeepLabCut depends on:
  → pytorch/pytorch (94,999 ⭐)
  → numpy/numpy (30,805 ⭐)
  → pandas-dev/pandas (47,088 ⭐)
  → matplotlib/matplotlib (21,968 ⭐)
  ... 56 more GitHub repos
```

**What we see on web but can't get from API:**
```
142 repositories depend on DeepLabCut:
  → [Repository names not accessible]
  → [No API to retrieve this list]
```

## Enhanced GraphQL Query

Our improved query now includes:

```graphql
query {
  repository {
    dependencyGraphManifests(first: 100) {  # More manifests
      totalCount                            # Total available
      pageInfo {
        hasNextPage                         # Pagination support
        endCursor
      }
      nodes {
        dependenciesCount                   # Total deps per manifest
        dependencies(first: 100) {          # More dependencies
          totalCount                        # Total available
          pageInfo {
            hasNextPage
            endCursor
          }
          nodes {
            repository {
              primaryLanguage { name }      # NEW: Language
              licenseInfo { name }          # NEW: License
              isArchived                    # NEW: Status
              isPrivate                     # NEW: Visibility
            }
          }
        }
      }
    }
  }
}
```

## Summary Table

| Feature | Dependencies | Dependents |
|---------|-------------|------------|
| **Direction** | ⬇️ What this repo uses | ⬆️ Who uses this repo |
| **DeepLabCut Example** | 60 packages | 142 repositories |
| **API Access** | ✅ Yes (GraphQL) | ❌ No |
| **GitHub URLs** | ✅ Yes (57 repos) | ❌ N/A |
| **Metadata** | ✅ Stars, forks, language | ❌ N/A |
| **Use Case** | Build dependency chains | Impact analysis |
| **Open Pulse Crawler** | ✅ Can implement | ❌ Cannot implement |

## Conclusion

**For Open Pulse Crawler:**
- Focus on **dependency discovery** (what repos depend on)
- Use GraphQL API to build forward dependency chains
- Accept that reverse dependency analysis (dependents) is not possible via API
- The 142 repositories you see on the web page are **not accessible programmatically**

**Enhanced Features We Added:**
- Increased from 10 to 100 manifests per query
- Increased from 20 to 100 dependencies per manifest
- Added totalCount to know if pagination is needed
- Added primaryLanguage, licenseInfo, isArchived, isPrivate
- Better error handling and pagination detection

This gives us the **maximum possible dependency data** from the GitHub API! 🎯
