"""MCP surface. Tools are thin wrappers over the same functions the REST API uses."""
from mcp.server.mcpserver import MCPServer

from .db import connect
from .search import hybrid_search

mcp = MCPServer(
    "trustdesk",
    instructions=(
        "TrustDesk exposes a company's security and compliance knowledge base. "
        "Use search_knowledge to find policy passages and always cite the returned source and heading."
    ),
)


def _shape(rows: list[dict]) -> list[dict]:
    return [
        {
            "chunk_id": r["chunk_id"],
            "document": r["title"],
            "source": r["source"],
            "version": r["version"],
            "heading": r["heading"],
            "text": r["body"],
            "score": round(r["score"], 5),
        }
        for r in rows
    ]


@mcp.tool()
def search_knowledge(query: str, limit: int = 5, doc_type: str | None = None) -> list[dict]:
    """Hybrid (vector + full-text) search over the knowledge base. Returns passages with provenance."""
    return _shape(hybrid_search(query[:500], limit=max(1, min(limit, 20)), doc_type=doc_type))


@mcp.tool()
def get_policy_passage(chunk_id: int) -> dict:
    """Fetch one passage by chunk id, with its document metadata."""
    with connect() as conn:
        row = conn.execute(
            "SELECT c.id AS chunk_id, d.title, d.source, d.version, c.heading, c.body "
            "FROM kb_chunk c JOIN kb_document d ON d.id=c.doc_id WHERE c.id=%s",
            (chunk_id,),
        ).fetchone()
    return row or {"error": "not found"}


@mcp.tool()
def list_documents() -> list[dict]:
    """List the documents in the knowledge base (id, title, source, version, chunk count)."""
    with connect() as conn:
        return conn.execute(
            "SELECT d.id, d.title, d.source, d.doc_type, d.version, count(c.id) AS chunks "
            "FROM kb_document d LEFT JOIN kb_chunk c ON c.doc_id=d.id GROUP BY d.id ORDER BY d.id"
        ).fetchall()
