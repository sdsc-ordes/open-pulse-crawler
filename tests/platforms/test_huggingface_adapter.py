"""Tests for the HuggingFace PlatformAdapter."""
from unittest.mock import MagicMock
import pytest

from open_pulse_crawler.models import (
    HuggingFaceUser, HuggingFaceOrg, HuggingFaceRepo,
    HuggingFacePaper, HuggingFaceCollection,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.huggingface.adapter import HuggingFaceAdapter


@pytest.fixture
def adapter():
    client = MagicMock()
    return HuggingFaceAdapter(client=client, instance_host="huggingface.co")


# --- classify -------------------------------------------------------

def test_classify_model_url(adapter):
    assert adapter.classify("https://huggingface.co/meta-llama/Llama-3.2-1B") == NodeKind.REPO


def test_classify_dataset_url(adapter):
    assert adapter.classify("https://huggingface.co/datasets/openai/gsm8k") == NodeKind.REPO


def test_classify_space_url(adapter):
    assert adapter.classify("https://huggingface.co/spaces/black-forest-labs/FLUX.1-schnell") == NodeKind.REPO


def test_classify_paper_url(adapter):
    assert adapter.classify("https://huggingface.co/papers/2307.09288") == NodeKind.REPO


def test_classify_collection_url(adapter):
    assert adapter.classify(
        "https://huggingface.co/collections/meta-llama/llama-32-language-models-and-evals-675bfd70e574a62dd0e40586"
    ) == NodeKind.ORG


def test_classify_user_or_org_url(adapter):
    assert adapter.classify("https://huggingface.co/karpathy") == NodeKind.USER_OR_ORG


def test_classify_reserved_index_pages_return_none(adapter):
    """The bare reserved prefixes are index pages, not entities."""
    assert adapter.classify("https://huggingface.co/datasets") is None
    assert adapter.classify("https://huggingface.co/spaces") is None
    assert adapter.classify("https://huggingface.co/papers") is None
    assert adapter.classify("https://huggingface.co/collections") is None


def test_classify_unknown_path_returns_none(adapter):
    assert adapter.classify("https://huggingface.co/blog/some-post") is None


def test_classify_legacy_arxiv_id_returns_none(adapter):
    """Legacy arxiv IDs (cond-mat/0303517 form) are not supported."""
    assert adapter.classify("https://huggingface.co/papers/cond-mat/0303517") is None


# --- normalize_uri --------------------------------------------------

def test_normalize_strips_trailing_slash(adapter):
    assert adapter.normalize_uri("https://huggingface.co/meta-llama/Llama-3.2-1B/") == \
        "https://huggingface.co/meta-llama/Llama-3.2-1B"


def test_normalize_lowercases_host(adapter):
    assert adapter.normalize_uri("https://HUGGINGFACE.CO/meta-llama/Llama-3.2-1B") == \
        "https://huggingface.co/meta-llama/Llama-3.2-1B"


def test_normalize_strips_query_and_fragment(adapter):
    assert adapter.normalize_uri("https://huggingface.co/karpathy?tab=models#header") == \
        "https://huggingface.co/karpathy"


def test_normalize_paper_with_version_suffix_preserved(adapter):
    assert adapter.normalize_uri("https://huggingface.co/papers/2307.09288v2") == \
        "https://huggingface.co/papers/2307.09288v2"


def test_normalize_dataset_url(adapter):
    assert adapter.normalize_uri("https://huggingface.co/datasets/openai/gsm8k") == \
        "https://huggingface.co/datasets/openai/gsm8k"


def test_normalize_collection_url(adapter):
    coll_url = "https://huggingface.co/collections/meta-llama/llama-32-x-675bfd70"
    assert adapter.normalize_uri(coll_url) == coll_url


def test_normalize_empty_raises(adapter):
    with pytest.raises(ValueError):
        adapter.normalize_uri("")
