
import pytest
import csv
import io
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

from open_pulse_crawler.models import UserModel, OrgModel, RepoModel, GraphData, GitHubItemType
from open_pulse_crawler.crawler import GitHubCrawler
from open_pulse_crawler.io_utils import export_nodes_csv

def test_model_defaults():
    """Test that new fields have correct defaults."""
    user = UserModel(login="test")
    assert user.is_explored is False
    assert user.exploration_timestamp is None
    assert user.is_epfl is False

def test_crawler_sets_properties():
    """Test that crawler sets properties correctly."""
    # Mock client
    mock_client = MagicMock()
    
    # Mock user response
    mock_user = MagicMock()
    mock_user.login = "epfl-user"
    mock_user.name = "EPFL User"
    mock_user.id = 1
    mock_user.type = "User"
    mock_user.get_repos = MagicMock(return_value=[])
    mock_user.get_orgs = MagicMock(return_value=[])
    
    mock_client.get_user.return_value = mock_user
    mock_client._make_request.side_effect = lambda x: x() # Just execute the callable
    
    # Initialize crawler with EPFL list
    crawler = GitHubCrawler(
        client=mock_client,
        epfl_entities={"epfl-user"}
    )
    
    # Process user
    user_model = crawler._process_user("epfl-user")
    
    assert user_model is not None
    assert user_model.is_explored is True
    assert user_model.exploration_timestamp is not None
    assert user_model.is_epfl is True
    
    # Test non-EPFL user
    mock_user.login = "other-user"
    mock_client.get_user.return_value = mock_user
    
    user_model = crawler._process_user("other-user")
    assert user_model.is_epfl is False

def test_export_nodes_csv_content(tmp_path):
    """Test that CSV export includes new columns and unexplored nodes."""
    graph = GraphData()
    
    # Add an explored user
    user = UserModel(
        login="explored-user",
        is_explored=True,
        exploration_timestamp="2023-01-01T12:00:00",
        is_epfl=True
    )
    graph.add_user(user)
    
    # Discovered nodes (unexplored)
    discovered_nodes = {
        "unexplored-user": ("user", "explored-user", "user"),
        "epfl-repo/repo": ("repo", "explored-user", "user")
    }
    
    seed_nodes = {"explored-user"}
    epfl_entities = {"explored-user", "epfl-repo"}
    
    output_file = tmp_path / "nodes.csv"
    
    export_nodes_csv(
        graph, 
        output_file, 
        seed_nodes, 
        discovered_nodes=discovered_nodes, 
        epfl_entities=epfl_entities
    )
    
    # Read CSV and verify
    with open(output_file, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        
    assert len(rows) == 3
    
    # Check explored user
    row1 = next(r for r in rows if r['id'] == 'explored-user')
    assert row1['is_explored'] == 'True'
    assert row1['exploration_timestamp'] == '2023-01-01T12:00:00'
    assert row1['is_epfl'] == 'True'
    
    # Check unexplored user
    row2 = next(r for r in rows if r['id'] == 'unexplored-user')
    assert row2['is_explored'] == 'False'
    assert row2['exploration_timestamp'] == ''
    assert row2['is_epfl'] == 'False'
    
    # Check unexplored repo (should be EPFL because owner is in list)
    row3 = next(r for r in rows if r['id'] == 'epfl-repo/repo')
    assert row3['is_explored'] == 'False'
    assert row3['is_epfl'] == 'True'
