"""Tests for the v2 → v3 state-file cutover.

Task 15 makes the schema bump a hard break: a v2 state file is refused
at load with ``IncompatibleStateError`` rather than the previous silent
log-and-continue, so a stale snapshot can't poison a v3 run.
"""

import json

import pytest

from open_pulse_crawler.crawler import IncompatibleStateError
from open_pulse_crawler.models import GRAPH_SCHEMA_VERSION


def test_graph_schema_is_v3():
    assert GRAPH_SCHEMA_VERSION == 3


def test_state_schema_is_v3():
    from open_pulse_crawler.crawler import STATE_SCHEMA_VERSION
    assert STATE_SCHEMA_VERSION == 3


def test_v2_state_file_rejected(tmp_path):
    """A state file written under schema_version=2 must be refused on load."""
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "schema_version": 2,
        "visited": ["https://github.com/torvalds"],
        "queue": [],
        "current_round": 0,
        "graph": {"users": {}, "orgs": {}, "repos": {}, "teams": {}},
    }))

    from open_pulse_crawler.crawler import GitHubCrawler
    from open_pulse_crawler.platforms import PlatformRegistry
    from tests.platforms._fake_adapter import FakePlatformAdapter

    reg = PlatformRegistry()
    reg.register(FakePlatformAdapter())

    c = GitHubCrawler(registry=reg, state_file=state, max_rounds=1)
    with pytest.raises(IncompatibleStateError) as exc_info:
        c.load_state()
    msg = str(exc_info.value).lower()
    # The message names the on-disk version vs the build's requirement so
    # an operator can confirm what they're dealing with.
    assert "schema" in msg or "earlier release" in msg
    assert "2" in str(exc_info.value)
    assert "3" in str(exc_info.value)


def test_v3_state_file_loads_cleanly(tmp_path):
    """A current-schema state file should load without error."""
    from open_pulse_crawler.crawler import GitHubCrawler, STATE_SCHEMA_VERSION
    from open_pulse_crawler.platforms import PlatformRegistry
    from tests.platforms._fake_adapter import FakePlatformAdapter

    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "schema_version": STATE_SCHEMA_VERSION,
        "visited": [],
        "queue": [],
        "current_round": 0,
        "graph": {"users": {}, "orgs": {}, "repos": {}, "teams": {}},
    }))

    reg = PlatformRegistry()
    reg.register(FakePlatformAdapter())
    c = GitHubCrawler(registry=reg, state_file=state, max_rounds=1)
    c.load_state()


def test_missing_state_file_is_not_an_error(tmp_path):
    """``load_state`` returns False (not raises) when no state file exists yet."""
    from open_pulse_crawler.crawler import GitHubCrawler
    from open_pulse_crawler.platforms import PlatformRegistry
    from tests.platforms._fake_adapter import FakePlatformAdapter

    reg = PlatformRegistry()
    reg.register(FakePlatformAdapter())
    c = GitHubCrawler(registry=reg, state_file=tmp_path / "does-not-exist.json", max_rounds=1)
    assert c.load_state() is False
