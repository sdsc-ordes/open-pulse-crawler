# Building instructions.

This is a crawler based on a breadth-first search strategy over Github. It's goal is to find links to:

1. Users
2. Organizations
3. Repositories 

from a list of initial nodes that can be any of these classes. It connects users to organizations and repositories, Organizations to users and repositories, and Repositories to Organizations with the following classes:

Relationship mapping:
    - For a user or org “authored” repo: if the repo exists and its owner equals the actor,
        use "owner of"; otherwise use "contributor of".
    - For a forked repo (from a user or org): use "fork of".
    - For an org’s membership: record an edge from the member to the org with "member of".
    - For repo details:
        • For each contributor, add an edge (actor -> repo) as "contributor of".
        • For the repo owner, add an edge (actor -> repo) as "owner of".
        • If the repo is a fork, add an edge from the parent repo to the fork with "parent of".


The input can be provided as a txt with the inital seed:

```seed.txt
caviri
sdsc-ordes/gimie
```

or appending these to the CLI command. command --flag xxxx caviri sdsc-ordes/gimie

Please make the seed compatible with the full http link or just the github user or user/repo or org/repo

The output should these:

1. JSON

JSON containing all information per source node?

``` json
{}
```


2. CSV

```csv
source,target,property,source_type,target_type
06kellyjac,gabyx/home-manager,contributor of,user,repo
06needhamt,gabyx/json,contributor of,user,repo
0f-0b,gabyx/wezterm,contributor of,user,repo
0x0D05,gabyx/wezterm,contributor of,user,repo
0x0L,gabyx/notebook,contributor of,user,repo
0x17de,gabyx/kitty,contributor of,user,repo

```

## Properties

- It make use of .env `GITHUB_TOKEN`
- Allow to have several comma separated tokens in `GITHUB_TOKEN` and split the requests needed by round. Keep some statistics of use and check if possible how many requests per minute are available, we want to use the API gentlely.
- It makes use of PyGithub for the calls https://github.com/PyGithub/PyGithub
- Custom number of rounds, each round meaning one expansion degree from the previous nodes. 
- Smart way to cache and avoid performing the same call to the GitHub API.
- Its able to store the status in one cache file and restart from that file. 
- Include a system to be gentle with the API of maybe PyGithub already has it. Retry failed calls. 
- It can be used as a CLI with Typer or as a python package
- Show some useful logs with timestamps. It's really important to have a feeling of the progression by round and provide statistics on time. 
- As optional make it possible to render a visualization of the graph color code by type of node. Please point at the initial seeds being squares or another graphical. Store these as a png in a good resolution. 


- Uses Pydantic classes

Take these as inspiration

```python
from typing import List, Dict, Set, Union, Optional
from pydantic import BaseModel, Field
from enum import Enum

# --------------- #
# Pydantic Models #
# --------------- #

class GitHubItemType(str, Enum):
    USER = "User"
    ORGANIZATION = "Organization"
    REPOSITORY = "Repository"
    BOT = "Bot"
    UNKNOWN = "Unknown"


class UserModel(BaseModel):
    login: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.USER

    # Repos the user owns
    authored_repositories: List[str] = Field(default_factory=list)
    # Repos the user has forked
    forked_repositories: List[str] = Field(default_factory=list)

    # (Optionally, you might still want a single "all_repositories" if you prefer,
    # but for clarity, we'll keep them separate.)


class OrgModel(BaseModel):
    login: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.ORGANIZATION
    members: List[str] = Field(default_factory=list)

    # Org-owned repos that are original
    authored_repositories: List[str] = Field(default_factory=list)
    # Org-owned repos that are forks
    forked_repositories: List[str] = Field(default_factory=list)


class RepoModel(BaseModel):
    full_name: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.REPOSITORY
    contributors: List[str] = Field(default_factory=list)
    owner: str = ""

    # New fields to identify a fork
    is_fork: bool = False
    # If it's a fork, store the original parent's full_name, if provided by API
    forked_from: Optional[str] = None


class GraphData(BaseModel):
    """Holds references to users, orgs, and repos discovered."""
    users: Dict[str, UserModel] = Field(default_factory=dict)
    orgs: Dict[str, OrgModel] = Field(default_factory=dict)
    repos: Dict[str, RepoModel] = Field(default_factory=dict)

    def add_user(self, user: UserModel):
        self.users[user.login] = user

    def add_org(self, org: OrgModel):
        self.orgs[org.login] = org

    def add_repo(self, repo: RepoModel):
        self.repos[repo.full_name] = repo

    def has_user(self, login: str) -> bool:
        return login in self.users

    def has_org(self, login: str) -> bool:
        return login in self.orgs

    def has_repo(self, full_name: str) -> bool:
        return full_name in self.repos
```