"""Decisions as their own items (Q5, K5): what was decided, who decided, when, and where it's recorded.

Connectors mark them as they read: a Jira comment that records a decision ("we decided", "decision:", "agreed to")
and an issue's resolution, and a Confluence page's decision items, each become a section headed
"Decision by <who> on <date>". `GET /v1/decisions` lists them from documents the caller may read, latest first; each
says what it replaced: the decision before it in the same document, or the latest one in a page a newer page
supersedes."""
import re

DECIDED = re.compile(r"\b(we decided|decided to|decided that|decision:|we agreed|agreed to|agreed that|we will go with|approved:)", re.I)
HEADING = re.compile(r"^Decision(?: by (?P<by>.+?))?(?: on (?P<at>\d{4}-\d{2}-\d{2}))?$")
DECISION_ITEM = re.compile(r'<ac:adf-node type="decision-item">(.*?)</ac:adf-node>', re.S)
TAG = re.compile(r"<[^>]+>")


def heading(by: str | None, at: str | None) -> str:
    return "Decision" + (f" by {by}" if by else "") + (f" on {at[:10]}" if at else "")


def is_decision(text: str) -> bool:
    return bool(DECIDED.search(text or ""))


def parse_heading(h: str | None) -> dict | None:
    """A decision section's heading as {"by", "at"}; None for any other section."""
    m = HEADING.fullmatch((h or "").strip())
    return {"by": m["by"], "at": m["at"]} if m else None


def confluence_items(storage: str) -> list[str]:
    """The text of a Confluence page's decision items (its storage format's decision list)."""
    return [" ".join(TAG.sub(" ", item).split()) for item in DECISION_ITEM.findall(storage or "") if TAG.sub("", item).strip()]


def ordered(rows: list[dict], supersedes: list[tuple[str, str]]) -> list[dict]:
    """Decisions latest first, each with what it replaced. rows: {"document", "text", "by", "at", ...}, all readable."""
    by_doc: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: (r["at"] or "", r.get("ordinal", 0))):
        by_doc.setdefault(r["document"]["id"], []).append(r)
    out = []
    for doc, items in by_doc.items():
        for i, r in enumerate(items):
            r = {**r, "replaces": None}
            if i:
                r["replaces"] = {"text": items[i - 1]["text"], "at": items[i - 1]["at"], "document": items[i - 1]["document"]}
            out.append(r)
    firsts = {doc: next(r for r in out if r["document"]["id"] == doc) for doc in by_doc}
    for newer, older in supersedes:  # a page another supersedes: its latest decision is what the newer page's first replaced
        if newer in firsts and older in by_doc and firsts[newer]["replaces"] is None:
            last = by_doc[older][-1]
            firsts[newer]["replaces"] = {"text": last["text"], "at": last["at"], "document": last["document"]}
    return sorted(out, key=lambda r: (r["at"] or "", r.get("ordinal", 0)), reverse=True)


if __name__ == "__main__":
    assert is_decision("We decided to cut over in Q3.") and not is_decision("Looks good to me.")
    assert parse_heading("Decision by Olivia on 2025-06-01") == {"by": "Olivia", "at": "2025-06-01"}
    assert parse_heading("Comment by Olivia on 2025-06-01") is None
    assert confluence_items('<ac:adf-node type="decision-item"><ac:adf-content>Go with <b>BigQuery</b></ac:adf-content></ac:adf-node>') == ["Go with BigQuery"]
    doc = {"id": "jira-X-1"}
    rows = [{"document": doc, "text": "Q2", "by": "O", "at": "2025-03-01"}, {"document": doc, "text": "Q3", "by": "O", "at": "2025-06-01"}]
    first = ordered(rows, [])[0]
    assert first["text"] == "Q3" and first["replaces"]["text"] == "Q2", first
    print("decisions: ok")
