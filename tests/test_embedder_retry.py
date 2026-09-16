"""Regression test: quota/auth errors from the embedding provider must fail
fast, not retry with a blocking time.sleep(). A Cohere trial-key 429 used to
retry 3x with 3/6/9s sleeps *per queued add*, freezing the whole process
(cloud/api.py's add-pipeline runs on a plain threading.Thread, but an
unbounded pile of those sleeping threads under a backlog starved every
gunicorn worker, including /health).
"""
import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
_spec = importlib.util.spec_from_file_location(
    "embedder", Path(__file__).parent.parent / "cloud" / "embedder.py"
)
_embedder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_embedder)


def test_quota_error_is_not_retryable():
    quota_exc = Exception(
        "status_code: 429, body: {'message': \"You are using a Trial key, "
        "which is limited to 1000 API calls / month\"}"
    )
    assert _embedder._is_retryable(quota_exc) is False


def test_auth_error_is_not_retryable():
    assert _embedder._is_retryable(Exception("401 Unauthorized")) is False


def test_generic_429_is_retryable():
    # A real transient rate limit (no quota/auth wording) should still retry.
    assert _embedder._is_retryable(Exception("429 Too Many Requests")) is True


def test_network_error_is_retryable():
    assert _embedder._is_retryable(Exception("Connection reset by peer")) is True


def test_cloud_embedder_fails_fast_on_quota_no_sleep():
    """embed_batch must raise EmbeddingQuotaExceeded immediately — zero
    retries, zero time.sleep() — when the provider reports quota exhaustion."""
    emb = _embedder.CloudEmbedder.__new__(_embedder.CloudEmbedder)
    emb.provider = "openai"
    emb.model = "text-embedding-3-large"
    emb.dimensions = 1536
    emb.url = "https://api.openai.com/v1/embeddings"
    emb.api_key = "sk-test"

    quota_error = Exception(
        "status_code: 429, body: {'message': \"You are using a Trial key, "
        "which is limited to 1000 API calls / month\"}"
    )
    mock_client = MagicMock()
    mock_client.post.side_effect = quota_error
    emb._client = mock_client

    slept = []
    orig_sleep = time.sleep
    _embedder.time.sleep = lambda s: slept.append(s)
    try:
        try:
            emb.embed_batch(["hello"])
            assert False, "expected EmbeddingQuotaExceeded"
        except _embedder.EmbeddingQuotaExceeded:
            pass
    finally:
        _embedder.time.sleep = orig_sleep

    assert slept == [], f"blocking sleep was called on a quota error: {slept}"
    assert mock_client.post.call_count == 1, "quota error must not be retried"


def test_cloud_embedder_still_retries_transient_errors():
    """Sanity check: genuine transient errors keep retrying (existing behavior
    preserved) — only quota/auth errors get the fail-fast path."""
    emb = _embedder.CloudEmbedder.__new__(_embedder.CloudEmbedder)
    emb.provider = "openai"
    emb.model = "text-embedding-3-large"
    emb.dimensions = 1536
    emb.url = "https://api.openai.com/v1/embeddings"
    emb.api_key = "sk-test"

    mock_client = MagicMock()
    mock_client.post.side_effect = Exception("connection reset")
    emb._client = mock_client

    slept = []
    _embedder.time.sleep = lambda s: slept.append(s)
    try:
        try:
            emb.embed_batch(["hello"], max_retries=2)
            assert False, "expected the original exception after exhausting retries"
        except Exception as e:
            assert not isinstance(e, _embedder.EmbeddingQuotaExceeded)
    finally:
        _embedder.time.sleep = time.sleep

    assert mock_client.post.call_count == 3  # initial + 2 retries
    assert len(slept) == 2
