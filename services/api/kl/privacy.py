"""Personal-data detection (slice I5): documents that look like they hold personal data are flagged for their team's
admins to review, with a suggested classification. Found by conservative patterns, as entities are, so no model
reads anything. A flag records which kinds of personal data were found and how many of each, never the values, so
the detector can't become a copy of what it finds.

Flagged, with the classification suggested:
- restricted: government ID numbers (US Social Security numbers), payment card numbers (Luhn-checked, the same
  check knowledge CI uses);
- confidential: a résumé (three or more of its usual sections, plus an email address or phone number), a date of
  birth, or HR material (two or more of salary, performance review, disciplinary, termination, medical leave, home
  address, emergency contact, passport number).
An email address or phone number alone is no flag: every runbook has an on-call contact. A document already
classified at least as strictly as the suggestion isn't flagged.
"""
import json
import re

from .okf import _CARD, _luhn

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")
PHONE = re.compile(r"(?<![\w+])(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}(?![\w-])"
                   r"|(?<![\w+])\+\d{1,3}(?:[ .-]\d{2,4}){2,4}(?![\w-])")
SSN = re.compile(r"(?<![\d-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d-])")
DOB = re.compile(r"\b(?:date of birth|DOB|born on)\b", re.I)
HR = re.compile(r"\b(salary|compensation package|performance review|disciplinary|termination|medical leave|"
                r"home address|emergency contact|passport (?:number|no))\b", re.I)
RESUME = {"experience", "work experience", "professional experience", "employment history", "education", "skills",
          "technical skills", "objective", "summary", "professional summary", "certifications", "projects",
          "references", "publications"}
RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


def detect(title: str, sections: list[tuple[str | None, str]]) -> tuple[dict, str | None]:
    """(how many distinct matches of each kind, the suggested classification or None)."""
    text = "\n".join([title or ""] + [f"{heading or ''}\n{body}" for heading, body in sections])
    kinds: dict[str, int] = {}

    def count(kind: str, values) -> None:
        distinct = {v.strip().lower() for v in values}
        if distinct:
            kinds[kind] = len(distinct)

    count("email", EMAIL.findall(text))
    count("phone", PHONE.findall(text))
    count("government_id", SSN.findall(text))
    count("payment_card", [m.group() for m in _CARD.finditer(text) if _luhn(re.sub(r"\D", "", m.group()))])
    count("date_of_birth", DOB.findall(text))
    count("hr_terms", [m.group(1) for m in HR.finditer(text)])
    headings = {(heading or "").split(">")[-1].strip().lower() for heading, _ in sections}
    headings |= {line.strip().strip("#:").strip().lower() for line in text.splitlines() if 0 < len(line.strip()) < 40}
    sections_found = len(RESUME & headings)
    if sections_found >= 3 and ("email" in kinds or "phone" in kinds):
        kinds["resume_layout"] = sections_found
    if "government_id" in kinds or "payment_card" in kinds:
        return kinds, "restricted"
    if "resume_layout" in kinds or "date_of_birth" in kinds or kinds.get("hr_terms", 0) >= 2:
        return kinds, "confidential"
    return kinds, None


# ------------------------------------------------------------------ the worker's pass (run as kl_ingest, in one org)
DUE_SQL = """
SELECT d.id, d.org_id, d.title, d.content_hash, d.classification, f.status, f.kinds
FROM documents d LEFT JOIN privacy_flags f ON f.document_id = d.id
WHERE f.document_id IS NULL OR f.content_hash IS DISTINCT FROM d.content_hash
ORDER BY d.id
LIMIT 200
"""
UPSERT_SQL = """
INSERT INTO privacy_flags (document_id, org_id, content_hash, kinds, suggested, status)
VALUES (%s, %s, %s, %s::jsonb, %s, %s)
ON CONFLICT (document_id) DO UPDATE SET
  content_hash = EXCLUDED.content_hash, kinds = EXCLUDED.kinds, suggested = EXCLUDED.suggested,
  status = EXCLUDED.status, scanned_at = now(),
  resolved_by = CASE WHEN EXCLUDED.status IN ('dismissed', 'reclassified') THEN privacy_flags.resolved_by END,
  resolved_at = CASE WHEN EXCLUDED.status IN ('dismissed', 'reclassified') THEN privacy_flags.resolved_at END
"""


def status_for(kinds: dict, suggested: str | None, classification: str, previous: str | None, previous_kinds) -> str:
    """A scan's verdict: nothing to review, a flag to review, or an admin's earlier decision still standing."""
    if suggested is None or RANK.get(classification, 1) >= RANK[suggested]:
        return "reclassified" if previous == "reclassified" else "clear"
    if previous == "dismissed" and previous_kinds == kinds:
        return "dismissed"  # the same findings an admin already looked at and dismissed
    return "open"


def scan(conn) -> int:
    """Scan documents that are new or changed since their last scan. Returns how many were scanned."""
    scanned = 0
    while True:
        docs = conn.execute(DUE_SQL).fetchall()
        if not docs:
            return scanned
        for d in docs:
            sections = [(c["heading_path"], c["body"]) for c in conn.execute(
                "SELECT heading_path, body FROM chunks WHERE document_id = %s ORDER BY ordinal", (d["id"],)).fetchall()]
            kinds, suggested = detect(d["title"], sections)
            status = status_for(kinds, suggested, d["classification"], d["status"], d["kinds"])
            conn.execute(UPSERT_SQL, (d["id"], d["org_id"], d["content_hash"], json.dumps(kinds), suggested, status))
            scanned += 1
