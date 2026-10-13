"""Structure-aware Markdown chunking: one chunk per section, carrying its heading path, so a
search hit can cite "Payments API incident response > Ledger checks" instead of a bare file."""
import re

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def parse(text: str, fallback_title: str, max_chars: int = 1500) -> tuple[str, list[tuple[str | None, str]]]:
    """Return (title, [(heading_path, body), ...]). Headings inside code fences are ignored."""
    text = text.replace("\r\n", "\n")
    if text.startswith("---\n"):  # YAML front matter (OKF files carry it); M3 parses it properly
        end = text.find("\n---", 4)
        if end != -1:
            text = text[end + 4:].lstrip("\n")
    title: str | None = None
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str | None, str]] = []
    buf: list[str] = []
    fenced = False

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append((" > ".join(h for _, h in stack) or None, body))
        buf.clear()

    for line in text.split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
        match = None if fenced else _HEADING.match(line)
        if match:
            flush()
            level, heading = len(match.group(1)), match.group(2).strip()
            if level == 1 and title is None:
                title = heading
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
        else:
            buf.append(line)
    flush()
    return title or fallback_title, [(h, part) for h, body in sections for part in _split(body, max_chars)]


def _split(body: str, max_chars: int) -> list[str]:
    if len(body) <= max_chars:
        return [body]
    parts, current = [], ""
    for para in body.split("\n\n"):
        if current and len(current) + 2 + len(para) > max_chars:
            parts.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        parts.append(current)
    return [p[i:i + max_chars] for p in parts for i in range(0, len(p), max_chars)]
