"""Embeddings through any OpenAI-compatible endpoint, with an offline fallback.

The fallback is a signed feature-hashing embedder: deterministic, dependency-free and good enough
to demo hybrid search without credentials. It is lexical, NOT semantic: set LLM_BASE_URL for real use.
"""
import hashlib
import math
import re

import httpx

from .config import get_settings

_TOKEN = re.compile(r"[a-z0-9]+")


def hash_embed(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    tokens = _TOKEN.findall(text.lower())
    for tok in tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]:
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
        vec[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    if not s.llm_base_url:
        return [hash_embed(t, s.embedding_dim) for t in texts]
    out: list[list[float]] = []
    headers = {"Authorization": f"Bearer {s.llm_api_key}"} if s.llm_api_key else {}
    with httpx.Client(timeout=60) as client:
        for i in range(0, len(texts), 64):
            batch = texts[i : i + 64]
            resp = client.post(
                f"{s.llm_base_url}/embeddings",
                headers=headers,
                json={"model": s.embedding_model, "input": batch, "dimensions": s.embedding_dim},
            )
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            vecs = [d["embedding"] for d in data]
            if vecs and len(vecs[0]) != s.embedding_dim:
                raise ValueError(
                    f"model returned {len(vecs[0])} dims but EMBEDDING_DIM={s.embedding_dim}; "
                    "set EMBEDDING_DIM to match and re-run migrations on a fresh schema"
                )
            out.extend(vecs)
    return out
