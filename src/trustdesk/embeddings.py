"""Embeddings through any OpenAI-compatible endpoint (POST {LLM_BASE_URL}/embeddings), with an offline fallback.

The fallback is a signed feature-hashing embedder: deterministic, dependency-free and good enough
to demo hybrid search without credentials. It is lexical, NOT semantic: set LLM_BASE_URL for real use.
"""
import hashlib
import itertools
import math
import re
import time

import httpx

from .config import get_settings

_TOKEN = re.compile(r"[a-z0-9]+")
_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
BATCH = 64


class EmbeddingError(RuntimeError):
    """Raised with a message that is safe to show: it never contains the API key."""


def hash_embed(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    tokens = _TOKEN.findall(text.lower())
    for tok in tokens + [f"{a}_{b}" for a, b in itertools.pairwise(tokens)]:
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
        vec[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _client() -> httpx.Client:  # indirection so tests can inject a mock transport
    return httpx.Client(timeout=60)


def _post(client: httpx.Client, url: str, headers: dict, body: dict, retries: int = 3) -> httpx.Response:
    for attempt in range(retries + 1):
        try:
            resp = client.post(url, headers=headers, json=body)
        except httpx.TransportError as exc:
            if attempt == retries:
                raise EmbeddingError(f"could not reach the embeddings endpoint: {type(exc).__name__}") from exc
        else:
            if resp.status_code not in _RETRY_STATUS or attempt == retries:
                return resp
        time.sleep(min(2**attempt, 8))
    raise AssertionError("unreachable")


def embed(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    if not s.llm_base_url:
        return [hash_embed(t, s.embedding_dim) for t in texts]
    url = f"{s.llm_base_url}/embeddings"
    headers = {"Authorization": f"Bearer {s.llm_api_key}"} if s.llm_api_key else {}
    out: list[list[float]] = []
    with _client() as client:
        for i in range(0, len(texts), BATCH):
            body: dict = {"model": s.embedding_model, "input": texts[i : i + BATCH]}
            if s.send_dimensions:
                body["dimensions"] = s.embedding_dim
            resp = _post(client, url, headers, body)
            if resp.status_code >= 400:
                detail = resp.text[:300].replace(s.llm_api_key, "***") if s.llm_api_key else resp.text[:300]
                hint = " (if it mentions 'dimensions', set EMBEDDING_SEND_DIMENSIONS=false)" if s.send_dimensions else ""
                raise EmbeddingError(f"embeddings endpoint returned HTTP {resp.status_code}: {detail}{hint}")
            try:
                data = sorted(resp.json()["data"], key=lambda d: d["index"])
                vecs = [d["embedding"] for d in data]
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbeddingError("unexpected response from the embeddings endpoint (no data[].embedding)") from exc
            if len(vecs) != len(body["input"]):
                raise EmbeddingError("embeddings endpoint returned a different number of vectors than inputs")
            if vecs and len(vecs[0]) != s.embedding_dim:
                raise EmbeddingError(
                    f"model returned {len(vecs[0])} dimensions but EMBEDDING_DIM={s.embedding_dim}. "
                    f"Set EMBEDDING_DIM={len(vecs[0])} (or a size the model supports) and run `trustdesk reembed`."
                )
            out.extend(vecs)
    return out
