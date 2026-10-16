"""Claim-level checking (Q3, K9): each sentence of an answer, checked against the passages it cites.

A claim is a sentence of the answer with at least two content words. Its support is the share of those words that
the best of its cited passages also holds (a passage it doesn't cite never counts), and it's supported when that
reaches the threshold. A claim citing nothing is unsupported. The threshold is calibrated against the evaluation set
(`calibrate`, from the golden set's key facts against their right passages and against others), stored per org, and
used by every answer; until then it's DEFAULT_THRESHOLD, and the answer says so. Words, not a model: a team's own
model judging entailment can come on top of this, never instead of it.

Shared words aren't agreement (H4): a claim that negates what its source says, or gives a number the source doesn't (a
different amount, a different count of days), is unsupported however many words it shares. So negations and every
number count as content words, each number the claim gives (with its currency or unit) must be in the passage, and
the claim must be negated exactly when the passage's sentence it matches best is."""
import re

DEFAULT_THRESHOLD = 0.5
MIN_WORDS = 2  # shorter sentences ("In short:") claim nothing
MIN_SAMPLES = 8
SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)(?:\s*\[\d{1,2}\])*")
MARK = re.compile(r"\[(\d{1,2})\]")
WORD = re.compile(r"[a-z0-9]+")
STOP = {"the", "and", "for", "are", "was", "were", "with", "that", "this", "from", "into", "they", "their", "there", "then",
        "than", "what", "when", "which", "who", "whom", "about", "should", "would", "could", "can", "will", "must", "may",
        "has", "have", "had", "but", "all", "any", "its", "his", "her", "our", "your", "you", "one", "per", "via",
        "according", "source", "sources", "says", "said", "also", "only", "just", "first", "next", "before", "after"}
NEGATIONS = {"not", "no", "never", "without", "nor", "none", "neither", "cannot"}
CONTRACTION = re.compile(r"n['’]t\b")  # isn't, don't, won't: not
# A number, with a currency before it or a percent or unit after it: $500, 90 days, 12%, 1,000 rows. Not the digits
# inside a word (Q3, EDP-412's are its own: "412").
NUMBER = re.compile(r"(?<![a-z0-9])([$€£]?)(\d+(?:[.,]\d+)*)(?:\s*(%|[a-z]+))?")
UNITS = {"second", "minute", "hour", "day", "week", "month", "quarter", "year", "ms", "kb", "mb", "gb", "tb", "row", "record",
         "percent", "user", "people", "time", "dollar", "euro", "usd", "eur", "gbp"}


def _plain(text: str) -> str:
    return CONTRACTION.sub(" not", (text or "").lower())


def _one(w: str) -> str:
    return w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w


def words(text: str) -> set[str]:
    """Content words, lower case, a plural's trailing s dropped, so "approves" and "approve" meet; negations and every
    number count, whatever their length."""
    return {_one(w) for w in WORD.findall(_plain(text)) if w in NEGATIONS or w.isdigit() or (len(w) > 2 and w not in STOP)}


def numbers(text: str) -> set[str]:
    """The numbers a text gives, each with its currency, percent, or unit: {"$500", "90 day", "12%"}."""
    out = set()
    for currency, value, after in NUMBER.findall(_plain(text)):
        unit = after if after == "%" else ""
        if after and after != "%" and (_one(after) in UNITS or after.rstrip("s") in UNITS):
            unit = " " + (_one(after) if _one(after) in UNITS else after.rstrip("s"))
        out.add(f"{currency}{value.replace(',', '')}{unit}")
    return out


def negated(text: str) -> bool:
    return bool(NEGATIONS & set(WORD.findall(_plain(text))))


def support(claim: set[str], passage: str) -> float:
    return len(claim & words(passage)) / len(claim) if claim else 0.0


def contradiction(text: str, claim: set[str], passage: str) -> str | None:
    """Why a claim says otherwise than a passage, or None: a number the passage doesn't give, or a negation the
    passage's best-matching sentence doesn't share (or the reverse)."""
    if not numbers(text) <= numbers(passage):
        return "a number its source doesn't give"
    sentences = SENTENCE.findall(passage) or [passage]
    closest = max(sentences, key=lambda s: len(claim & words(s)))
    if negated(text) != negated(closest):
        return "its source says otherwise"
    return None


def check(answer: str, passages: dict[int, str], threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """Each claim of an answer: {"text", "cites", "score", "supported"}, and the "reason" when a passage that shares
    enough of its words says otherwise. passages: {citation number: its text}."""
    out = []
    for raw in SENTENCE.findall(answer or ""):
        cites = sorted({int(n) for n in MARK.findall(raw)})
        text = " ".join(MARK.sub("", raw).split())
        claim = words(text)
        if len(claim) < MIN_WORDS:
            continue
        scored = [(support(claim, passages[n]), contradiction(text, claim, passages[n])) for n in cites if n in passages]
        score = max((s for s, _ in scored), default=0.0)
        supported = bool(cites) and any(s >= threshold and why is None for s, why in scored)
        reason = next((why for s, why in sorted(scored, key=lambda x: -x[0]) if s >= threshold and why), None) if not supported else None
        out.append({"text": text[:500], "cites": cites, "score": round(score, 3), "supported": supported,
                    **({"reason": reason} if reason else {})})
    return out


# The production review's H4 cases (docs/REVIEW.md, finding 5): the passage's own statements, and four that share
# nearly all its words but say otherwise. Checked here and in smoke.
REFUNDS = "Refunds over $500 are approved by the finance lead. Records are kept for 90 days."
CONTRADICTIONS = [
    ("Refunds over $500 are approved by the finance lead [1].", True),
    ("Records are kept for 90 days [1].", True),
    ("Refunds over $500 are not approved by the finance lead [1].", False),
    ("Records are kept for 30 days [1].", False),
    ("Refunds over $50 are approved by the finance lead [1].", False),
    ("Refunds are never approved by the finance lead [1].", False),
]


def calibrate(samples: list[tuple[float, bool]]) -> dict | None:
    """The threshold that best tells supported claims from unsupported ones in labeled samples (score, supported): the
    one with the highest accuracy, the stricter on a tie. None with too few samples, or only one kind."""
    if len(samples) < MIN_SAMPLES or len({label for _, label in samples}) < 2:
        return None
    best: tuple[float, int] = (DEFAULT_THRESHOLD, -1)
    for t in sorted({round(s, 3) for s, _ in samples} | {DEFAULT_THRESHOLD}):
        right = sum((s >= t) == label for s, label in samples)
        if right > best[1] or (right == best[1] and t > best[0]):
            best = (t, right)
    return {"threshold": max(best[0], 0.05), "accuracy": round(best[1] / len(samples), 3), "samples": len(samples)}


if __name__ == "__main__":
    passage = "When payments are down, page the payments on-call first, then open an incident channel."
    honest = check("Page the payments on-call first [1]. Then open an incident channel [1].", {1: passage})
    assert [c["supported"] for c in honest] == [True, True], honest
    lying = check("The CFO approves every refund personally [1].", {1: passage})
    assert [c["supported"] for c in lying] == [False], lying
    assert check("Open an incident channel right away.", {1: passage})[0]["supported"] is False  # it cites nothing
    assert (fitted := calibrate([(0.9, True)] * 5 + [(0.1, False)] * 5)) and fitted["accuracy"] == 1.0
    assert calibrate([(0.9, True)] * 3) is None
    for claim, supported in CONTRADICTIONS:  # H4: a contradiction is never supported, however many words it shares
        got = check(claim, {1: REFUNDS})
        assert got and got[0]["supported"] is supported, (claim, got)
    print("claims: ok")
