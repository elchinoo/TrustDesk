from trustdesk.chunking import chunk_markdown
from trustdesk.embeddings import hash_embed


def test_chunks_carry_heading_path():
    md = "# Policy\n\nintro\n\n## Data at rest\n\nAES-256 everywhere.\n\n## Data in transit\n\nTLS 1.2+."
    chunks = chunk_markdown(md)
    assert [c.heading for c in chunks] == ["Policy", "Policy > Data at rest", "Policy > Data in transit"]
    assert "AES-256" in chunks[1].body


def test_long_sections_are_split():
    md = "# T\n\n" + "\n\n".join(["word " * 100] * 6)
    assert len(chunk_markdown(md, max_chars=1000)) > 1


def test_hash_embed_is_normalised_and_deterministic():
    a, b = hash_embed("encrypt data at rest", 64), hash_embed("encrypt data at rest", 64)
    assert a == b and abs(sum(x * x for x in a) - 1.0) < 1e-9
    assert len(a) == 64
