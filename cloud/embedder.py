"""
Cloud Embedder — API-based embeddings (no PyTorch).

Uses OpenAI or Cohere API instead of local sentence-transformers.
Result: ~200 MB Docker image instead of 8.7 GB.

Provider selection:
  EMBEDDING_PROVIDER=openai (default) — OpenAI text-embedding-3-large @ 1536D
  EMBEDDING_PROVIDER=cohere           — Cohere embed-multilingual-v3.0 @ 1024D

Cohere is recommended for products with non-English users (Russian, Chinese,
Spanish, etc.) — its multilingual model has equal quality across 100+ languages,
unlike OpenAI which is English-biased.
"""

import logging
import os
import time
from typing import Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    import json
    import urllib.request

logger = logging.getLogger("mengram")


class CloudEmbedder:
    """
    Generates embeddings via API.
    Uses OpenAI text-embedding-3-large with Matryoshka dimensionality reduction (1536D).
    Better quality than text-embedding-3-small at the same dimensions.
    """

    def __init__(self, provider: str = "openai", api_key: Optional[str] = None):
        self.provider = provider
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

        if provider == "openai":
            self.model = "text-embedding-3-large"
            self.dimensions = 1536
            self.url = "https://api.openai.com/v1/embeddings"
        else:
            raise ValueError(f"Unsupported embedding provider: {provider}")

        # Persistent HTTP client with connection pooling
        if HTTPX_AVAILABLE:
            self._client = httpx.Client(
                base_url="https://api.openai.com",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        else:
            self._client = None

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str], max_retries: int = 3) -> list[list[float]]:
        """Generate embeddings for multiple texts with retry and backoff for rate limits."""
        # Sanitize: empty/None → space, truncate to 25k chars (model limit ~8191 tokens ≈ 30k chars)
        texts = [(t[:25000] if t else " ") for t in texts]

        payload = {
            "model": self.model,
            "input": texts,
            "dimensions": self.dimensions,
        }

        for attempt in range(max_retries + 1):
            try:
                if self._client:
                    resp = self._client.post("/v1/embeddings", json=payload)
                    resp.raise_for_status()
                    result = resp.json()
                else:
                    # Fallback: urllib (no httpx installed)
                    data = json.dumps(payload).encode()
                    req = urllib.request.Request(
                        self.url, data=data,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        result = json.loads(resp.read())

                embeddings = sorted(result["data"], key=lambda x: x["index"])
                return [e["embedding"] for e in embeddings]

            except Exception as e:
                is_rate_limit = "429" in str(e)
                is_bad_request = "400" in str(e)
                if is_bad_request and attempt == 0:
                    logger.error(f"Embedding 400 debug: {len(texts)} texts, lengths={[len(t) for t in texts]}, first_100={[t[:100] for t in texts[:3]]}")
                if attempt < max_retries:
                    wait = 2 * (attempt + 1) if not is_rate_limit else 3 * (attempt + 1)
                    logger.warning(f"Embedding attempt {attempt + 1} failed: {e}, retrying in {wait}s")
                    time.sleep(wait)
                else:
                    raise


class CohereEmbedder:
    """
    Multilingual embeddings via Cohere embed-multilingual-v3.0 (1024 dim).
    Equal quality across 100+ languages (incl. Russian, Chinese, Spanish);
    handles cross-lingual retrieval (English query finds Russian doc).
    Limits: 96 texts per request, ~512 tokens (~2000 chars) per text.
    """

    DIMENSIONS = 1024
    MODEL = "embed-multilingual-v3.0"
    MAX_BATCH = 96
    MAX_CHARS = 2000  # conservative; Cohere truncates at 512 tokens

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("COHERE_API_KEY", "")
        if not self.api_key:
            raise ValueError("COHERE_API_KEY not set; cannot use CohereEmbedder")
        self.dimensions = self.DIMENSIONS
        self.model = self.MODEL
        # Lazy import — cohere is a heavy dep, only imported when this class is used
        import cohere
        self._cohere = cohere
        self._client = cohere.ClientV2(api_key=self.api_key)

    def embed(self, text: str, input_type: str = "search_query") -> list[float]:
        """Embed a single text. input_type='search_query' for queries (default
        because most callers do .embed(query_text)), 'search_document' for stored facts."""
        return self.embed_batch([text], input_type=input_type)[0]

    def embed_batch(
        self, texts: list[str], max_retries: int = 3, input_type: str = "search_document"
    ) -> list[list[float]]:
        """Batched embedding. Default input_type='search_document' since most batch
        usage in Mengram is for storing entity/fact text."""
        # Sanitize: empty/None → space, truncate to MAX_CHARS
        clean = [(t[: self.MAX_CHARS] if t else " ") for t in texts]
        # Cohere limit: 96 texts per request — split if needed
        out: list[list[float]] = []
        for i in range(0, len(clean), self.MAX_BATCH):
            chunk = clean[i : i + self.MAX_BATCH]
            for attempt in range(max_retries + 1):
                try:
                    resp = self._client.embed(
                        model=self.model,
                        texts=chunk,
                        input_type=input_type,
                        embedding_types=["float"],
                    )
                    out.extend(resp.embeddings.float)
                    break
                except Exception as e:
                    is_rate_limit = "429" in str(e) or "rate" in str(e).lower()
                    if attempt < max_retries:
                        wait = 3 * (attempt + 1) if is_rate_limit else 2 * (attempt + 1)
                        logger.warning(
                            f"Cohere embedding attempt {attempt + 1} failed: {e}, "
                            f"retrying in {wait}s"
                        )
                        time.sleep(wait)
                    else:
                        raise
        return out


def create_embedder():
    """Factory that returns the embedder configured via EMBEDDING_PROVIDER env.

    Returns None if no provider can be initialized (e.g. missing API key).
    Caller is expected to handle None — most code paths already do for
    self-host setups without OpenAI key.
    """
    provider = os.environ.get("EMBEDDING_PROVIDER", "openai").lower()
    try:
        if provider == "cohere":
            return CohereEmbedder()
        # default — openai (also fallback if unknown provider)
        return CloudEmbedder(provider="openai")
    except Exception as e:
        logger.warning(f"Embedder init failed for provider={provider}: {e}")
        return None
