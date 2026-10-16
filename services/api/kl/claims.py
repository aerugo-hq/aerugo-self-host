"""Claim-level checking (Q3, K9): each sentence of an answer, checked against the passages it cites.

A claim is a sentence of the answer with at least two content words. Its support is the share of those words that
the best of its cited passages also holds (a passage it doesn't cite never counts), and it's supported when that
reaches the threshold. A claim citing nothing is unsupported. The threshold is calibrated against the evaluation set
(`calibrate`, from the golden set's key facts against their right passages and against others), stored per org, and
used by every answer; until then it's DEFAULT_THRESHOLD, and the answer says so. Words, not a model: a team's own
model judging entailment can come on top of this, never instead of it."""
import re

DEFAULT_THRESHOLD = 0.5
MIN_WORDS = 2  # shorter sentences ("In short:") claim nothing
MIN_SAMPLES = 8
SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)(?:\s*\[\d{1,2}\])*")
MARK = re.compile(r"\[(\d{1,2})\]")
WORD = re.compile(r"[a-z0-9]+")
STOP = {"the", "and", "for", "are", "was", "were", "with", "that", "this", "from", "into", "they", "their", "there", "then",
        "than", "what", "when", "which", "who", "whom", "about", "should", "would", "could", "can", "will", "must", "may",
        "has", "have", "had", "not", "but", "all", "any", "its", "his", "her", "our", "your", "you", "one", "per", "via",
        "according", "source", "sources", "says", "said", "also", "only", "just", "first", "next", "before", "after"}


def words(text: str) -> set[str]:
    """Content words, lower case, a plural's trailing s dropped, so "approves" and "approve" meet."""
    out = set()
    for w in WORD.findall((text or "").lower()):
        if len(w) > 2 and w not in STOP:
            out.add(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def support(claim: set[str], passage: str) -> float:
    return len(claim & words(passage)) / len(claim) if claim else 0.0


def check(answer: str, passages: dict[int, str], threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """Each claim of an answer: {"text", "cites", "score", "supported"}. passages: {citation number: its text}."""
    out = []
    for raw in SENTENCE.findall(answer or ""):
        cites = sorted({int(n) for n in MARK.findall(raw)})
        text = " ".join(MARK.sub("", raw).split())
        claim = words(text)
        if len(claim) < MIN_WORDS:
            continue
        score = max((support(claim, passages[n]) for n in cites if n in passages), default=0.0)
        out.append({"text": text[:500], "cites": cites, "score": round(score, 3), "supported": bool(cites) and score >= threshold})
    return out


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
    print("claims: ok")
