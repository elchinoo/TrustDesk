"""The packaged fictional "Acme" policy pack: security policies plus HR, travel and expense rules."""
from pathlib import Path

from .ingest import ingest_file

PACK = Path(__file__).resolve().parent / "seed" / "acme"
_PREFIXES = {"hr": "hr", "travel": "travel", "expense": "expense", "governance": "governance"}


def doc_type_for(name: str) -> str:
    """Policy type from the file name prefix (hr-, travel-, expense-, governance-); everything else is security."""
    return _PREFIXES.get(name.split("-", 1)[0], "security")


def files() -> list[Path]:
    return sorted(PACK.glob("*.md"))


def load(conn=None) -> list[dict]:
    results = []
    for f in files():
        r = ingest_file(f, doc_type=doc_type_for(f.name), conn=conn)
        results.append({"source": f.name, "status": r["status"], "chunks": r["chunks"]})
    return results
