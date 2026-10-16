"""Embedding providers (M2).

The org default is a small open model (BAAI/bge-small-en-v1.5, 384 dimensions) running on CPU inside
the stack: no keys, no cost, and no text leaves the cell. An org or a team can register another
provider in embedding_models; OpenAI-compatible endpoints (OpenAI, Ollama, vLLM, LiteLLM) work today.
Embedding calls are egress like any model call, so the M4 egress policy will govern them too."""
import os
import re
from functools import lru_cache

import httpx

LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
LOCAL_DIMS = 384
CACHE = os.environ.get("KL_EMBED_CACHE", "/opt/kl/models")


@lru_cache(maxsize=4)
def _local(model: str):
    from fastembed import TextEmbedding  # lazy: only processes that embed load the model
    return TextEmbedding(model_name=model, cache_dir=CACHE)


def embed(provider: str, model: str, texts: list[str], base_url: str | None = None,
          api_key: str | None = None) -> list[list[float]]:
    if not texts:
        return []
    if provider == "local":
        return [vector.tolist() for vector in _local(model).embed(texts)]
    if provider == "openai_compatible":
        url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/embeddings"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        r = httpx.post(url, headers=headers, json={"model": model, "input": texts}, timeout=60)
        r.raise_for_status()
        return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]
    raise ValueError(f"embedding provider {provider!r} isn't supported yet")


def embed_query(provider: str, model: str, text: str, base_url: str | None = None,
                api_key: str | None = None) -> list[float]:
    """Queries are encoded as queries (BGE models add a retrieval prefix), passages as passages."""
    if provider == "local":
        encoder = _local(model)
        return next(iter(getattr(encoder, "query_embed", encoder.embed)([text]))).tolist()
    return embed(provider, model, [text], base_url=base_url, api_key=api_key)[0]


CONTEXT = 2  # how chunks are embedded now (Q2): with the document's opening line; older embeddings are made again
OPENING = re.compile(r"^(.{20,240}?[.!?])(\s|$)", re.S)


def opening_line(text: str | None) -> str:
    """A document's opening, in a line: its first sentence, or its first 200 characters."""
    flat = " ".join((text or "").split())
    m = OPENING.match(flat)
    return m[1] if m else flat[:200]


def passage_text(title: str | None, heading: str | None, body: str, opening: str | None = None) -> str:
    """Contextual chunk text: the title, the section path, and (past the first chunk) the document's opening line
    travel with the body, so a chunk saying 'fail over only if lag stays high' is still found as part of the payments
    incident runbook, and one saying "it" knows what "it" is."""
    return "\n".join(part for part in (title, heading, opening, body) if part)


def vector_literal(vector: list[float]) -> str:
    """pgvector's text input format, so no driver adapter is needed: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"
