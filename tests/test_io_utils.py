"""Tests for I/O utilities."""

import tempfile
from pathlib import Path

from open_pulse_crawler.models import GraphData, UserModel, OrgModel, RepoModel, TeamModel
from open_pulse_crawler.io_utils import (
    parse_seed_file,
    export_to_json,
    export_to_csv,
    export_nodes_csv
)


def test_parse_seed_file():
    """Test parsing seed file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write("user1\n")
        f.write("# Comment\n")
        f.write("org/repo\n")
        f.write("\n")  # Empty line
        f.write("user2\n")
        temp_path = Path(f.name)
    
    try:
        seeds = parse_seed_file(temp_path)
        assert len(seeds) == 3
        assert "user1" in seeds
        assert "org/repo" in seeds
        assert "user2" in seeds
    finally:
        temp_path.unlink()


def test_export_json():
    """Test JSON export."""
    graph = GraphData()
    user = UserModel(login="testuser", id=1)
    graph.add_user(user)
    
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        export_to_json(graph, temp_path)
        assert temp_path.exists()
        
        # Read back and verify
        import json
        with open(temp_path) as f:
            data = json.load(f)
        
        assert "users" in data
        # GraphData is keyed by canonical URL.
        assert "https://github.com/testuser" in data["users"]
    finally:
        temp_path.unlink()


def test_export_csv():
    """Test CSV export."""
    graph = GraphData()
    
    # Create test data
    user = UserModel(login="user1", id=1)
    user.authored_repositories.append("user1/repo1")
    graph.add_user(user)
    
    repo = RepoModel(full_name="user1/repo1", id=2, owner="user1")
    repo.contributors.append("user1")
    graph.add_repo(repo)
    
    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        export_to_csv(graph, temp_path, set())
        assert temp_path.exists()
        
        # Read back and verify
        import csv
        with open(temp_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        assert len(rows) > 0
        assert all('source' in row for row in rows)
        assert all('target' in row for row in rows)
    finally:
        temp_path.unlink()


def test_export_csv_follows_edges():
    """Follow lists should produce `follows` edges only between users in the graph."""
    graph = GraphData()

    alice = UserModel(login="alice", id=1, following=["bob", "ghost"], followers=["bob"])
    bob = UserModel(login="bob", id=2, following=["alice"], followers=["alice"])
    graph.add_user(alice)
    graph.add_user(bob)

    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
        temp_path = Path(f.name)

    try:
        export_to_csv(graph, temp_path, set())

        import csv
        with open(temp_path) as fp:
            rows = list(csv.DictReader(fp))

        follow_edges = {
            (r['source'], r['target'])
            for r in rows
            if r['property'] == 'follows'
        }
        alice_url = "https://github.com/alice"
        bob_url = "https://github.com/bob"
        ghost_url = "https://github.com/ghost"
        assert (alice_url, bob_url) in follow_edges
        assert (bob_url, alice_url) in follow_edges
        # "ghost" is not in the graph, so the edge must be dropped.
        assert (alice_url, ghost_url) not in follow_edges
        assert all(r['source_type'] == 'user' and r['target_type'] == 'user'
                   for r in rows if r['property'] == 'follows')
    finally:
        temp_path.unlink()


def test_export_csv_star_and_watch_edges():
    """Starred and watched lists should produce edges only when repo is in graph."""
    graph = GraphData()
    alice = UserModel(
        login="alice",
        id=1,
        starred_repositories=["org/a", "org/ghost"],
        watched_repositories=["org/a"],
    )
    graph.add_user(alice)
    graph.add_repo(RepoModel(full_name="org/a", id=10, owner="org"))

    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
        temp_path = Path(f.name)
    try:
        export_to_csv(graph, temp_path, set())
        import csv
        with open(temp_path) as fp:
            rows = list(csv.DictReader(fp))

        starred = {(r['source'], r['target']) for r in rows if r['property'] == 'starred'}
        watching = {(r['source'], r['target']) for r in rows if r['property'] == 'watching'}
        alice_url = "https://github.com/alice"
        repo_a_url = "https://github.com/org/a"
        repo_ghost_url = "https://github.com/org/ghost"
        assert (alice_url, repo_a_url) in starred
        assert (alice_url, repo_a_url) in watching
        assert (alice_url, repo_ghost_url) not in starred  # repo not in graph
    finally:
        temp_path.unlink()


def test_export_csv_issue_pr_edges():
    """Issue/PR activity should produce edges only between users in the graph and the repo."""
    graph = GraphData()
    graph.add_user(UserModel(login="alice", id=1))
    graph.add_user(UserModel(login="bob", id=2))

    repo = RepoModel(
        full_name="org/repo",
        id=10,
        owner="org",
        issue_authors=["alice", "ghost"],
        pr_authors=["bob"],
        commenters=["alice", "ghost"],
        pr_reviewers=["alice"],
    )
    graph.add_repo(repo)

    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
        temp_path = Path(f.name)
    try:
        export_to_csv(graph, temp_path, set())
        import csv
        with open(temp_path) as fp:
            rows = list(csv.DictReader(fp))
        triples = {(r['source'], r['target'], r['property']) for r in rows}
        alice_url = "https://github.com/alice"
        bob_url = "https://github.com/bob"
        ghost_url = "https://github.com/ghost"
        repo_url_ = "https://github.com/org/repo"
        assert (alice_url, repo_url_, "issue_author") in triples
        assert (bob_url, repo_url_, "pr_author") in triples
        assert (alice_url, repo_url_, "commented_on") in triples
        assert (alice_url, repo_url_, "pr_reviewer") in triples
        # Users not in the graph are dropped.
        assert (ghost_url, repo_url_, "issue_author") not in triples
        assert (ghost_url, repo_url_, "commented_on") not in triples
    finally:
        temp_path.unlink()


def test_export_csv_team_edges():
    """Team relationships should produce has_team, has_access, and parent_of edges."""
    graph = GraphData()
    graph.add_org(OrgModel(login="acme", id=1, name="Acme"))
    graph.add_user(UserModel(login="alice", id=2))
    graph.add_repo(RepoModel(full_name="acme/widget", id=3, owner="acme"))

    parent_team = TeamModel(
        full_name="acme/eng",
        slug="eng",
        name="Engineering",
        id=100,
        org="acme",
    )
    child_team = TeamModel(
        full_name="acme/core",
        slug="core",
        name="Core",
        id=101,
        org="acme",
        parent="acme/eng",
        members=["alice", "ghost"],
        repositories=["acme/widget", "acme/missing"],
    )
    graph.add_team(parent_team)
    graph.add_team(child_team)

    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
        temp_path = Path(f.name)
    try:
        export_to_csv(graph, temp_path, set())
        import csv
        with open(temp_path) as fp:
            rows = list(csv.DictReader(fp))

        triples = {(r['source'], r['target'], r['property']) for r in rows}
        acme_url = "https://github.com/acme"
        core_url = "https://github.com/orgs/acme/teams/core"
        eng_url = "https://github.com/orgs/acme/teams/eng"
        widget_url = "https://github.com/acme/widget"
        missing_url = "https://github.com/acme/missing"
        assert (acme_url, core_url, "has_team") in triples
        assert (acme_url, eng_url, "has_team") in triples
        # member_of edges are intentionally not emitted (org/team membership
        # is too incomplete a signal — see CHANGELOG).
        assert not any(p == "member_of" for _, _, p in triples)
        assert (core_url, widget_url, "has_access") in triples
        assert (core_url, missing_url, "has_access") not in triples  # repo not in graph
        assert (eng_url, core_url, "parent_of") in triples
    finally:
        temp_path.unlink()


def test_export_nodes_csv_includes_teams():
    """Team nodes should appear in the nodes CSV."""
    graph = GraphData()
    graph.add_team(TeamModel(full_name="acme/core", slug="core", name="Core", id=1, org="acme"))

    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
        temp_path = Path(f.name)
    try:
        export_nodes_csv(graph, temp_path, set())
        import csv
        with open(temp_path) as fp:
            rows = list(csv.DictReader(fp))
        team_rows = [r for r in rows if r['type'] == 'team']
        assert len(team_rows) == 1
        assert team_rows[0]['id'] == 'https://github.com/orgs/acme/teams/core'
        assert team_rows[0]['name'] == 'Core'
    finally:
        temp_path.unlink()


def test_export_nodes_csv():
    """Test nodes CSV export."""
    graph = GraphData()
    
    user = UserModel(login="user1", id=1, name="User One")
    graph.add_user(user)
    
    org = OrgModel(login="org1", id=2, name="Org One")
    graph.add_org(org)
    
    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        # Seed set carries URLs (the crawler normalizes seeds to URLs).
        export_nodes_csv(graph, temp_path, {"https://github.com/user1"})
        assert temp_path.exists()

        # Read back and verify
        import csv
        with open(temp_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        user_row = [r for r in rows if r['id'] == 'https://github.com/user1'][0]
        assert user_row['is_seed'] == 'True'
    finally:
        temp_path.unlink()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
