# GitHub Pulse & Activity Metrics - Complete Guide

**Date:** November 12, 2025  
**Investigation:** What activity metrics can we track for Open Pulse Crawler?

## Summary

GitHub provides **extensive pulse/activity data** through its API! You can track:
- ✅ Commit frequency and activity patterns
- ✅ Fork evolution over time
- ✅ PR and issue activity
- ✅ Contributor statistics
- ✅ Release history
- ✅ Code churn (additions/deletions)
- ⚠️ Traffic stats (requires repo push access)

## Available Metrics

### 1. ✅ Basic Repository Metrics (Always Available)

**API:** `GET /repos/{owner}/{repo}`  
**PyGithub:** `repo = g.get_repo("owner/repo")`

| Metric | Field | Example (DeepLabCut) | Tracks |
|--------|-------|---------------------|--------|
| **Stars** | `stargazers_count` | 5,338 | Popularity |
| **Forks** | `forks_count` | 1,767 | Derivatives/usage |
| **Watchers** | `watchers_count` | 5,338 | Interest |
| **Open Issues+PRs** | `open_issues_count` | 36 | Active discussions |
| **Repository Size** | `size` | 202,574 KB | Growth |
| **Created Date** | `created_at` | 2018-03-26 | Age |
| **Last Updated** | `updated_at` | 2025-11-12 | Activity |
| **Last Pushed** | `pushed_at` | 2025-11-08 | Development |
| **Contributors** | `get_contributors().totalCount` | 128 | Community size |
| **Releases** | `get_releases().totalCount` | 59 | Maturity |
| **Branches** | `get_branches().totalCount` | 75 | Development style |
| **Tags** | `get_tags().totalCount` | 66 | Version history |

**Usage:**
```python
from github import Github

g = Github("token")
repo = g.get_repo("DeepLabCut/DeepLabCut")

print(f"Stars: {repo.stargazers_count:,}")
print(f"Forks: {repo.forks_count:,}")
print(f"Last pushed: {repo.pushed_at}")
print(f"Contributors: {repo.get_contributors().totalCount}")
```

### 2. ✅ Commit Activity (Last 52 Weeks)

**API:** `GET /repos/{owner}/{repo}/stats/commit_activity`  
**Returns:** Weekly commit counts for the past year

```python
import requests

url = f"https://api.github.com/repos/{owner}/{repo}/stats/commit_activity"
response = requests.get(url, headers={'Authorization': f'token {token}'})

if response.status_code == 200:
    data = response.json()
    for week in data:
        week_date = datetime.fromtimestamp(week['week'])
        total_commits = week['total']
        commits_per_day = week['days']  # [Sun, Mon, Tue, ...]
        print(f"{week_date}: {total_commits} commits")
```

**Data Structure:**
```json
[
  {
    "days": [0, 3, 5, 2, 1, 4, 3],
    "total": 18,
    "week": 1604188800
  }
]
```

**Use Cases:**
- Track development velocity
- Identify active/inactive periods
- Compare projects' activity levels
- Detect abandoned repositories

**Note:** ⚠️ First request may return `202 Accepted` - GitHub is computing stats. Retry after a moment.

### 3. ✅ Contributors Statistics

**API:** `GET /repos/{owner}/{repo}/stats/contributors`  
**Returns:** Per-contributor commit statistics

```python
url = f"https://api.github.com/repos/{owner}/{repo}/stats/contributors"
response = requests.get(url, headers=headers)

if response.status_code == 200:
    contributors = response.json()
    for contrib in contributors:
        author = contrib['author']['login']
        total = contrib['total']
        weeks = contrib['weeks']  # Weekly breakdown
        print(f"@{author}: {total} commits")
```

**Data per Contributor:**
- Total commits
- Weekly commit history
- Additions/deletions per week
- Complete activity timeline

**Use Cases:**
- Identify key contributors
- Track contributor engagement over time
- Detect single-maintainer projects
- Analyze contribution patterns

### 4. ✅ Code Frequency (Additions/Deletions)

**API:** `GET /repos/{owner}/{repo}/stats/code_frequency`  
**Returns:** Weekly code churn (additions and deletions)

```python
url = f"https://api.github.com/repos/{owner}/{repo}/stats/code_frequency"
response = requests.get(url, headers=headers)

if response.status_code == 200:
    data = response.json()
    for week in data:
        timestamp = week[0]
        additions = week[1]
        deletions = abs(week[2])  # Negative value
        week_date = datetime.fromtimestamp(timestamp)
        print(f"{week_date}: +{additions} / -{deletions}")
```

**Use Cases:**
- Track repository growth
- Identify refactoring periods (high deletions)
- Measure development intensity
- Detect major rewrites

### 5. ✅ Participation (Owner vs Community)

**API:** `GET /repos/{owner}/{repo}/stats/participation`  
**Returns:** Owner commits vs all commits (last 52 weeks)

```python
url = f"https://api.github.com/repos/{owner}/{repo}/stats/participation"
response = requests.get(url, headers=headers)

if response.status_code == 200:
    data = response.json()
    all_commits = data['all']      # [week1, week2, ...]
    owner_commits = data['owner']  # [week1, week2, ...]
    
    total_all = sum(all_commits)
    total_owner = sum(owner_commits)
    community_ratio = (total_all - total_owner) / total_all
    print(f"Community contribution: {community_ratio:.1%}")
```

**Example (DeepLabCut):**
- Total commits: 193
- Owner commits: 0
- Community commits: 193 (100%)

**Use Cases:**
- Assess project health (community vs single-maintainer)
- Track ownership transfer
- Identify company-driven vs community-driven projects

### 6. ✅ Punch Card (Commit Time Distribution)

**API:** `GET /repos/{owner}/{repo}/stats/punch_card`  
**Returns:** Commits by day of week and hour

```python
url = f"https://api.github.com/repos/{owner}/{repo}/stats/punch_card"
response = requests.get(url, headers=headers)

if response.status_code == 200:
    data = response.json()
    # Each entry: [day, hour, commits]
    # day: 0=Sunday, 1=Monday, ..., 6=Saturday
    # hour: 0-23
    
    peak = max(data, key=lambda x: x[2])
    days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    print(f"Peak: {days[peak[0]]} at {peak[1]}:00 with {peak[2]} commits")
```

**Example (DeepLabCut):**
- Peak: Friday at 18:00 with 52 commits
- 168 data points (7 days × 24 hours)

**Use Cases:**
- Identify global team distribution
- Detect work patterns (business hours vs personal projects)
- Timezone analysis

### 7. ✅ Issues & Pull Requests

**API:** `GET /repos/{owner}/{repo}/issues`  
**PyGithub:** `repo.get_issues(state='all', since=date)`

```python
from datetime import datetime, timedelta

# Issues from last 30 days
since = datetime.now() - timedelta(days=30)
issues = repo.get_issues(state='all', since=since)

for issue in issues:
    if issue.pull_request:
        print(f"PR #{issue.number}: {issue.title}")
    else:
        print(f"Issue #{issue.number}: {issue.title}")
    
    print(f"  State: {issue.state}")
    print(f"  Created: {issue.created_at}")
    print(f"  Comments: {issue.comments}")
```

**Available Data:**
- Total issues/PRs count
- Open vs closed
- Creation/close dates
- Comment counts
- Labels, assignees, milestones
- Reaction counts (👍, ❤️, etc.)

**Use Cases:**
- Track issue resolution time
- Measure responsiveness
- Identify popular issues (by comments/reactions)
- Monitor PR merge rate

### 8. ✅ Releases & Tags

**API:** `GET /repos/{owner}/{repo}/releases`  
**PyGithub:** `repo.get_releases()`

```python
releases = repo.get_releases()

for release in releases:
    print(f"{release.tag_name}")
    print(f"  Published: {release.published_at}")
    print(f"  Assets: {len(release.get_assets())}")
    print(f"  Downloads: {sum(a.download_count for a in release.get_assets())}")
```

**Example (DeepLabCut):**
- Total releases: 59
- Latest: v3.0.0rc10 (2025-07-01)

**Use Cases:**
- Track release frequency
- Monitor version progression
- Analyze download counts
- Identify long-term support versions

### 9. ✅ Forks (with Evolution)

**API:** `GET /repos/{owner}/{repo}/forks?sort=newest`  
**PyGithub:** `repo.get_forks()`

```python
forks = repo.get_forks()
print(f"Total forks: {forks.totalCount}")

# Get recent forks
recent = sorted(
    list(forks[:100]), 
    key=lambda x: x.created_at, 
    reverse=True
)[:10]

for fork in recent:
    print(f"{fork.full_name} - created {fork.created_at}")
    print(f"  Stars: {fork.stargazers_count}")
    print(f"  Ahead/behind: {fork.get_commits().totalCount - repo.get_commits().totalCount}")
```

**Fork Evolution Tracking:**
```python
# Sample forks over time
fork_dates = [f.created_at for f in forks]
# Group by month/year to see growth
```

**Use Cases:**
- Track fork growth rate
- Identify active forks (more stars than original)
- Monitor derivatives
- Assess project adoption

### 10. ✅ Stargazers (with Timestamps)

**API:** `GET /repos/{owner}/{repo}/stargazers` (with `application/vnd.github.star+json`)  
**PyGithub:** `repo.get_stargazers_with_dates()`

```python
stargazers = repo.get_stargazers_with_dates()

for star in stargazers:
    print(f"@{star.user.login} starred at {star.starred_at}")
```

**Star Evolution Tracking:**
```python
star_dates = [s.starred_at for s in repo.get_stargazers_with_dates()]
# Group by month to create growth chart
```

**Use Cases:**
- Track popularity growth
- Identify viral moments
- Correlate with releases/events
- Predict trending repositories

### 11. ✅ Commits (Full History)

**API:** `GET /repos/{owner}/{repo}/commits`  
**PyGithub:** `repo.get_commits(since=date, until=date)`

```python
from datetime import datetime, timedelta

# Last 30 days
since = datetime.now() - timedelta(days=30)
commits = repo.get_commits(since=since)

for commit in commits:
    sha = commit.sha[:7]
    message = commit.commit.message.split('\n')[0]
    author = commit.commit.author.name
    date = commit.commit.author.date
    
    print(f"{sha}: {message}")
    print(f"  By {author} on {date}")
    
    # Stats
    print(f"  Files changed: {len(commit.files)}")
    print(f"  Additions: {commit.stats.additions}")
    print(f"  Deletions: {commit.stats.deletions}")
```

**Commit Frequency Analysis:**
```python
# Count commits per day/week/month
commit_dates = [c.commit.author.date for c in commits]
# Aggregate to measure velocity
```

**Use Cases:**
- Calculate commit frequency
- Track development velocity
- Identify commit patterns
- Measure code quality (commit message quality, file changes per commit)

### 12. ❌ Traffic Stats (Requires Push Access)

**API:** `GET /repos/{owner}/{repo}/traffic/views`  
**API:** `GET /repos/{owner}/{repo}/traffic/clones`  
**Access:** Requires **push** permission to repository

```python
# Only works if you have push access
try:
    views = repo.get_views_traffic()
    print(f"Views (14 days): {views['count']} total")
    print(f"Unique visitors: {views['uniques']}")
    
    clones = repo.get_clones_traffic()
    print(f"Clones (14 days): {clones['count']} total")
except GithubException:
    print("❌ Requires push access")
```

**Note:** ⚠️ Only available for repositories you own/maintain.

## GraphQL API - Enhanced Queries

For more efficient batch queries, use GraphQL:

```graphql
query RepositoryPulse($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    # Basic metrics
    stargazerCount
    forkCount
    watchers { totalCount }
    
    # Activity
    pushedAt
    updatedAt
    createdAt
    
    # Issues & PRs
    issues(states: OPEN) { totalCount }
    pullRequests(states: OPEN) { totalCount }
    
    # Releases
    releases(first: 10) {
      nodes {
        tagName
        publishedAt
        releaseAssets(first: 5) {
          nodes {
            name
            downloadCount
          }
        }
      }
    }
    
    # Recent commits
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 100) {
            totalCount
            nodes {
              committedDate
              author {
                name
                user { login }
              }
              additions
              deletions
            }
          }
        }
      }
    }
    
    # Contributors
    mentionableUsers(first: 100) {
      totalCount
    }
  }
}
```

## Time Series Analysis

### Example: Track Repository Growth

```python
from datetime import datetime, timedelta
import json

def collect_pulse_snapshot(repo):
    """Collect all pulse metrics at a point in time."""
    return {
        'timestamp': datetime.now().isoformat(),
        'stars': repo.stargazers_count,
        'forks': repo.forks_count,
        'watchers': repo.watchers_count,
        'open_issues': repo.open_issues_count,
        'contributors': repo.get_contributors().totalCount,
        'size_kb': repo.size,
        'updated_at': repo.updated_at.isoformat(),
        'pushed_at': repo.pushed_at.isoformat(),
    }

# Collect snapshots over time
snapshots = []
for repo_name in repositories:
    repo = g.get_repo(repo_name)
    snapshot = collect_pulse_snapshot(repo)
    snapshots.append(snapshot)

# Save for time series analysis
with open('pulse_snapshots.json', 'w') as f:
    json.dump(snapshots, f, indent=2)
```

### Example: Commit Frequency Over Time

```python
from collections import defaultdict

def analyze_commit_frequency(repo, days=90):
    """Analyze commit frequency over last N days."""
    since = datetime.now() - timedelta(days=days)
    commits = repo.get_commits(since=since)
    
    # Group by date
    commits_per_day = defaultdict(int)
    for commit in commits:
        date = commit.commit.author.date.date()
        commits_per_day[date] += 1
    
    # Calculate stats
    total_commits = sum(commits_per_day.values())
    avg_per_day = total_commits / days
    peak_day = max(commits_per_day.items(), key=lambda x: x[1])
    
    return {
        'total_commits': total_commits,
        'avg_per_day': avg_per_day,
        'peak_day': peak_day[0].isoformat(),
        'peak_commits': peak_day[1],
        'active_days': len(commits_per_day),
        'activity_ratio': len(commits_per_day) / days
    }
```

## Integration with Open Pulse Crawler

### Recommended Pulse Metrics to Collect

```python
class RepoModel(BaseModel):
    # Existing fields...
    
    # Add pulse metrics
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    open_issues: int = 0
    size_kb: int = 0
    contributors_count: int = 0
    releases_count: int = 0
    
    # Timestamps
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    pushed_at: Optional[datetime] = None
    
    # Activity indicators
    is_active: bool = True  # Based on recent commits
    last_commit_days_ago: Optional[int] = None
```

### Activity Score Calculation

```python
def calculate_activity_score(repo) -> float:
    """Calculate repository activity score (0-100)."""
    score = 0
    
    # Recent push (0-30 points)
    days_since_push = (datetime.now() - repo.pushed_at).days
    if days_since_push < 7:
        score += 30
    elif days_since_push < 30:
        score += 20
    elif days_since_push < 90:
        score += 10
    
    # Stars (0-20 points)
    star_score = min(repo.stargazers_count / 1000, 20)
    score += star_score
    
    # Forks (0-15 points)
    fork_score = min(repo.forks_count / 500, 15)
    score += fork_score
    
    # Contributors (0-15 points)
    contrib_count = repo.get_contributors().totalCount
    contrib_score = min(contrib_count / 10, 15)
    score += contrib_score
    
    # Open issues (engagement, 0-10 points)
    issue_score = min(repo.open_issues_count / 50, 10)
    score += issue_score
    
    # Recent releases (0-10 points)
    releases = list(repo.get_releases()[:1])
    if releases:
        days_since_release = (datetime.now() - releases[0].published_at).days
        if days_since_release < 90:
            score += 10
        elif days_since_release < 180:
            score += 5
    
    return min(score, 100)
```

## Rate Limiting Considerations

### Stats Endpoints
- **First request:** May return `202 Accepted` (computing)
- **Subsequent requests:** Return cached data (updated hourly)
- **Rate limit impact:** Minimal (cached responses)

### Regular Endpoints
- **Rate limit:** 5,000 requests/hour per token
- **Recommendation:** Batch requests with GraphQL when possible

### Optimization Strategy
```python
# Use GraphQL to batch multiple metrics in one request
# Use stats endpoints for historical data (cached)
# Cache locally to avoid repeated requests
```

## Summary Table

| Metric | Availability | Requires Auth | Time Range | Update Frequency | Use Case |
|--------|-------------|---------------|------------|------------------|----------|
| Stars/Forks/Watchers | ✅ Always | No | Current | Real-time | Popularity |
| Commit Activity | ✅ API | Yes | 52 weeks | Hourly cache | Velocity |
| Contributors Stats | ✅ API | Yes | All time | Hourly cache | Community |
| Code Frequency | ✅ API | Yes | All time | Hourly cache | Growth |
| Participation | ✅ API | Yes | 52 weeks | Hourly cache | Health |
| Punch Card | ✅ API | Yes | All time | Hourly cache | Patterns |
| Issues/PRs | ✅ API | No | All time | Real-time | Activity |
| Releases | ✅ API | No | All time | Real-time | Maturity |
| Commits | ✅ API | No | All time | Real-time | History |
| Stargazers Timeline | ✅ API | No | All time | Real-time | Growth |
| Traffic Stats | ❌ Push access | Yes (owner) | 14 days | Daily | Visibility |

## Conclusion

**YES! You can track extensive pulse/activity metrics!** ✅

**Available for Open Pulse Crawler:**
1. **Commit frequency** - Weekly/daily commit counts
2. **Fork evolution** - Track fork growth over time
3. **PR/Issue activity** - Open, closed, merged counts
4. **Star growth** - Popularity trends with timestamps
5. **Contributor activity** - Per-contributor statistics
6. **Release frequency** - Version history and timing
7. **Code churn** - Lines added/deleted over time
8. **Activity patterns** - When commits happen (punch card)

**Not available without push access:**
- Traffic/views statistics (last 14 days)
- Clone counts

**Recommendation:** Integrate pulse metrics into `RepoModel` to track repository health and activity over time in Open Pulse Crawler! 🚀
