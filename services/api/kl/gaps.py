"""The learning loop's first half (F5a, docs/ENTERPRISE_PLAN.md section 5; principle 7, no monitoring of people):
what people ask, and what isn't answered well, as gaps their teams' curators see.

Ask records each question it answers for a person (`record`): whether anything was found, its confidence, whether a
knowledge unit led, the asker's teams, and its text, unless the sensitive gate (one person's health, performance,
discipline, or pay) or personal-data detection flags it, when only the rest is kept. The asker's own feedback on that
answer ("helpful" or not) is added to their own row; nobody else's is ever read or changed by the API. Questions are
pruned after QUESTION_DAYS.

A question wasn't answered well when nothing was found, or its asker said it didn't help, or, with no word from them,
its confidence was low (`unanswered`). The worker's pass (`run`) groups the window's questions by their content words
(`group`: a question joins the first group sharing most of its words), and a group becomes a gap once at least
MIN_ASKERS different people asked it and weren't answered well; its wording is the one asked most often, and it's shown
only to curators of the teams whose people asked (`teams`), with how often and by how many, never by whom. A gap whose
latest ask was answered well is marked answered; a curator may dismiss one, and that sticks while it's asked."""
from __future__ import annotations

import logging
import re
from collections import Counter

from . import config, privacy
from .extraction import SENSITIVE
from .unit_answers import question_words

log = logging.getLogger("kl.gaps")

SIMILAR = 0.6  # a question joins a group sharing at least this share of the smaller one's words (and two of them)

RECORD_SQL = ("INSERT INTO questions (org_id, username, text, words, teams, found, confidence, unit_led) "
              "VALUES (kl.current_org(), %s, %s, %s, %s, %s, %s, %s) RETURNING id")
WINDOW_SQL = """SELECT id, username, text, words, teams, found, confidence, helpful, asked_at FROM questions
WHERE text IS NOT NULL AND asked_at > now() - make_interval(days => %s) ORDER BY asked_at, id"""
QUEUE_SQL = """INSERT INTO jobs (org_id, kind) SELECT kl.current_org(), 'gaps.refresh'
WHERE EXISTS (SELECT 1 FROM questions WHERE asked_at > coalesce(
        (SELECT max(created_at) FROM jobs WHERE kind = 'gaps.refresh' AND status = 'done'), '-infinity'))
AND NOT EXISTS (SELECT 1 FROM jobs WHERE kind = 'gaps.refresh' AND (status IN ('queued', 'running') OR created_at > now() - interval '1 minute'))"""


def kept_text(question: str) -> str | None:
    """A question's text as it may be kept: None when it names one person's sensitive matters or personal data."""
    if SENSITIVE.search(question) or privacy.detect("", [(None, question)])[1]:
        return None
    return " ".join(question.split())[:2000]


def record(conn, caller, question: str, found: bool, confidence: str | None, unit_led: bool, teams: set[str]) -> int | None:
    """Keep a person's question (a service account's are automation, never kept). Returns its id, for their feedback."""
    if caller.service:
        return None
    text = kept_text(question)
    words = sorted(question_words(question)) if text else []
    return conn.execute(RECORD_SQL, (caller.username, text, words, sorted(teams), found, confidence, unit_led)).fetchone()["id"]


def unanswered(row: dict) -> bool:
    if not row["found"] or row["helpful"] is False:
        return True
    return row["helpful"] is None and row["confidence"] == "low"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())[:200]


def stem(word: str) -> str:
    """A word's stem, lightly: "stored" and "store", "keys" and "key", meet; for grouping questions only."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[:-len(suffix)]
            break
    return word[:-1] if word.endswith("e") and len(word) > 3 else word


def similar(a: set[str], b: set[str]) -> bool:
    shared = a & b
    return len(shared) >= 2 and len(shared) / min(len(a), len(b)) >= SIMILAR


def group(rows: list[dict]) -> list[list[dict]]:
    """The questions in groups that ask the same thing: each joins the first group whose first question it resembles.
    ponytail: in memory, over the window; by meaning (embeddings) when wording varies more than words can follow."""
    groups: list[tuple[set[str], list[dict]]] = []
    for row in rows:
        words = {stem(w) for w in row["words"]}
        if not words:
            continue
        home = next((g for seed, g in groups if similar(seed, words)), None)
        if home is None:
            groups.append((words, [row]))
        else:
            home.append(row)
    return [g for _, g in groups]


def gap_of(rows: list[dict], min_askers: int) -> dict | None:
    """A group as a gap, or None when too few different people went unanswered."""
    missed = [r for r in rows if unanswered(r)]
    askers = {r["username"] for r in missed}
    if len(askers) < min_askers:
        return None
    wording = Counter(norm(r["text"]) for r in missed)
    question = next(r["text"] for r in missed if norm(r["text"]) == wording.most_common(1)[0][0])
    return {"key": norm(rows[0]["text"]), "question": question, "words": sorted(set(rows[0]["words"])), "asked": len(missed),
            "askers": len(askers), "teams": sorted({t for r in missed for t in r["teams"]}),
            "first_asked": missed[0]["asked_at"], "last_asked": rows[-1]["asked_at"], "answered": not unanswered(rows[-1])}


def run(conn) -> dict:
    """One pass: the window's gaps, kept by their key (a curator's dismissal sticks), the rest removed."""
    rows = conn.execute(WINDOW_SQL, (config.QUESTION_DAYS,)).fetchall()
    gaps = [g for g in (gap_of(rows_, config.GAP_MIN_ASKERS) for rows_ in group(rows)) if g]
    with conn.transaction():
        for g in gaps:
            conn.execute(
                """INSERT INTO knowledge_gaps (org_id, key, question, words, asked, askers, teams, state, first_asked, last_asked)
                   VALUES (kl.current_org(), %(key)s, %(question)s, %(words)s, %(asked)s, %(askers)s, %(teams)s, %(state)s,
                           %(first_asked)s, %(last_asked)s)
                   ON CONFLICT (org_id, key) DO UPDATE SET question = EXCLUDED.question, words = EXCLUDED.words,
                     asked = EXCLUDED.asked, askers = EXCLUDED.askers, teams = EXCLUDED.teams, last_asked = EXCLUDED.last_asked,
                     state = CASE WHEN knowledge_gaps.state = 'dismissed' THEN 'dismissed' ELSE EXCLUDED.state END, updated_at = now()""",
                {**g, "state": "answered" if g["answered"] else "open"})
        conn.execute("DELETE FROM knowledge_gaps WHERE NOT (key = ANY(%s))", ([g["key"] for g in gaps],))
    return {"questions": len(rows), "gaps": len(gaps)}


def prune(conn) -> int:
    """Retention: questions past the window go, and so does every gap built from them."""
    n = conn.execute("DELETE FROM questions WHERE asked_at < now() - make_interval(days => %s)", (config.QUESTION_DAYS,)).rowcount
    conn.execute("DELETE FROM knowledge_gaps WHERE last_asked < now() - make_interval(days => %s)", (config.QUESTION_DAYS,))
    return n
