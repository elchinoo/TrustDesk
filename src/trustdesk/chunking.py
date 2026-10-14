"""Heading-aware chunking for Markdown/plain text."""
import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Chunk:
    heading: str
    body: str


def chunk_markdown(text: str, max_chars: int = 1200) -> list[Chunk]:
    sections: list[tuple[str, list[str]]] = [("", [])]
    stack: list[str] = []
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            stack = stack[: level - 1] + [m.group(2).strip()]
            sections.append((" > ".join(stack), []))
        else:
            sections[-1][1].append(line)
    chunks: list[Chunk] = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        buf = ""
        for p in paras:
            if buf and len(buf) + len(p) + 2 > max_chars:
                chunks.append(Chunk(heading, buf))
                buf = p
            else:
                buf = f"{buf}\n\n{p}" if buf else p
        if buf:
            chunks.append(Chunk(heading, buf))
    return chunks
