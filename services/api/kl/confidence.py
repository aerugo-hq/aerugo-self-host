"""Confidence for an answer (slice K9), from signals, never from the model's opinion of itself: a model's own reported
confidence is poorly calibrated, worst of all when it answers from retrieved text (ADR 0010). The signals are whether
anything supports an answer at all, the tier of what the answer actually cites (gold: a person verified it; silver:
reviewed; evidence: raw), how many separate sources it rests on, and whether it cites a page something newer replaces.
Every level comes with its reasons. Calibrating the thresholds against each company's evaluation set comes next."""


def confidence(found: bool, cited: list[dict], supersedes: list[tuple[str, str]]) -> dict:
    """found: whether there was an answer at all. cited: the sources the answer cites, each {"id", "tier"}.
    supersedes: (newer, older) pairs among the sources. Returns {"level": "high" | "medium" | "low", "reasons": [...]}."""
    if not found:
        return {"level": "low", "reasons": ["Nothing you can read supports an answer."]}
    if not cited:
        return {"level": "low", "reasons": ["The answer doesn't cite any source."]}
    ids = {c["id"] for c in cited}
    tiers = {c["id"]: c.get("tier") for c in cited}
    gold = sum(1 for t in tiers.values() if t == "gold")
    silver = sum(1 for t in tiers.values() if t == "silver")
    plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"
    if gold:
        reasons = [f"Cites {plural(gold, 'verified page')}."]
    elif silver:
        reasons = [f"Cites {plural(silver, 'reviewed page')}."]
    else:
        reasons = ["Cites only raw evidence, nothing reviewed."]
    reasons.append(f"Rests on {plural(len(ids), 'source')}." if len(ids) > 1 else "Rests on a single source.")
    replaced = sorted({older for newer, older in supersedes if older in ids})
    if replaced:
        reasons.append(f"Cites {plural(len(replaced), 'superseded page')}; something newer replaces it.")
    if (gold or (silver and len(ids) > 1)) and not replaced:
        level = "high"
    elif gold or silver or len(ids) > 1:
        level = "medium"
    else:
        level = "low"
    return {"level": level, "reasons": reasons}
