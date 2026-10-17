FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app
# OCR for scanned PDFs and pictures of text (slice L2b): Tesseract reads text, Poppler renders PDF pages for it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng poppler-utils \
 && rm -rf /var/lib/apt/lists/*
# Install from the hash-pinned lockfile when there is one (`make lock` writes it), else from the ranges.
# A lockfile missing anything requirements.txt asks for stops the build, instead of shipping without it.
COPY requirements* ./
RUN if [ -f requirements.lock ]; then \
      python -c "import re, sys; norm = lambda s: s.strip().lower().replace('_', '-'); want = {norm(re.split(r'[<>=!~\[ ;]', l)[0]) for l in open('requirements.txt') if l.strip() and not l.startswith('#')}; have = {norm(l.split('==')[0]) for l in open('requirements.lock') if '==' in l and not l[0].isspace() and not l.startswith('#')}; missing = sorted(want - have); sys.exit('requirements.lock is out of date (missing ' + ', '.join(missing) + '): run make lock') if missing else None" \
      && pip install --no-cache-dir --require-hashes -r requirements.lock; \
    else pip install --no-cache-dir -r requirements.txt; fi
# Bake the default embedding model into the image: no download at runtime, nothing leaves the cell.
ENV KL_EMBED_CACHE=/opt/kl/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/kl/models')" \
 && chown -R app:app /opt/kl/models
COPY kl ./kl

# Smoke and the fake model (make smoke) build from this stage. Production images never carry them.
FROM base AS test
RUN pip install --no-cache-dir --require-hashes -r requirements-test.lock   # M4: pytest, hash-pinned
COPY tests ./tests
USER app

# The default target, and the only one deployed: the same code without test tooling.
FROM base AS runtime
RUN rm -f kl/smoke.py kl/fake_llm.py kl/eval.py kl/golden.yaml kl/golden_mine.py kl/drill.py
USER app
EXPOSE 8000
CMD ["uvicorn", "kl.main:app", "--host", "0.0.0.0", "--port", "8000"]
