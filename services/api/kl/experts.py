"""Routing a question to an expert, and keeping their answer (F5b, docs/ENTERPRISE_PLAN.md section 6, ask-time capture).

When Ask doesn't answer, the asker may send the question to a likely expert: someone who-to-ask suggests about a page
the asker can read (never anyone at all, so it can't be used to message the whole company). Only the two of them see
it, in their inboxes in the app; nothing is sent outside it. When replying, the expert chooses who may read the reply
as knowledge (decided 2026-10-20): the asker only (nothing kept), their own team (the default), or the whole company. A
kept reply becomes a document with exactly that audience (its team's own members and groups, never a grant to the
whole company, or the company's members) and a proposed unit for the team's curators to confirm; a question or reply
that names one person's sensitive matters or personal data can't be kept (the question becomes the page's title). The expert's own words, shared by their own choice: not
synced content, so invariant 12 (a source's own permissions) doesn't reach it."""
from __future__ import annotations

import hashlib
import logging

from . import notion, privacy
from .authz import fga
from .extraction import SENSITIVE
from .markdown import parse as parse_markdown
from .pipelines import viewer_tuples

log = logging.getLogger("kl.experts")

AUDIENCES = ("asker", "team", "org")
ROUTED_SQL = """SELECT r.id::text AS id, r.created_by, r.expert, r.question, r.reply, r.audience, r.keep_team, r.replied_at,
       r.kept_document, w.id AS team_id, o.slug AS org
FROM routed_questions r JOIN workspaces w ON w.slug = r.keep_team AND w.org_id = r.org_id JOIN orgs o ON o.id = r.org_id
WHERE r.id = %s"""
DOCUMENT_SQL = """
INSERT INTO documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash, classification,
                       acl_principals, container_ref, source_path)
VALUES (kl.current_org(), 'answer', %s, %s, %s, %s, %s, 'internal', %s, %s, NULL)
ON CONFLICT (org_id, external_key) DO NOTHING RETURNING id
"""


def keepable(reply: str) -> bool:
    """Whether a reply may become knowledge: not when it names one person's sensitive matters or personal data."""
    return not (SENSITIVE.search(reply) or privacy.detect("", [(None, reply)])[1])


def readers_for(audience: str, team: str, org: str) -> list[str]:
    """Who may read a kept reply: the team's own members and groups (never a grant to the whole company), or everyone in
    the company."""
    if audience == "org":
        return [f"org:{org}"]
    return notion.team_readers(team)


def keep(conn, routed_id: str) -> dict:
    """The worker's step: a reply its expert chose to keep, as a document with its audience and a proposed unit. Run once:
    a question already kept is left as it is."""
    row = conn.execute(ROUTED_SQL, (routed_id,)).fetchone()
    if not row or row["audience"] not in ("team", "org") or not row["reply"] or row["kept_document"]:
        return {"kept": 0}
    if not (keepable(row["reply"]) and keepable(row["question"])):  # the question is the kept page's title: both are screened
        return {"kept": 0, "refused": "sensitive"}
    readers = readers_for(row["audience"], row["keep_team"], row["org"])
    if not readers:
        return {"kept": 0, "refused": "no readers"}
    key, day = f"answer-{routed_id}", row["replied_at"].date().isoformat()
    markdown = f"# {row['question']}\n\n{row['reply']}\n\nAnswered by {row['expert']} on {day}."
    with conn.transaction():
        doc = conn.execute(DOCUMENT_SQL, (f"aerugo://answers/{routed_id}", row["replied_at"].isoformat(), key,
                                          f"Answer: {row['question']}"[:300], hashlib.sha256(markdown.encode()).hexdigest(),
                                          readers, f"answers:{row['keep_team']}")).fetchone()
        if not doc:
            return {"kept": 0}
        _, sections = parse_markdown(markdown, fallback_title=row["question"][:200])
        for ordinal, (heading, body) in enumerate(sections):
            conn.execute("INSERT INTO chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                         "VALUES (kl.current_org(), %s, %s, %s, %s, %s)", (doc["id"], ordinal, heading, body, readers))
        conn.execute("INSERT INTO workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, kl.current_org()) "
                     "ON CONFLICT DO NOTHING", (row["team_id"], doc["id"]))
        unit = conn.execute(
            "INSERT INTO knowledge_units (org_id, owner_team, kind, topic, title, body, trust, status, evidence, sections, created_by) "
            "VALUES (kl.current_org(), %s, 'answer', %s, %s, %s, 'documented', 'proposed', %s::uuid[], %s, %s) RETURNING id",
            (row["team_id"], f"answer-{routed_id[:8]}", row["question"][:200], row["reply"], [doc["id"]], [""], row["expert"])).fetchone()
        conn.execute("UPDATE routed_questions SET kept_document = %s, kept_unit = %s WHERE id = %s", (doc["id"], unit["id"], routed_id))
    fga.write(writes=viewer_tuples(key, readers))
    log.info("kept an expert's answer for %s (%s)", row["keep_team"], row["audience"])
    return {"kept": 1, "audience": row["audience"]}
