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
