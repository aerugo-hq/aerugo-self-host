"""Reciprocal rank fusion (M2). Merges ranked lists by position only, so keyword scores and vector
distances (or vectors from different models) never have to be compared on the same scale."""

K = 60


def fuse(ranked: list[tuple[str, list[dict]]], key: str = "external_key", k: int = K,
         weights: dict[str, float] | None = None) -> list[dict]:
    """ranked: [(channel name, rows best-first)]. Returns rows best-first, each with the fused score
    and the channels that matched it. The first row seen for a document is the one displayed. A channel's
    weight scales its contribution (default 1)."""
    scores: dict[str, float] = {}
    best: dict[str, dict] = {}
    for channel, rows in ranked:
        weight = (weights or {}).get(channel, 1.0)
        for position, row in enumerate(rows):
            doc = row[key]
            scores[doc] = scores.get(doc, 0.0) + weight / (k + position + 1)
            if doc not in best:
                best[doc] = {**row, "matched": [channel]}
            elif channel not in best[doc]["matched"]:
                best[doc]["matched"].append(channel)
    return [{**row, "score": scores[doc]}
            for doc, row in sorted(best.items(), key=lambda item: scores[item[0]], reverse=True)]


def prefer_curated(rows: list[dict], boost: float, tiers: tuple[str, ...] = ("gold", "silver")) -> list[dict]:
    """Curated knowledge wins a near tie: a curated page's fused score counts `boost` times, so it outranks evidence
    that matches about as well as it does, and never evidence that clearly matches better. Each one moved is marked
    `curated` among the channels that matched it, so the reason shows. It replaced a curated channel that gave every
    curated page matching at all a second full vote, ranked among curated pages alone: a page the meaning channel found
    sixteenth outranked the one it found first, the page that answered (found by F9's synthetic retailer)."""
    out = [{**r, "score": r["score"] * boost, "matched": [*r["matched"], "curated"]} if r.get("tier") in tiers else r
           for r in rows]
    return sorted(out, key=lambda r: r["score"], reverse=True)  # stable: equal scores keep fusion's order


def current_first(rows: list[dict], supersedes: list[tuple[str, str]], key: str = "external_key") -> list[dict]:
    """Superseded knowledge after what replaced it (slice K5). For each (newer, older) pair with both in rows and the
    older ranked first, the older moves to just after the newer; everything else keeps its order. Chains settle (C
    replaces B, which replaced A: C, B, A), and a cycle can't loop forever. Callers pass only rows the person may see,
    so the order never hints at anything hidden."""
    order = list(rows)
    for _ in range(len(supersedes) * len(order) + 1):
        position = {r[key]: i for i, r in enumerate(order)}
        pair = next(((newer, older) for newer, older in sorted(supersedes)
                     if newer in position and older in position and position[older] < position[newer]), None)
        if pair is None:
            return order
        row = order.pop(position[pair[1]])
        order.insert(next(i for i, r in enumerate(order) if r[key] == pair[0]) + 1, row)
    return order
