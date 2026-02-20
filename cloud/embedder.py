"""
Cloud Embedder — API-based embeddings (no PyTorch).

Uses OpenAI or Anthropic API instead of local sentence-transformers.
Result: ~200 MB Docker image instead of 8.7 GB.
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

    def embed_batch(self, texts: list[str], max_retries: int = 2) -> list[list[float]]:
        """Generate embeddings for multiple texts with retry."""
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
                if attempt < max_retries:
                    wait = (attempt + 1) * 1.0
                    logger.warning(f"Embedding attempt {attempt + 1} failed: {e}, retrying in {wait}s")
                    time.sleep(wait)
                else:
                    raise
