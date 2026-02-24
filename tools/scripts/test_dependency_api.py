#!/usr/bin/env python3
"""
Test script to fetch dependency graph data from GitHub API.

Usage:
    python tools/scripts/test_dependency_api.py DeepLabCut/DeepLabCut
"""

import os
import sys
import json
import requests
from typing import Dict, List, Any


def fetch_dependencies_graphql(owner: str, repo_name: str, token: str) -> Dict[str, Any]:
    """
    Fetch dependency graph data using GitHub GraphQL API.
    
    This retrieves DEPENDENCIES (what this repo depends on), not DEPENDENTS
    (repos that depend on this one). Dependents are not available via API.
    
    Args:
        owner: Repository owner (user or org)
        repo_name: Repository name
        token: GitHub personal access token
        
    Returns:
        Dictionary containing dependency graph data
    """
    query = """
    query($owner: String!, $repo: String!) {
      repository(owner: $owner, name: $repo) {
        name
        nameWithOwner
        url
        dependencyGraphManifests(first: 100) {
          pageInfo {
            hasNextPage
            endCursor
          }
          totalCount
          nodes {
            filename
            parseable
            blobPath
            dependenciesCount
            dependencies(first: 100) {
              pageInfo {
                hasNextPage
                endCursor
              }
              totalCount
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
                  isArchived
                  isPrivate
                  primaryLanguage {
                    name
                  }
                  licenseInfo {
                    name
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    
    response = requests.post(
        'https://api.github.com/graphql',
        headers={
            'Authorization': f'bearer {token}',
            'Content-Type': 'application/json'
        },
        json={
            'query': query,
            'variables': {
                'owner': owner,
                'repo': repo_name
            }
        }
    )
    
    response.raise_for_status()
    return response.json()


def fetch_sbom(owner: str, repo_name: str, token: str) -> Dict[str, Any]:
    """
    Fetch SBOM (Software Bill of Materials) using REST API.
    
    Args:
        owner: Repository owner (user or org)
        repo_name: Repository name
        token: GitHub personal access token
        
    Returns:
        Dictionary containing SBOM data in SPDX format
    """
    url = f"https://api.github.com/repos/{owner}/{repo_name}/dependency-graph/sbom"
    response = requests.get(
        url,
        headers={
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github+json'
        }
    )
    
    response.raise_for_status()
    return response.json()


def print_dependency_summary(data: Dict[str, Any]) -> None:
    """Print a summary of dependency data from GraphQL response."""
    if 'errors' in data:
        print("❌ GraphQL Errors:")
        for error in data['errors']:
            print(f"  - {error.get('message', 'Unknown error')}")
        return
    
    repo = data['data']['repository']
    print(f"\n{'='*70}")
    print(f"Repository: {repo['nameWithOwner']}")
    print(f"URL: {repo['url']}")
    print(f"{'='*70}")
    
    print("\n⚠️  IMPORTANT: This shows DEPENDENCIES (what this repo depends on)")
    print("   DEPENDENTS (who depends on this repo) are NOT available via API")
    print("   Example: Web UI shows 142 dependents, but API cannot access them")
    
    manifests = repo['dependencyGraphManifests']['nodes']
    total_count = repo['dependencyGraphManifests'].get('totalCount', len(manifests))
    has_more_manifests = repo['dependencyGraphManifests']['pageInfo']['hasNextPage']
    
    print(f"\n📄 Found {len(manifests)} dependency manifests (total: {total_count}):")
    if has_more_manifests:
        print("   ⚠️  More manifests available - pagination needed!")
    
    total_deps = 0
    github_deps = 0
    
    for manifest in manifests:
        deps = manifest['dependencies']['nodes']
        total_dep_count = manifest['dependencies'].get('totalCount', len(deps))
        has_more_deps = manifest['dependencies']['pageInfo']['hasNextPage']
        total_deps += len(deps)
        
        print(f"\n  📋 {manifest['filename']}")
        print(f"     Parseable: {manifest['parseable']}")
        print(f"     Path: {manifest['blobPath']}")
        print(f"     Dependencies: {len(deps)} retrieved / {total_dep_count} total")
        
        if has_more_deps:
            print(f"     ⚠️  More dependencies available - pagination needed!")
        
        if deps:
            print(f"     Sample dependencies:")
            for dep in deps[:5]:  # Show first 5
                repo_info = ""
                if dep.get('repository'):
                    github_deps += 1
                    repo = dep['repository']
                    lang = repo.get('primaryLanguage', {})
                    lang_name = lang.get('name', 'Unknown') if lang else 'Unknown'
                    repo_info = f" → 🔗 {repo['nameWithOwner']} ({lang_name})"
                print(f"       - {dep['packageName']} ({dep['packageManager']}){repo_info}")
                if dep.get('requirements'):
                    print(f"         Requirements: {dep['requirements']}")
            
            if len(deps) > 5:
                print(f"       ... and {len(deps) - 5} more")
    
    print(f"\n{'='*70}")
    print(f"📊 Summary:")
    print(f"   Total dependencies: {total_deps}")
    print(f"   GitHub-hosted dependencies: {github_deps}")
    print(f"   External packages: {total_deps - github_deps}")
    print(f"{'='*70}\n")


def print_sbom_summary(data: Dict[str, Any]) -> None:
    """Print a summary of SBOM data."""
    sbom = data['sbom']
    packages = sbom.get('packages', [])
    
    print(f"\n{'='*70}")
    print(f"SBOM (Software Bill of Materials)")
    print(f"SPDX Version: {sbom.get('spdxVersion', 'N/A')}")
    print(f"{'='*70}")
    print(f"\n📦 Total packages: {len(packages)}")
    
    if packages:
        print(f"\n📋 Sample packages:")
        for pkg in packages[:10]:  # Show first 10
            print(f"  - {pkg.get('name', 'Unknown')} v{pkg.get('versionInfo', 'N/A')}")
            if pkg.get('externalRefs'):
                for ref in pkg['externalRefs'][:1]:  # Show first external ref
                    print(f"    Type: {ref.get('referenceType', 'N/A')}")
                    print(f"    Locator: {ref.get('referenceLocator', 'N/A')}")
        
        if len(packages) > 10:
            print(f"  ... and {len(packages) - 10} more")
    
    print(f"\n{'='*70}\n")


def extract_github_dependencies(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Extract only GitHub-hosted dependencies from GraphQL response.
    
    Returns:
        List of dicts with keys: package_name, repo_name, url, stars, forks
    """
    github_deps = []
    
    if 'errors' in data or 'data' not in data:
        return github_deps
    
    manifests = data['data']['repository']['dependencyGraphManifests']['nodes']
    
    for manifest in manifests:
        for dep in manifest['dependencies']['nodes']:
            if dep.get('repository'):
                repo = dep['repository']
                github_deps.append({
                    'package_name': dep['packageName'],
                    'package_manager': dep['packageManager'],
                    'requirements': dep.get('requirements', ''),
                    'repo_name': repo['nameWithOwner'],
                    'url': repo['url'],
                    'description': repo.get('description', ''),
                    'stars': repo.get('stargazerCount', 0),
                    'forks': repo.get('forkCount', 0),
                    'manifest': manifest['filename']
                })
    
    return github_deps


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python test_dependency_api.py OWNER/REPO")
        print("Example: python test_dependency_api.py DeepLabCut/DeepLabCut")
        sys.exit(1)
    
    # Parse repo argument
    repo_full = sys.argv[1]
    if '/' not in repo_full:
        print("❌ Error: Repository must be in format OWNER/REPO")
        sys.exit(1)
    
    owner, repo_name = repo_full.split('/', 1)
    
    # Get token
    tokens = os.getenv('GITHUB_TOKEN', '').split(',')
    token = tokens[0] if tokens and tokens[0] else None
    
    if not token:
        print("❌ Error: GITHUB_TOKEN environment variable not set")
        sys.exit(1)
    
    print(f"\n🔍 Fetching dependency data for {owner}/{repo_name}...")
    
    # Method 1: GraphQL (Recommended)
    print("\n" + "="*70)
    print("METHOD 1: GraphQL API (Recommended)")
    print("="*70)
    try:
        graphql_data = fetch_dependencies_graphql(owner, repo_name, token)
        print_dependency_summary(graphql_data)
        
        # Extract GitHub dependencies
        github_deps = extract_github_dependencies(graphql_data)
        if github_deps:
            print(f"\n🔗 GitHub-hosted dependencies ({len(github_deps)}):")
            for dep in github_deps:
                print(f"  - {dep['repo_name']} (⭐ {dep['stars']}, 🍴 {dep['forks']})")
                print(f"    Package: {dep['package_name']} ({dep['package_manager']})")
                print(f"    From: {dep['manifest']}")
        
        # Save to file
        output_file = f"dependency_graph_{owner}_{repo_name}.json"
        with open(output_file, 'w') as f:
            json.dump(graphql_data, f, indent=2)
        print(f"\n💾 Full data saved to: {output_file}")
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP Error: {e}")
        print(f"   Response: {e.response.text}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # Method 2: SBOM REST API
    print("\n" + "="*70)
    print("METHOD 2: SBOM REST API")
    print("="*70)
    try:
        sbom_data = fetch_sbom(owner, repo_name, token)
        print_sbom_summary(sbom_data)
        
        # Save to file
        output_file = f"sbom_{owner}_{repo_name}.json"
        with open(output_file, 'w') as f:
            json.dump(sbom_data, f, indent=2)
        print(f"💾 SBOM data saved to: {output_file}")
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP Error: {e}")
        print(f"   Response: {e.response.text}")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == '__main__':
    main()
