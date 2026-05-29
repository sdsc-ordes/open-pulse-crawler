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


# --- fetch ----------------------------------------------------------

def test_fetch_user_returns_huggingface_user(adapter):
    adapter._client.get_user_overview.return_value = {
        "user": "karpathy",
        "type": "user",
        "fullname": "Andrej Karpathy",
        "isPro": False,
        "avatarUrl": "https://cdn.hf.co/karpathy.png",
        "numModels": 30,
        "numDatasets": 5,
        "numSpaces": 2,
        "numPapers": 12,
        "numFollowers": 80000,
        "orgs": [{"name": "nanoGPT"}],
    }
    node = adapter.fetch("https://huggingface.co/karpathy")
    assert isinstance(node, HuggingFaceUser)
    assert node.username == "karpathy"
    assert node.fullname == "Andrej Karpathy"
    assert node.num_models == 30
    assert node.member_orgs == ["nanoGPT"]
    # Should NOT call org endpoint when user lookup succeeded
    adapter._client.get_org_overview.assert_not_called()


def test_fetch_org_falls_through_when_user_404(adapter):
    """If /api/users/<x>/overview returns None, fall through to /api/organizations."""
    adapter._client.get_user_overview.return_value = None
    adapter._client.get_org_overview.return_value = {
        "name": "meta-llama",
        "fullname": "Meta Llama",
        "isVerified": True,
        "plan": "enterprise",
        "numModels": 80,
        "numFollowers": 5000,
    }
    node = adapter.fetch("https://huggingface.co/meta-llama")
    assert isinstance(node, HuggingFaceOrg)
    assert node.org_name == "meta-llama"
    assert node.is_verified is True
    adapter._client.get_user_overview.assert_called_once_with("meta-llama")
    adapter._client.get_org_overview.assert_called_once_with("meta-llama")


def test_fetch_user_or_org_both_404_returns_none(adapter):
    adapter._client.get_user_overview.return_value = None
    adapter._client.get_org_overview.return_value = None
    assert adapter.fetch("https://huggingface.co/nobody-anywhere") is None


def test_fetch_model_returns_repo(adapter):
    adapter._client.get_model.return_value = {
        "id": "meta-llama/Llama-3.2-1B",
        "author": "meta-llama",
        "sha": "abc123",
        "tags": ["transformers", "llama-3"],
        "downloads": 2222053,
        "likes": 2412,
        "gated": True,
        "pipeline_tag": "text-generation",
        "library_name": "transformers",
        "cardData": {"license": "llama3.2", "language": ["en"]},
    }
    node = adapter.fetch("https://huggingface.co/meta-llama/Llama-3.2-1B")
    assert isinstance(node, HuggingFaceRepo)
    assert node.repo_type == "model"
    assert node.repo_id == "meta-llama/Llama-3.2-1B"
    assert node.owner == "meta-llama"
    assert node.repo_name == "Llama-3.2-1B"
    assert node.pipeline_tag == "text-generation"
    assert node.library_name == "transformers"
    assert node.downloads == 2222053
    assert node.license == "llama3.2"
    assert node.language == ["en"]
    assert node.gated is True


def test_fetch_dataset_returns_repo(adapter):
    adapter._client.get_dataset.return_value = {
        "id": "openai/gsm8k",
        "author": "openai",
        "downloads": 5000,
        "likes": 100,
        "paperswithcode_id": "gsm8k",
        "cardData": {"license": "mit"},
    }
    node = adapter.fetch("https://huggingface.co/datasets/openai/gsm8k")
    assert isinstance(node, HuggingFaceRepo)
    assert node.repo_type == "dataset"
    assert node.paperswithcode_id == "gsm8k"


def test_fetch_space_returns_repo_with_runtime(adapter):
    adapter._client.get_space.return_value = {
        "id": "black-forest-labs/FLUX.1-schnell",
        "author": "black-forest-labs",
        "likes": 5067,
        "sdk": "gradio",
        "runtime": {"stage": "RUNNING"},
        "models": ["black-forest-labs/FLUX.1-schnell"],
    }
    node = adapter.fetch("https://huggingface.co/spaces/black-forest-labs/FLUX.1-schnell")
    assert isinstance(node, HuggingFaceRepo)
    assert node.repo_type == "space"
    assert node.sdk == "gradio"
    assert node.runtime_stage == "RUNNING"
    assert node.used_models == ["black-forest-labs/FLUX.1-schnell"]


def test_fetch_paper_returns_huggingface_paper(adapter):
    adapter._client.get_paper.return_value = {
        "id": "2307.09288",
        "title": "Llama 2: Open Foundation and Fine-Tuned Chat Models",
        "summary": "We develop Llama 2…",
        "ai_summary": "Llama 2 is open-weight.",
        "ai_keywords": ["llm", "fine-tuning"],
        "authors": [{"name": "Hugo Touvron"}, {"name": "Louis Martin"}],
        "upvotes": 252,
        "publishedAt": "2023-07-18T00:00:00Z",
        "githubRepo": "facebookresearch/llama",
        "linkedModels": [{"id": "meta-llama/Llama-2-7b"}, {"id": "meta-llama/Llama-2-13b"}],
        "linkedDatasets": [{"id": "some/dataset"}],
        "linkedSpaces": [],
        "numTotalModels": 8,
        "numTotalDatasets": 2,
        "numTotalSpaces": 14,
    }
    node = adapter.fetch("https://huggingface.co/papers/2307.09288")
    assert isinstance(node, HuggingFacePaper)
    assert node.arxiv_id == "2307.09288"
    assert node.arxiv_url == "https://arxiv.org/abs/2307.09288"
    assert node.title.startswith("Llama 2")
    assert node.github_repo == "facebookresearch/llama"
    assert node.num_linked_models == 8
    assert len(node.authors) == 2


def test_fetch_paper_404_returns_none(adapter):
    adapter._client.get_paper.return_value = None
    assert adapter.fetch("https://huggingface.co/papers/9999.99999") is None


def test_fetch_collection_returns_collection(adapter):
    adapter._client.get_collection.return_value = {
        "slug": "meta-llama/llama-32-x-675bfd70",
        "owner": {"name": "meta-llama"},
        "title": "Llama 3.2 evals",
        "description": "Release bundle.",
        "upvotes": 120,
        "lastUpdated": "2024-12-01T00:00:00Z",
    }
    node = adapter.fetch(
        "https://huggingface.co/collections/meta-llama/llama-32-x-675bfd70"
    )
    assert isinstance(node, HuggingFaceCollection)
    assert node.slug == "meta-llama/llama-32-x-675bfd70"
    assert node.owner == "meta-llama"
    assert node.title == "Llama 3.2 evals"


def test_fetch_unknown_url_form_returns_none(adapter):
    assert adapter.fetch("https://huggingface.co/blog/some-post") is None
