"""Grow the golden set from real questions (slice M2b): `make eval-mine`.

In the app, people mark a search result right or wrong, say none of the results answered them, or rate an
answer (POST /v1/feedback). That feedback is kept in the audit log, and this turns it into candidate
golden cases, printed as YAML for a person to review: keep the good ones, add must_not where it matters,
paste them under `cases:` in golden.yaml, and raise its version.

- A source counts as a right answer when more people marked it right (or found an answer citing it
  helpful) than marked it wrong.
- Questions already in the golden set are skipped.
- Questions nothing answered are listed separately: they're content gaps to write, not test cases.

Nothing is written anywhere. Candidates carry real people's questions, so review them before committing,
and keep a customer's golden set in the customer's own repository.
"""
import json
import os
import sys
from collections import Counter

SMOKE = "[smoke]"  # the smoke suite's own feedback starts with this, and is left out of real runs


def norm(query: str) -> str:
    return " ".join(query.lower().split())


def events() -> list[dict]:
    """Every feedback event, oldest first. Run as the owner, like the rest of the eval tooling."""
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ["KL_OWNER_DATABASE_URL"], row_factory=dict_row) as conn:
        return conn.execute("SELECT actor, at, details FROM kl.audit_events "
                            "WHERE action = 'feedback' ORDER BY at").fetchall()


def candidates(feedback: list[dict], golden: dict, include_smoke: bool = False) -> tuple[list[dict], list[dict]]:
    """Returns (candidate cases with right answers, questions nothing answered), most recent first."""
    known = {(c["user"], norm(c["query"])) for c in golden["cases"]}
    groups: dict[tuple, dict] = {}
    for e in feedback:
        d = e["details"] or {}
        query = str(d.get("query") or "").strip()
        if not query or (query.startswith(SMOKE) and not include_smoke):
            continue
        key = (e["actor"], norm(query))
        if key in known:
            continue
        g = groups.setdefault(key, {"user": e["actor"], "query": query, "right": Counter(), "wrong": Counter(),
                                    "unanswered": 0, "last": e["at"]})
        verdict, result, cited = d.get("verdict"), d.get("result"), d.get("cited") or []
        if verdict == "right" and result:
            g["right"][result] += 1
        elif verdict == "wrong" and result:
            g["wrong"][result] += 1
        elif verdict == "helpful":
            g["right"].update(cited)
        elif verdict == "not_helpful":
            g["wrong"].update(cited)
            g["unanswered"] += 1
        elif verdict == "missing":
            g["unanswered"] += 1
        g["last"] = max(g["last"], e["at"])
    ready, gaps = [], []
    for g in sorted(groups.values(), key=lambda g: g["last"], reverse=True):
        relevant = [k for k, n in g["right"].most_common() if n > g["wrong"][k]]
        if relevant:
            ready.append({"user": g["user"], "query": g["query"], "relevant": relevant,
                          "votes": {k: g["right"][k] for k in relevant}})
        elif g["unanswered"]:
            gaps.append({"user": g["user"], "query": g["query"], "times": g["unanswered"]})
    return ready, gaps


def render(ready: list[dict], gaps: list[dict]) -> str:
    """Paste-ready YAML for the cases, and the gaps as comments."""
    lines = [f"# {len(ready)} candidate case(s) from feedback. Review each, add must_not where it matters, then",
             "# paste the ones you keep under `cases:` in kl/golden.yaml and raise its version."]
    for c in ready:
        votes = ", ".join(f"{k} {n}x" for k, n in c["votes"].items())
        lines += [f"  - user: {c['user']}", f"    query: {json.dumps(c['query'])}",
                  f"    relevant: [{', '.join(c['relevant'])}]  # marked right: {votes}"]
    if gaps:
        lines += ["", f"# {len(gaps)} question(s) nothing answered: content gaps to write, not test cases."]
        lines += [f"#   {g['user']}: {json.dumps(g['query'])} ({g['times']}x)" for g in gaps]
    return "\n".join(lines)


def main() -> int:
    from .eval import load
    ready, gaps = candidates(events(), load())
    print(render(ready, gaps) if ready or gaps else "# No feedback yet. Mark results right in the app, then run this again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
