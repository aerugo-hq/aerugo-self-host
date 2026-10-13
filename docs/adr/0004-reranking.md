# ADR 0004: Reranking with one reranker per org, over the /rerank API

**Status:** accepted (slice M2)

## Context

Fusion ranks by position across the lexical, title, vector, and curated channels; it never reads a
passage against the question. A reranker (a cross-encoder) does, and it lifts first-place accuracy most
on questions phrased unlike their answers. But a reranker reads passages, so it's an egress path: what
it reads must obey the egress policy (invariant 7), and it must never read anything the caller may not
see (invariant 1).

## Decision

**One reranker per org, called over the Cohere-style `/rerank` API.** An org admin sets it with
`PUT /v1/reranker`. It's stored in `rerank_models` with its own `allowed_classifications`, and its key
lives in the secrets backend. The API (`POST {base_url}/rerank` with model, query, documents, and top_n)
is spoken by LiteLLM, which fronts Cohere, Voyage, Bedrock, and Vertex rankers, and by vLLM, Infinity,
Cohere, Jina, and Voyage directly. A self-hosted reranker (vLLM or Infinity serving a bge reranker, for
example) keeps content inside the cell, and the API image gains no new dependency.

**Where it runs:** inside `retrieve()`, after the OpenFGA post-check and before the graph hop, so
search, ask, MCP, and the compiler all get it. With a reranker set, up to 30 candidates pass the
post-check (checked in batches) to give it a pool, and it reads at most 1,500 characters of each.

**What it may change:**

- Passages its egress list forbids are never sent, and they keep the exact place fusion gave them.
- Its order is blended with fusion's by rank (RRF, its rank counting double), so neither score is read
  on the other's scale, and a strong fused lead survives a near-tie from the reranker.
- Canonical matches stay first.
- A failure, or a timeout past 5 seconds, leaves fusion's order. Search never fails because the
  reranker did.

**Measured, not assumed:** `make eval` scores the golden set with the reranker and without it, and
`?rerank=false` turns it off for a single search.

## Alternatives considered

- **A cross-encoder inside the API image** (sentence-transformers or ONNX Runtime with a MiniLM or bge
  model). Content never leaves the process, but every cell gains torch or onnxruntime and model weights,
  every search spends CPU in the API, and air-gapped installs must ship the weights. A local reranker
  served by vLLM or Infinity gives the same privacy without tying the API to a model runtime.
- **The chat model as a reranker** (asking a language model to order the candidates). It works with any
  registered model, but it costs a generation per search, adds seconds of latency, and its output needs
  parsing and validation.
- **A reranker per team, like chat models.** Org-wide searches mix teams' results, and scores from
  different rerankers can't be compared. The reranker belongs to the org, like the default embedding
  model.

## Consequences

- A new egress path, governed like the others: its own classification list, withholds counted in
  `kl.egress.withheld` with surface `rerank`, and smoke checks that it never reads what the caller can't
  see or its list forbids.
- A search with a reranker makes one more call and post-checks up to 30 candidates instead of `limit`;
  batching keeps that to one OpenFGA request per 50.
- Rerank calls aren't metered per team yet. Slice O adds budgets.
