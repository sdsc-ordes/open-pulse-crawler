
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


def test_crawler_sets_properties():
    """Test that crawler sets is_explored + timestamp correctly."""
    mock_client = MagicMock()

    mock_user = MagicMock()
    mock_user.login = "some-user"
    mock_user.name = "Some User"
    mock_user.id = 1
    mock_user.type = "User"
    mock_user.get_repos = MagicMock(return_value=[])
    mock_user.get_orgs = MagicMock(return_value=[])

    mock_client.get_user.return_value = mock_user
    mock_client._make_request.side_effect = lambda x: x()

    crawler = GitHubCrawler(client=mock_client)

    user_model = crawler._process_user("https://github.com/some-user")

    assert user_model is not None
    assert user_model.is_explored is True
    assert user_model.exploration_timestamp is not None


def test_export_nodes_csv_content(tmp_path):
    """Test that CSV export includes new columns and unexplored nodes."""
    graph = GraphData()

    user = UserModel(
        login="explored-user",
        is_explored=True,
        exploration_timestamp="2023-01-01T12:00:00",
    )
    graph.add_user(user)

    # discovered_nodes is keyed by canonical URL post-refactor (the crawler
    # writes URLs into this dict via _track_discovered_node).
    discovered_nodes = {
        "https://github.com/unexplored-user": (
            "user", "https://github.com/explored-user", "user"
        ),
        "https://github.com/some-org/repo": (
            "repo", "https://github.com/explored-user", "user"
        ),
    }

    seed_nodes = {"https://github.com/explored-user"}

    output_file = tmp_path / "nodes.csv"

    export_nodes_csv(
        graph,
        output_file,
        seed_nodes,
        discovered_nodes=discovered_nodes,
    )

    with open(output_file, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 3

    row1 = next(r for r in rows if r['id'] == 'https://github.com/explored-user')
    assert row1['is_explored'] == 'True'
    assert row1['exploration_timestamp'] == '2023-01-01T12:00:00'

    row2 = next(r for r in rows if r['id'] == 'https://github.com/unexplored-user')
    assert row2['is_explored'] == 'False'
    assert row2['exploration_timestamp'] == ''

    row3 = next(r for r in rows if r['id'] == 'https://github.com/some-org/repo')
    assert row3['is_explored'] == 'False'
