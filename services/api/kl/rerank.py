"""Reranking (slice M2): a closer second read of the best candidates by a reranking model.

Rules:
- The reranker reads only documents that already passed the permission post-check.
- It reads only passages its egress policy allows (invariant 7).
- A passage it may not read keeps exactly the place fusion gave it, so being withheld never pushes a
  document down.
- Its order is blended with fusion's by rank (RRF, the reranker counting double), so neither score has to
  be read on the other's scale.
- Canonical matches stay first.
- A reranker that fails or times out leaves fusion's order in place: search never fails because the
  reranker did.

The API is the Cohere-style POST {base_url}/rerank (model, query, documents, top_n; results carry index
and relevance_score), which LiteLLM, vLLM, Infinity, Cohere, Jina, and Voyage speak. A self-hosted
reranker keeps content inside the cell. Telemetry gets counts and durations only, never the query or a
passage.
"""
import os
from collections.abc import Callable

import httpx

POOL = int(os.environ.get("KL_RERANK_POOL", "30"))  # how many post-checked candidates the reranker reads
WEIGHT = float(os.environ.get("KL_RERANK_WEIGHT", "2"))  # its say against fusion's, in the blend
TIMEOUT = float(os.environ.get("KL_RERANK_TIMEOUT", "5"))  # seconds; past this, fusion's order stands
PASSAGE_CHARS = 1500
K = 60  # the same rank constant as fusion's


class RerankError(RuntimeError):
    pass


def passage(row: dict) -> str:
    """What the reranker reads for one candidate: title, section, and the matched chunk's text."""
    where = f" > {row['heading_path']}" if row.get("heading_path") else ""
    text = row.get("body") or row.get("snippet") or ""
    return f"{row['title']}{where}\n{text[:PASSAGE_CHARS]}"


def scores(provider: str, model: str, base_url: str, api_key: str | None, query: str,
           documents: list[str]) -> list[float]:
    """One relevance score per document, in the order given. Anything short of a score for every
    document is an error: a partial answer isn't trusted."""
    if provider != "openai_compatible":
        raise RerankError(f"rerank provider {provider!r} isn't supported")
    url = base_url.rstrip("/") + "/rerank"
    try:
        r = httpx.post(url, headers={"Authorization": f"Bearer {api_key}"} if api_key else {}, timeout=TIMEOUT,
                       json={"model": model, "query": query, "documents": documents, "top_n": len(documents)})
        r.raise_for_status()
        body = r.json()
    except httpx.HTTPStatusError as e:
        raise RerankError(f"rerank call failed ({e.response.status_code})") from None
    except (httpx.HTTPError, ValueError) as e:
        raise RerankError(f"reranker unreachable ({type(e).__name__})") from None
    items = body if isinstance(body, list) else (body.get("results") or body.get("data") or [])
    out: list[float | None] = [None] * len(documents)
    for item in items:
        i, s = item.get("index"), item.get("relevance_score", item.get("score"))
        if isinstance(i, int) and 0 <= i < len(documents) and isinstance(s, int | float):
            out[i] = float(s)
    if any(v is None for v in out):
        raise RerankError("the reranker didn't score every document")
    return out  # type: ignore[return-value]


def rerank(rows: list[dict], query: str, reranker: dict, api_key: str | None,
           score: Callable[..., list[float]] = scores) -> tuple[list[dict], int, int]:
    """rows: post-checked candidates, best first. Returns (rows in their new order, how many the reranker
    read, how many it wasn't allowed to read). Raises RerankError; the caller keeps fusion's order then."""
    allowed = set(reranker["allowed_classifications"])
    pinned = 0
    while pinned < len(rows) and "canonical" in rows[pinned].get("matched", ()):
        pinned += 1  # an explicit request for one concept stays first
    slots = [i for i in range(pinned, len(rows)) if rows[i]["classification"] in allowed]
    withheld = len(rows) - pinned - len(slots)
    if len(slots) < 2:
        return rows, 0, withheld  # nothing to reorder
    sendable = [rows[i] for i in slots]
    got = score(reranker["provider"], reranker["model"], reranker["base_url"], api_key, query,
                [passage(r) for r in sendable])
    by_model = sorted(range(len(sendable)), key=lambda j: got[j], reverse=True)
    model_rank = {j: rank for rank, j in enumerate(by_model)}
    blended = sorted(range(len(sendable)),
                     key=lambda j: WEIGHT / (K + model_rank[j] + 1) + 1 / (K + j + 1), reverse=True)
    out = list(rows)
    for slot, j in zip(slots, blended):
        row = sendable[j]
        out[slot] = {**row, "matched": [*row.get("matched", []), "rerank"], "rerank_score": got[j]}
    return out, len(sendable), withheld
