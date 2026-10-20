"""Pointer mode's live reads (F11a): a pointer document keeps no text, so its passages are read from its source when
someone asks, for that person only, and dropped once the answer is made.

Each read asks the source again who may read the item now, the way a sync works it out, and goes ahead only when the
asker is among them (their own principals and the source accounts linked to them), failing closed: a source that
doesn't answer, an item it no longer has, or an asker it no longer lists reads as nothing. No transaction is open while
a source answers (H5), and nothing read is logged or stored."""
import logging

from . import confluence, jira, slack
from .claims import STOP, WORD, words
from .db import scoped

log = logging.getLogger(__name__)

SOURCES = {"jira": (jira.client_for, jira.live), "confluence": (confluence.client_for, confluence.live),
           "slack": (slack.client_for, slack.live)}
SEARCHES = {"jira": jira.search, "confluence": confluence.search}  # F11b: each live-only source's own search
LIVE_SQL = """
SELECT c.id, c.kind, c.config, c.credential_ref FROM connections c JOIN workspaces w ON w.id = c.workspace_id
WHERE c.config->>'storage' = 'live' AND c.kind = ANY(%(kinds)s) AND c.status <> 'disabled' AND w.slug = ANY(%(targets)s)
ORDER BY c.id
"""
MAX_WORDS = 8  # a question's words a source's search is asked for, its rarest-looking (longest) first
DOCS_SQL = """
SELECT d.external_key, d.storage, d.container_ref, c.id AS connection_id, c.kind, c.config, c.credential_ref
FROM documents d JOIN connections c ON c.id = d.connection_id
WHERE d.external_key = ANY(%s) AND d.source_kind = c.kind
"""
NOT_READABLE, UNAVAILABLE = "not readable", "unavailable"
PASSAGE_CHARS = 2400  # what one pointer's passage may hold past its best section: about an indexed chunk or two

Sections = list[tuple[str | None, str]]


def allowed(readers: list[str], principals: set[str]) -> bool:
    """Whether the source's readers, as it lists them now, include the asker."""
    return bool(set(readers) & principals)


def best(question: str, sections: Sections, budget: int = PASSAGE_CHARS) -> str | None:
    """A passage: the section sharing most of the question's words (the first on a tie, or with none shared), then the
    sections after it while they fit the budget, so a thread's question carries its replies."""
    asked, kept = words(question), [(h, b) for h, b in sections if (b or "").strip()]
    if not kept:
        return None
    start = max(range(len(kept)), key=lambda i: (len(asked & words(f"{kept[i][0] or ''} {kept[i][1]}")), -i))
    parts: list[str] = []
    for heading, body in kept[start:]:
        part = f"{heading}\n{body}" if heading else body
        if parts and sum(map(len, parts)) + len(part) > budget:
            break
        parts.append(part)
    return "\n\n".join(parts)


def search_words(question: str) -> list[str]:
    """What a source's own search is asked for: the question's content words as asked (its own search stems them),
    letters and digits only, the longest first."""
    asked = {w for w in WORD.findall(question.lower()) if len(w) > 2 and w not in STOP and len(w) <= 40}
    return sorted(asked, key=lambda w: (-len(w), w))[:MAX_WORDS]


def interleave(found: list[list]) -> list:
    """Each source's results in turn, best first, so no one source crowds out the others."""
    out: list = []
    for rank in range(max(map(len, found), default=0)):
        for keys in found:
            if rank < len(keys) and keys[rank] not in out:
                out.append(keys[rank])
    return out


def search(org_id: str, caller, question: str, targets: list[str]) -> list[tuple]:
    """F11b's runner: the documents each live-only connection in scope finds through its source's own search, run as the
    connection's account, best first, as (connection id, key): a key is matched only to that connection's own document,
    since two sites can hold the same one. Only keys come back, and every one still passes the caller's own gates
    (row-level security, the post-check, and at answer time the source's own readers); a source that fails gives
    nothing. Run with no transaction open."""
    asked = search_words(question)
    if not asked:
        return []
    with scoped(org_id, caller.principals) as conn:
        connections = conn.execute(LIVE_SQL, {"kinds": list(SEARCHES), "targets": targets}).fetchall()
    found = []
    for c in connections:
        try:
            found.append([(c["id"], key) for key in SEARCHES[c["kind"]](SOURCES[c["kind"]][0](c), c["config"] or {}, asked)])
        except Exception as e:  # noqa: BLE001 - a source that fails finds nothing; only its kind is logged
            log.warning("live search of connection %s failed (%s)", c["id"], type(e).__name__)
    return interleave(found)


def read(org_id: str, caller, keys: list[str]) -> dict[str, Sections | str]:
    """Each pointer document's sections as its source holds them now, for this caller; NOT_READABLE when the source doesn't
    list them among its readers now, UNAVAILABLE when it couldn't be read. A key that isn't a pointer is left out."""
    with scoped(org_id, caller.principals) as conn:  # what to read, and who the caller is in each source
        docs = conn.execute(DOCS_SQL, (keys,)).fetchall()
        mine = set(conn.execute("SELECT kl.current_principals() AS p").fetchone()["p"] or [])
    out: dict[str, Sections | str] = {}
    clients: dict = {}
    for d in docs:
        if d["storage"] not in ("pointer", "live") or d["kind"] not in SOURCES:
            continue
        make, fetch = SOURCES[d["kind"]]
        try:
            if d["connection_id"] not in clients:
                clients[d["connection_id"]] = make(d)
            readers, sections = fetch(clients[d["connection_id"]], d["config"] or {}, d)
        except Exception as e:  # noqa: BLE001 - any failure reads as nothing; only its kind is logged, never what was read
            log.warning("live read of %s failed (%s)", d["external_key"], type(e).__name__)
            out[d["external_key"]] = UNAVAILABLE
            continue
        out[d["external_key"]] = sections if allowed(readers, mine) else NOT_READABLE
    return out
