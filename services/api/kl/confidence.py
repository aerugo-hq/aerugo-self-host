"""Confidence for an answer (slice K9), from signals, never from the model's opinion of itself: a model's own reported
confidence is poorly calibrated, worst of all when it answers from retrieved text (ADR 0010). The signals are whether
anything supports an answer at all, the tier of what the answer actually cites (gold: a person verified it; silver:
reviewed; evidence: raw), how many separate sources it rests on, and whether it cites a page something newer replaces.
Every level comes with its reasons. Claims (Q3) count too: an answer whose claims its citations don't support is
never high, and one with none supported is low."""


def confidence(found: bool, cited: list[dict], supersedes: list[tuple[str, str]], claims: list[dict] | None = None,
               calibrated: bool = True) -> dict:
    """found: whether there was an answer at all. cited: the sources the answer cites, each {"id", "tier"}.
    supersedes: (newer, older) pairs among the sources. claims: the answer's claims as `claims.check` judged them;
    calibrated: whether their threshold came from the evaluation set. Returns {"level": "high" | "medium" | "low",
    "reasons": [...]}."""
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
    unsupported = [c for c in claims or [] if not c["supported"]]
    if unsupported:
        reasons.append(f"{plural(len(unsupported), 'claim')} of {len(claims)} not supported by what {'it cites' if len(unsupported) == 1 else 'they cite'}"
                       + ("." if calibrated else ", by an uncalibrated threshold."))
    if (gold or (silver and len(ids) > 1)) and not replaced and not unsupported:
        level = "high"
    elif (gold or silver or len(ids) > 1) and len(unsupported) < len(claims or [None]):
        level = "medium"
    else:
        level = "low"
    return {"level": level, "reasons": reasons}
