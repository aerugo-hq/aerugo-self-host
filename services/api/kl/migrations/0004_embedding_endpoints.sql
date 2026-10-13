-- M2: embedding models can point at a remote endpoint (OpenAI-compatible: OpenAI, Ollama, vLLM, LiteLLM).
SET search_path = kl, public;
ALTER TABLE embedding_models ADD COLUMN base_url text;
