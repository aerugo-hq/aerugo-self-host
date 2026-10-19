"""Extraction's quality on the synthetic retailer (F2): a real model, run locally (`make llm`: Ollama in the stack, so
nothing sent to it leaves the machine), proposes units from every team's pages and tickets, and they're scored against
the truth's facts:

- recall: the facts some single proposal states (at least half of the fact's content words), from one of the fact's
  own pages or tickets;
- precision: the proposals that are part of some fact (at least half of their content words in it, from one of its
  pages or tickets); a lower bound, since the truth doesn't list every true statement a page makes;
- and what the checks dropped (unsupported, sensitive, invalid), and the tokens spent.

Reported, never gated: a model's quality is the model's, and CI's fake model measures the pipeline instead. Chat
threads aren't extracted (F2 waits for C1 and C2's opt-in), so facts found only in chat aren't counted.

    make llm                # the local model, once
    make eval-extraction
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter

import httpx
import psycopg
from psycopg.rows import dict_row

from . import claims, company, company_eval, local_llm
from .extraction import CHAT_SOURCES
from .smoke import OWNER_DB, api, api_ready

ADMIN = company_eval.ADMIN
MODEL = {"name": "local", "provider": "openai_compatible", "model": local_llm.NAME, "base_url": f"{local_llm.OLLAMA}/v1",
         "api_key": "ollama", "allowed_classifications": ["public", "internal", "confidential"]}  # nothing leaves the machine
WAIT_MINUTES = float(os.environ.get("KL_EXTRACTION_EVAL_MINUTES", "180"))
PAGES = ("confluence", "jira")  # the retailer's pages and tickets
PROGRESS_SQL = """
SELECT count(*) FILTER (WHERE s.content_hash = d.content_hash AND s.model_id = m.id) AS read, count(*) AS of
FROM kl.workspace_documents wd JOIN kl.workspaces w ON w.id = wd.workspace_id JOIN kl.documents d ON d.id = wd.document_id
JOIN kl.model_endpoints m ON m.workspace_id = w.id AND m.name = 'local'
LEFT JOIN kl.extraction_state s ON s.workspace_id = w.id AND s.document_id = d.id
WHERE w.slug = ANY(%s) AND d.source_kind <> ALL(%s)
"""
PROPOSALS_SQL = """
SELECT u.kind, u.body, d.external_key FROM kl.knowledge_units u JOIN kl.documents d ON d.id = u.evidence[1]
JOIN kl.workspaces w ON w.id = u.owner_team WHERE u.created_by = 'extraction' AND u.status = 'proposed' AND w.slug = ANY(%s)
"""
DROPS_SQL = """
SELECT coalesce(sum(s.unsupported), 0) AS unsupported, coalesce(sum(s.sensitive), 0) AS sensitive, coalesce(sum(s.invalid), 0) AS invalid,
       coalesce(sum(s.tokens), 0) AS tokens, count(*) FILTER (WHERE s.outcome = 'failed') AS failed
FROM kl.extraction_state s JOIN kl.workspaces w ON w.id = s.workspace_id WHERE w.slug = ANY(%s)
"""


def opt_in(teams: list[str]) -> None:
    """The local model in each team's registry, and the team opted in to extraction with it."""
    for team in teams:
        r = api("POST", f"/v1/workspaces/{team}/models", ADMIN, json=MODEL)
        if r.status_code == 409:
            r = api("PATCH", f"/v1/workspaces/{team}/models/local", ADMIN, json={k: MODEL[k] for k in ("model", "base_url", "allowed_classifications")})
        if r.status_code not in (200, 201):
            raise SystemExit(f"the local model for {team}: {r.status_code} {r.text[:200]}")
        r = api("PUT", f"/v1/workspaces/{team}/extraction", ADMIN, json={"model": "local"})
        if r.status_code != 200:
            raise SystemExit(f"extraction for {team}: {r.status_code} {r.text[:200]}")


def wait(teams: list[str]) -> float:
    started, deadline = time.monotonic(), time.monotonic() + WAIT_MINUTES * 60
    with psycopg.connect(OWNER_DB, row_factory=dict_row, autocommit=True) as conn:
        while True:
            done = conn.execute(PROGRESS_SQL, (teams, list(CHAT_SOURCES))).fetchone()
            print(f"  {done['read']} of {done['of']} pages and tickets read, {(time.monotonic() - started) / 60:.0f} minutes in", flush=True)
            if done["read"] == done["of"]:
                return time.monotonic() - started
            if time.monotonic() > deadline:
                raise SystemExit(f"extraction didn't finish in {WAIT_MINUTES:g} minutes (KL_EXTRACTION_EVAL_MINUTES)")
            time.sleep(30)


def states(fact_words: set[str], proposal: str) -> float:
    return claims.support(fact_words, proposal)


def score(world: company.World, teams: list[str], minutes: float) -> None:
    keys = {d.name: d.key for d in world.docs}
    pages = {d.name for d in world.docs if d.source in PAGES}
    facts = [f for f in world.facts if f.status != "sensitive" and set(f.evidence) & pages]
    with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
        proposals = conn.execute(PROPOSALS_SQL, (teams,)).fetchall()
        drops = conn.execute(DROPS_SQL, (teams,)).fetchone()
    found, kinds, part_of = [], Counter(), set()
    print(f"\nThe local model ({local_llm.NAME}) against the retailer's {len(facts)} facts with a page or ticket\n")
    for f in facts:
        mine = {keys[e] for e in f.evidence if e in pages}
        words = claims.words(f.text)
        best = max(((states(words, p["body"]), i) for i, p in enumerate(proposals) if p["external_key"] in mine), default=(0.0, None))
        for i, p in enumerate(proposals):
            if p["external_key"] in mine and claims.support(claims.words(p["body"]), f.text) >= 0.5:
                part_of.add(i)
        ok = best[0] >= 0.5
        if ok:
            found.append(f.id)
            kinds["same kind" if proposals[best[1]]["kind"] == f.kind else "another kind"] += 1
        print(f"  {'ok' if ok else 'miss':4}  {f.id:22} {f.kind:11} {best[0]:.2f}  {proposals[best[1]]['body'][:70] if best[1] is not None else ''}")
    print(f"\n  Recall     {len(found)} of {len(facts)} facts ({len(found) / len(facts):.0%}), {kinds['same kind']} of them as the same kind")
    print(f"  Precision  {len(part_of)} of {len(proposals)} proposals are part of a fact ({len(part_of) / max(len(proposals), 1):.0%}), a lower bound")
    print(f"  Dropped    {drops['unsupported']} unsupported, {drops['sensitive']} sensitive, {drops['invalid']} invalid; "
          f"{drops['failed']} documents failed; {drops['tokens']:,} tokens; {minutes / 60:.0f} minutes")


def main() -> int:
    world = company.build()
    api_ready()
    try:
        httpx.get(f"{local_llm.OLLAMA}/api/version", timeout=5).raise_for_status()
    except httpx.HTTPError:
        raise SystemExit("the local model isn't running: make llm first")
    company_eval.load(world)
    teams = sorted({world.container(d.source, d.container).team for d in world.docs if d.source in PAGES})
    opt_in(teams)
    score(world, teams, wait(teams))
    return 0


if __name__ == "__main__":
    sys.exit(main())
