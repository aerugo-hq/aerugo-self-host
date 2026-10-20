"""Pointer mode's live reads (F11a): a pointer document keeps no text, so its passages are read from its source when
someone asks, for that person only, and dropped once the answer is made.

Each read asks the source again who may read the item now, the way a sync works it out, and goes ahead only when the
asker is among them (their own principals and the source accounts linked to them), failing closed: a source that
doesn't answer, an item it no longer has, or an asker it no longer lists reads as nothing. No transaction is open while
a source answers (H5), and nothing read is logged or stored."""
import logging
from concurrent.futures import ThreadPoolExecutor, wait

from . import confluence, jira, slack, source_http
from .claims import STOP, WORD, words
from .db import scoped

log = logging.getLogger(__name__)

SOURCES = {"jira": (jira.client_for, jira.live), "confluence": (confluence.client_for, confluence.live),
           "slack": (slack.client_for, slack.live)}
SEARCHES = {"jira": jira.search, "confluence": confluence.search}  # F11b: each live-only source's own search
LIVE_SQL = """
SELECT c.id, c.kind, c.config, c.credential_ref FROM connections c JOIN workspaces w ON w.id = c.workspace_id
WHERE c.config->>'storage' = 'live' AND c.kind = ANY(%(kinds)s) AND c.status <> 'disabled' AND w.slug = ANY(%(targets)s)
ORDER BY w.slug, c.name  -- stable, so a question ranks the same on every stack
"""
MAX_WORDS = 8  # a question's words a source's search is asked for, its rarest-looking (longest) first
SEARCH_SECONDS = 8.0  # every live-only source in scope is asked at once, and none is waited for longer
SCOPE = {"jira": "projects", "confluence": "spaces"}  # what a source's search is kept within
# The containers of each live-only connection where the asker opens at least one document (row-level security decides):
# a source is searched only there, so an item the asker can't open never takes a result's place outside them.
VISIBLE_SQL = """
SELECT DISTINCT d.connection_id, d.container_ref FROM documents d
JOIN workspace_documents wd ON wd.document_id = d.id
JOIN workspaces w           ON w.id = wd.workspace_id
WHERE d.storage = 'live' AND d.connection_id = ANY(%(connections)s) AND w.slug = ANY(%(targets)s)
"""
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


def search(org_id: str, caller, question: str, targets: list[str]) -> dict:
    """F11b's runner: {connection id: keys, best first} that each live-only connection in scope finds through its
    source's own search, run as the connection's account, within only the projects or spaces where the caller already
    opens something (a connection where they open nothing isn't asked). Every source is asked at once, each call tried
    once and briefly (`source_http.answering`), and one that fails or takes longer than SEARCH_SECONDS gives nothing.
    The keys still pass every gate of the caller's own (`merge` comes after them). Run with no transaction open."""
    asked = search_words(question)
    if not asked:
        return {}
    seen: dict = {}
    with scoped(org_id, caller.principals) as conn:
        connections = conn.execute(LIVE_SQL, {"kinds": list(SEARCHES), "targets": targets}).fetchall()
        if connections:
            for r in conn.execute(VISIBLE_SQL, {"connections": [c["id"] for c in connections], "targets": targets}).fetchall():
                seen.setdefault(r["connection_id"], []).append(r["container_ref"])
    wanted = [c for c in connections if c["id"] in seen]
    if not wanted:
        return {}

    def ask(c) -> list[str]:
        with source_http.answering():
            cfg = {**(c["config"] or {}), SCOPE[c["kind"]]: sorted(seen[c["id"]])}
            return SEARCHES[c["kind"]](SOURCES[c["kind"]][0](c), cfg, asked)

    pool = ThreadPoolExecutor(max_workers=len(wanted))
    futures = {c["id"]: pool.submit(ask, c) for c in wanted}
    done, _ = wait(futures.values(), timeout=SEARCH_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)
    found = {}
    for cid, future in futures.items():  # in LIVE_SQL's order, so a run is the same on every stack
        if future not in done:
            log.warning("live search of connection %s took longer than %ss", cid, SEARCH_SECONDS)
            continue
        try:
            found[cid] = future.result()
        except Exception as e:  # noqa: BLE001 - a source that fails finds nothing; only its kind is logged
            log.warning("live search of connection %s failed (%s)", cid, type(e).__name__)
    return found


def merge(found: dict, rows: list[dict]) -> list[list[dict]]:
    """The rows the caller may see (SOURCE_SQL's), one list per source in its own order, for fusion to take each as a
    channel of its own, so every source's best hit counts alike, whatever the order the sources are listed in. Hidden
    keys are gone before anything is ordered, so an item's place, and its score, depend only on what the caller sees,
    never on how many items they can't open a source ranked above it (invariant 11)."""
    by = {(r["connection_id"], r["external_key"]): r for r in rows}
    groups = ([by[(cid, key)] for key in keys if (cid, key) in by] for cid, keys in found.items())
    return [rows for rows in groups if rows]


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
            with source_http.answering():  # someone is waiting: one brief try per call, never a throttle's wait
                if d["connection_id"] not in clients:
                    clients[d["connection_id"]] = make(d)
                readers, sections = fetch(clients[d["connection_id"]], d["config"] or {}, d)
        except Exception as e:  # noqa: BLE001 - any failure reads as nothing; only its kind is logged, never what was read
            log.warning("live read of %s failed (%s)", d["external_key"], type(e).__name__)
            out[d["external_key"]] = UNAVAILABLE
            continue
        out[d["external_key"]] = sections if allowed(readers, mine) else NOT_READABLE
    return out
