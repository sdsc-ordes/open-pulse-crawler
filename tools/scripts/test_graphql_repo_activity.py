#!/usr/bin/env python3
"""Quick GraphQL proof-of-concept for issue/PR activity extraction.

Fetches a single repo's most-recent issues + PRs with their authors,
conversation commenters, and PR reviewers in **one** GraphQL request.
Compares the API cost against the REST equivalent (one paginated list
call per resource: ~1 + N + 1 + N + N REST calls for the same data).

Usage:
    GITHUB_TOKEN=... python tools/scripts/test_graphql_repo_activity.py \\
        sdsc-ordes/gimie --issue-max 25 --pr-max 25
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Set

import httpx

GRAPHQL_URL = "https://api.github.com/graphql"

# Single query covering everything --crawl-issues + --crawl-prs currently pulls
# via dozens of REST calls. `comments` on PRs are the conversation comments
# (equivalent to PyGithub's `pr.get_issue_comments()`), not inline review
# comments. The trailing `rateLimit` block reports the point cost of this
# specific query plus the remaining budget for the hour.
QUERY = """
query($owner: String!, $name: String!, $issueMax: Int!, $prMax: Int!) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    nameWithOwner
    issues(
      first: $issueMax,
      orderBy: {field: CREATED_AT, direction: DESC},
      filterBy: {}
    ) {
      totalCount
      nodes {
        number
        author { login }
        comments(first: 100) { nodes { author { login } } }
      }
    }
    pullRequests(
      first: $prMax,
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      totalCount
      nodes {
        number
        author { login }
        comments(first: 100) { nodes { author { login } } }
        reviews(first: 100) { nodes { author { login } } }
      }
    }
  }
}
"""


def run(owner: str, name: str, issue_max: int, pr_max: int, token: str) -> Dict[str, Any]:
    headers = {
        "Authorization": f"bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    payload = {
        "query": QUERY,
        "variables": {
            "owner": owner,
            "name": name,
            "issueMax": issue_max,
            "prMax": pr_max,
        },
    }

    t0 = time.monotonic()
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(GRAPHQL_URL, headers=headers, json=payload)
    elapsed = time.monotonic() - t0
    resp.raise_for_status()
    body = resp.json()
    body["_elapsed_seconds"] = elapsed
    return body


def collect_logins(nodes: List[Dict[str, Any]], key: str = "author") -> List[str]:
    """Extract distinct, order-preserved login strings from a node list."""
    seen: Set[str] = set()
    out: List[str] = []
    for n in nodes or []:
        if n is None:
            continue
        actor = n.get(key)
        if not actor:
            continue
        login = actor.get("login")
        if login and login not in seen:
            seen.add(login)
            out.append(login)
    return out


def summarize(body: Dict[str, Any]) -> None:
    if body.get("errors"):
        print("GraphQL errors:")
        print(json.dumps(body["errors"], indent=2))
        sys.exit(1)

    data = body["data"]
    rl = data["rateLimit"]
    repo = data["repository"]
    if repo is None:
        print("Repository not found (or no access).")
        sys.exit(2)

    issues = repo["issues"]["nodes"] or []
    prs = repo["pullRequests"]["nodes"] or []

    issue_authors = collect_logins(issues)
    pr_authors = collect_logins(prs)

    commenter_set: Set[str] = set()
    commenters: List[str] = []
    for src in (issues, prs):
        for node in src:
            for c in (node.get("comments") or {}).get("nodes") or []:
                actor = c.get("author")
                if actor and actor.get("login") and actor["login"] not in commenter_set:
                    commenter_set.add(actor["login"])
                    commenters.append(actor["login"])

    reviewer_set: Set[str] = set()
    reviewers: List[str] = []
    for pr in prs:
        for r in (pr.get("reviews") or {}).get("nodes") or []:
            actor = r.get("author")
            if actor and actor.get("login") and actor["login"] not in reviewer_set:
                reviewer_set.add(actor["login"])
                reviewers.append(actor["login"])

    print(f"Repo: {repo['nameWithOwner']}")
    print(f"  Scanned: {len(issues)} issues, {len(prs)} PRs "
          f"(repo total: {repo['issues']['totalCount']} issues, "
          f"{repo['pullRequests']['totalCount']} PRs)")
    print()
    print(f"  issue_authors  ({len(issue_authors)}): {issue_authors}")
    print(f"  pr_authors     ({len(pr_authors)}): {pr_authors}")
    print(f"  commenters     ({len(commenters)}): {commenters}")
    print(f"  pr_reviewers   ({len(reviewers)}): {reviewers}")
    print()
    print("GraphQL cost:")
    print(f"  points charged: {rl['cost']}")
    print(f"  remaining:      {rl['remaining']} / hour")
    print(f"  resets at:      {rl['resetAt']}")
    print(f"  wall-clock:     {body['_elapsed_seconds']:.2f}s")
    print()
    # Rough REST equivalent for comparison:
    # 1 list issues + N issue.get_comments + 1 list prs + N pr.get_reviews + N pr.get_issue_comments
    rest_equiv = 1 + len(issues) + 1 + 2 * len(prs)
    print(f"REST equivalent for same coverage: ~{rest_equiv} requests "
          f"(1 list-issues + {len(issues)} issue-comments + 1 list-prs + "
          f"{len(prs)} reviews + {len(prs)} pr-comments)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", help="owner/name, e.g. sdsc-ordes/gimie")
    parser.add_argument("--issue-max", type=int, default=25)
    parser.add_argument("--pr-max", type=int, default=25)
    args = parser.parse_args()

    if "/" not in args.repo:
        parser.error("repo must be in owner/name form")
    owner, name = args.repo.split("/", 1)

    token = os.environ.get("GITHUB_TOKEN", "").strip().split(",")[0].strip()
    if not token:
        parser.error("GITHUB_TOKEN not set")

    body = run(owner, name, args.issue_max, args.pr_max, token)
    summarize(body)


if __name__ == "__main__":
    main()
