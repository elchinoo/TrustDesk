import json

import httpx
import pytest

from trustdesk import embeddings
from trustdesk.embeddings import EmbeddingError

SECRET = "sk-" + "test" * 5  # fake placeholder  # pragma: allowlist secret


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1/")
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    monkeypatch.setenv("EMBEDDING_MODEL", "some-model")
    monkeypatch.setenv("EMBEDDING_DIM", "4")
    monkeypatch.delenv("EMBEDDING_SEND_DIMENSIONS", raising=False)
    monkeypatch.setattr(embeddings.time, "sleep", lambda _: None)


def _use(monkeypatch, handler):
    monkeypatch.setattr(embeddings, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def _ok(request):
    n = len(json.loads(request.content)["input"])
    # return out of order to prove we sort by index
    data = [{"index": i, "embedding": [float(i), 0, 0, 0]} for i in range(n)][::-1]
    return httpx.Response(200, json={"data": data})


def test_request_shape_and_ordering(llm, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return _ok(request)

    _use(monkeypatch, handler)
    out = embeddings.embed(["a", "b", "c"])
    assert seen["url"] == "https://llm.example/v1/embeddings"  # trailing slash on base URL is tolerated
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["body"] == {"model": "some-model", "input": ["a", "b", "c"], "dimensions": 4}
    assert [v[0] for v in out] == [0.0, 1.0, 2.0]


def test_dimensions_field_can_be_disabled(llm, monkeypatch):
    monkeypatch.setenv("EMBEDDING_SEND_DIMENSIONS", "false")
    bodies = []
    _use(monkeypatch, lambda r: (bodies.append(json.loads(r.content)), _ok(r))[1])
    embeddings.embed(["a"])
    assert "dimensions" not in bodies[0]


def test_batches_large_inputs(llm, monkeypatch):
    sizes = []
    _use(monkeypatch, lambda r: (sizes.append(len(json.loads(r.content)["input"])), _ok(r))[1])
    assert len(embeddings.embed(["x"] * 150)) == 150
    assert sizes == [64, 64, 22]


def test_retries_transient_errors_then_succeeds(llm, monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, text="slow down") if calls["n"] < 3 else _ok(request)

    _use(monkeypatch, handler)
    assert len(embeddings.embed(["a"])) == 1 and calls["n"] == 3


def test_client_errors_are_not_retried_and_never_leak_the_key(llm, monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, text=f"bad key {SECRET}")  # a provider echoing the key back

    _use(monkeypatch, handler)
    with pytest.raises(EmbeddingError) as exc:
        embeddings.embed(["a"])
    assert calls["n"] == 1 and "401" in str(exc.value) and SECRET not in str(exc.value)


def test_dimension_mismatch_has_actionable_message(llm, monkeypatch):
    _use(monkeypatch, lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 8}]}))
    with pytest.raises(EmbeddingError, match=r"EMBEDDING_DIM=8.*reembed"):
        embeddings.embed(["a"])


def test_dimensions_rejection_hints_at_the_flag(llm, monkeypatch):
    _use(monkeypatch, lambda r: httpx.Response(400, text="unknown field dimensions"))
    with pytest.raises(EmbeddingError, match="EMBEDDING_SEND_DIMENSIONS=false"):
        embeddings.embed(["a"])


def test_network_failure_is_reported_without_details(llm, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("boom " + SECRET)

    _use(monkeypatch, handler)
    with pytest.raises(EmbeddingError) as exc:
        embeddings.embed(["a"])
    assert SECRET not in str(exc.value)


def test_offline_fallback_when_no_endpoint(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("EMBEDDING_DIM", "16")
    assert len(embeddings.embed(["hello world"])[0]) == 16
