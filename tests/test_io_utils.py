"""Tests for I/O utilities."""

import tempfile
from pathlib import Path

from open_pulse_crawler.models import GraphData, UserModel, OrgModel, RepoModel
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
        assert "testuser" in data["users"]
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
        export_nodes_csv(graph, temp_path, {"user1"})
        assert temp_path.exists()
        
        # Read back and verify
        import csv
        with open(temp_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        assert len(rows) == 2
        user_row = [r for r in rows if r['id'] == 'user1'][0]
        assert user_row['is_seed'] == 'True'
    finally:
        temp_path.unlink()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
