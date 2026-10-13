"""Stored originals, encrypted per team (slice I3b).

Concept versions and proposal drafts are the platform's own copies of knowledge. Each is encrypted with a
Transit key in OpenBao before it's stored: its team's key, or the org's key for a document several teams hold,
so purging one team never destroys what another still holds. A row keeps the name of its key (key_ref) beside
the ciphertext; a row from before this slice has none until the worker encrypts it. Search indexes (chunks and
embeddings) stay plaintext so search works, and backups holding them expire with retention.

Purging a team re-wraps its originals that outlive it (a shared document's history) under the org key, then
destroys the team's key, so every copy encrypted with it, in any backup, is unreadable from then on.
"""
from . import config
from .vault import vault

_READY: set[str] = set()  # keys this process has made sure exist


def team_key(workspace_id) -> str:
    return f"kl-{config.ORG_SLUG}-{workspace_id}"


def org_key() -> str:
    return f"kl-{config.ORG_SLUG}-org"


def _ensure(name: str) -> str:
    if name not in _READY:
        vault.transit_key(name)
        _READY.add(name)
    return name


def seal(text: str, key: str) -> str:
    return vault.encrypt(_ensure(key), text)


def open_one(content: str, key_ref: str | None) -> str:
    """A stored original's text. A row from before encryption (no key_ref) is returned as it is."""
    return content if key_ref is None else vault.decrypt(key_ref, content)


def open_many(rows: list[tuple[str, str | None]]) -> list[str]:
    """The text of many (content, key_ref) pairs, in order, with one Transit call per key."""
    out: list[str] = [""] * len(rows)
    by_key: dict[str, list[int]] = {}
    for i, (content, key) in enumerate(rows):
        if key is None:
            out[i] = content
        else:
            by_key.setdefault(key, []).append(i)
    for key, indexes in by_key.items():
        for i, text in zip(indexes, vault.decrypt_many(key, [rows[i][0] for i in indexes])):
            out[i] = text
    return out


def key_for_document(conn, document_id) -> str:
    """The key for a document's originals: its team's when exactly one team holds it, the org's otherwise."""
    teams = [r["workspace_id"] for r in conn.execute(
        "SELECT workspace_id FROM workspace_documents WHERE document_id = %s", (document_id,)).fetchall()]
    return team_key(teams[0]) if len(teams) == 1 else org_key()


def rewrap_survivors(conn, workspace_id) -> int:
    """Before a team's key is destroyed: re-encrypt under the org key every original of the team's that outlives
    it, meaning the history of a document another team also holds. Returns how many rows were re-wrapped."""
    key = team_key(workspace_id)
    rows = conn.execute(
        """SELECT v.concept_id, v.hash, v.content FROM concept_versions v JOIN concepts k ON k.id = v.concept_id
           WHERE v.key_ref = %s AND k.document_id IS NOT NULL
             AND EXISTS (SELECT 1 FROM workspace_documents wd
                         WHERE wd.document_id = k.document_id AND wd.workspace_id <> %s)""",
        (key, workspace_id)).fetchall()
    for row, text in zip(rows, vault.decrypt_many(key, [r["content"] for r in rows])):
        conn.execute("UPDATE concept_versions SET content = %s, key_ref = %s WHERE concept_id = %s AND hash = %s",
                     (seal(text, org_key()), org_key(), row["concept_id"], row["hash"]))
    return len(rows)


def encrypt_legacy(conn, limit: int = 200) -> int:
    """Encrypt a batch of originals stored before this slice, in plaintext. The worker runs this on every tick
    until none are left. Returns how many rows it encrypted."""
    done = 0
    for r in conn.execute(
            """SELECT v.concept_id, v.hash, v.content, k.document_id FROM concept_versions v
               JOIN concepts k ON k.id = v.concept_id WHERE v.key_ref IS NULL LIMIT %s""", (limit,)).fetchall():
        key = key_for_document(conn, r["document_id"]) if r["document_id"] else org_key()
        conn.execute("UPDATE concept_versions SET content = %s, key_ref = %s "
                     "WHERE concept_id = %s AND hash = %s AND key_ref IS NULL",
                     (seal(r["content"], key), key, r["concept_id"], r["hash"]))
        done += 1
    for r in conn.execute("SELECT id, workspace_id, content FROM proposals WHERE key_ref IS NULL LIMIT %s",
                          (limit,)).fetchall():
        key = team_key(r["workspace_id"])
        conn.execute("UPDATE proposals SET content = %s, key_ref = %s WHERE id = %s AND key_ref IS NULL",
                     (seal(r["content"], key), key, r["id"]))
        done += 1
    return done
