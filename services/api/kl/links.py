"""Automatic links on the map (slice N): semantic neighbors and entities, computed by the worker right after
indexing, so the map shows how knowledge connects without anyone curating it.

Semantic neighbors: a document's vector is the mean of its chunks' vectors, per embedding model. Two
documents are linked when each is among the other's three nearest (mutual nearest neighbors) and their
cosine similarity is at least 0.6. Mutual neighbors stay sparse and meaningful on any corpus and model,
where one fixed similarity threshold would link everything on one model and nothing on another.

Entities: people, systems, teams, and channels named in the text, found by conservative patterns, so no
model reads anything (nothing leaves the cell, nothing costs tokens) and precision wins over recall:
- systems: identifiers in code spans (`ledger-db`), service names with a service-like suffix
  (payments-api), and "<name> API / service / database / ..." phrases;
- teams: "<name> team / on-call / squad", and Slack-style #channels;
- people: @handles, and full names after "Owner:", "Contact:", "Maintainer:", or "Approver:".
Fenced code blocks are skipped, so a decorator like @app.get is never a person.

Visibility, enforced by RLS and again by the map: an edge is readable only when both of its documents are,
a mention only when its document is, and an entity only when one of its mentions is. An entity named only
in documents you can't see doesn't exist for you.
"""
import os
import re
from collections import Counter

TOP_K = int(os.environ.get("KL_LINKS_TOP_K", "3"))
MIN_SCORE = float(os.environ.get("KL_LINKS_MIN_SCORE", "0.6"))

SUFFIXES = ("api|db|database|svc|service|server|queue|cache|worker|gateway|cluster|bucket|topic|stream|store|"
            "index|pipeline|cron|job|bot|cli|ui|app|proxy|broker|registry")
FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
CODE_SPAN = re.compile(r"`([A-Za-z][A-Za-z0-9._-]{2,40})`")
SERVICE_NAME = re.compile(rf"(?<![\w./-])([a-z][a-z0-9]*(?:-[a-z0-9]+)*-(?:{SUFFIXES}))(?![\w-])")
SYSTEM_PHRASE = re.compile(r"\b([A-Za-z][a-z0-9]{2,})\s(API|service|database|cluster|queue|gateway|pipeline)\b")
TEAM_PHRASE = re.compile(r"\b([A-Za-z][a-z0-9]{2,})\s(team|on-call|oncall|squad|guild)\b")
CHANNEL = re.compile(r"(?<![\w#&/])#([a-z][a-z0-9_-]{1,40})(?![\w-])")
HANDLE = re.compile(r"(?<![\w@./])@([a-z0-9][a-z0-9_-]{1,38})(?![\w.(-])", re.I)
OWNER = re.compile(r"\b(?:[Oo]wners?|[Cc]ontacts?|[Mm]aintainers?|[Aa]pprovers?)\s*:\s*([A-Z][a-z]+(?:\s[A-Z][a-z]+){1,2})\b")
STOP = {"the", "a", "an", "this", "that", "these", "those", "our", "your", "their", "its", "his", "her", "each",
        "every", "any", "no", "one", "same", "other", "another", "new", "old", "main", "whole", "entire", "public",
        "private", "rest", "http", "web", "external", "internal", "which", "what", "whose", "same", "my"}
FILE_LIKE = re.compile(r"\.(py|js|ts|tsx|jsx|md|ya?ml|json|sql|toml|txt|sh|cfg|ini|lock|env|html|css)$", re.I)


def extract(text: str) -> dict[tuple[str, str], tuple[str, int]]:
    """Entities named in a document: {(kind, key): (name as most often written, mentions)}."""
    text = FENCE.sub(" ", text)
    names: dict[tuple[str, str], Counter] = {}

    def add(kind: str, key: str, name: str) -> None:
        names.setdefault((kind, key), Counter())[name] += 1

    for span in CODE_SPAN.findall(text):
        separated = any(c in span for c in "-_.") and not FILE_LIKE.search(span) and not span.isupper()
        camel = re.fullmatch(r"[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+", span) is not None  # OpenBao, PostgreSQL-ish
        if (separated or camel) and not span.startswith((".", "_")) and not span.endswith((".", "_", "-")):
            add("system", span.lower(), span)
    for name in SERVICE_NAME.findall(FENCE.sub(" ", CODE_SPAN.sub(" ", text))):
        add("system", name, name)
    for word, kind in SYSTEM_PHRASE.findall(text):
        if word.lower() not in STOP:
            add("system", f"{word.lower()} {kind.lower()}", f"{word.lower()} {kind}")
    for word, kind in TEAM_PHRASE.findall(text):
        if word.lower() not in STOP:
            kind = "on-call" if kind.lower() in ("on-call", "oncall") else kind.lower()
            add("team", f"{word.lower()} {kind}", f"{word.lower()} {kind}")
    for channel in CHANNEL.findall(text):
        add("channel", f"#{channel.lower()}", f"#{channel.lower()}")
    for handle in HANDLE.findall(text):
        add("person", f"@{handle.lower()}", f"@{handle.lower()}")
    for full in OWNER.findall(text):
        add("person", full.lower(), full)
    return {key: (counts.most_common(1)[0][0], sum(counts.values())) for key, counts in names.items()}


# ------------------------------------------------------------------ the worker's passes (run as kl_ingest, in one org)
VECTORS_SQL = """
WITH embedded AS (
  SELECT c.document_id, e.model_id, count(*) AS chunks, avg(e.embedding) AS v
  FROM chunks c JOIN chunk_embeddings e ON e.chunk_id = c.id
  GROUP BY c.document_id, e.model_id
), totals AS (
  SELECT document_id, count(*) AS chunks FROM chunks GROUP BY document_id
)
INSERT INTO document_vectors (org_id, document_id, model_id, content_hash, embedding)
SELECT d.org_id, d.id, x.model_id, d.content_hash, x.v
FROM documents d
JOIN totals t   ON t.document_id = d.id
JOIN embedded x ON x.document_id = d.id AND x.chunks = t.chunks  -- every chunk embedded by this model
LEFT JOIN document_vectors dv ON dv.document_id = d.id AND dv.model_id = x.model_id
WHERE dv.document_id IS NULL OR dv.content_hash IS DISTINCT FROM d.content_hash
ON CONFLICT (document_id, model_id) DO UPDATE
  SET content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding, computed_at = now()
RETURNING model_id
"""
# Mutual nearest neighbors: each document's top K by cosine distance, kept where the other side agrees.
NEIGHBORS_SQL = """
WITH knn AS (
  SELECT a.org_id, a.document_id AS a, nb.document_id AS b, nb.score
  FROM document_vectors a
  CROSS JOIN LATERAL (
    SELECT b.document_id, 1 - (a.embedding <=> b.embedding) AS score
    FROM document_vectors b
    WHERE b.model_id = a.model_id AND b.document_id <> a.document_id
    ORDER BY a.embedding <=> b.embedding
    LIMIT %(k)s
  ) nb
  WHERE a.model_id = %(model)s
)
INSERT INTO semantic_edges (org_id, source_id, target_id, model_id, score)
SELECT x.org_id, x.a, x.b, %(model)s, x.score
FROM knn x JOIN knn y ON y.a = x.b AND y.b = x.a
WHERE x.a < x.b AND x.score >= %(min_score)s
"""
DUE_SCANS_SQL = """
SELECT d.id, d.org_id, d.content_hash, d.title
FROM documents d LEFT JOIN entity_scans s ON s.document_id = d.id
WHERE s.document_id IS NULL OR s.content_hash IS DISTINCT FROM d.content_hash
ORDER BY d.id
LIMIT 200
"""


def refresh_semantic(conn) -> int:
    """Recompute document vectors that are missing or stale, then the neighbor edges of every model whose
    vectors changed. Returns how many edges the changed models now have."""
    with conn.transaction():
        changed = {r["model_id"] for r in conn.execute(VECTORS_SQL).fetchall()}
    edges = 0
    for model_id in changed:
        with conn.transaction():
            conn.execute("DELETE FROM semantic_edges WHERE model_id = %s", (model_id,))
            edges += conn.execute(NEIGHBORS_SQL, {"k": TOP_K, "model": model_id, "min_score": MIN_SCORE}).rowcount
    return edges


def scan_entities(conn) -> int:
    """Extract entities from documents that are new or changed since their last scan. Returns documents scanned."""
    scanned = 0
    while True:
        docs = conn.execute(DUE_SCANS_SQL).fetchall()
        if not docs:
            return scanned
        for d in docs:
            parts = [d["title"] or ""] + [f"{c['heading_path'] or ''}\n{c['body']}" for c in conn.execute(
                "SELECT heading_path, body FROM chunks WHERE document_id = %s ORDER BY ordinal", (d["id"],)).fetchall()]
            found = extract("\n".join(parts))
            with conn.transaction():
                conn.execute("DELETE FROM entity_mentions WHERE document_id = %s", (d["id"],))  # orphans go by trigger
                for (kind, key), (name, mentions) in found.items():
                    entity = conn.execute(
                        """INSERT INTO entities (org_id, kind, key, name) VALUES (%s, %s, %s, %s)
                           ON CONFLICT (org_id, kind, key) DO UPDATE SET name = entities.name RETURNING id""",
                        (d["org_id"], kind, key, name)).fetchone()["id"]
                    conn.execute("INSERT INTO entity_mentions (entity_id, document_id, org_id, mentions) VALUES (%s, %s, %s, %s)",
                                 (entity, d["id"], d["org_id"], mentions))
                conn.execute(
                    """INSERT INTO entity_scans (document_id, org_id, content_hash) VALUES (%s, %s, %s)
                       ON CONFLICT (document_id) DO UPDATE SET content_hash = EXCLUDED.content_hash, scanned_at = now()""",
                    (d["id"], d["org_id"], d["content_hash"]))
            scanned += 1


def refresh(conn) -> dict:
    """Both passes, after the worker has embedded what it could. Cheap when nothing changed."""
    return {"entities_scanned": scan_entities(conn), "semantic_edges": refresh_semantic(conn)}
