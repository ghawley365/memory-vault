# Memory Vault — Docker image
# sentence-transformers pulls PyTorch, so we use CPU-only to keep image smaller

# ---------------------------------------------------------------------------
# Stage 1 — build the React dashboard
# ---------------------------------------------------------------------------
FROM node:26-slim AS web-builder

WORKDIR /web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — Python runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim

WORKDIR /app

# Install CPU-only PyTorch first (avoids pulling the 2GB CUDA version)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Copy project files (migrations travel inside src/memory_vault/)
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY scripts/start.sh ./scripts/start.sh

# Copy the built dashboard into the package's static dir so it's bundled by the pip install
COPY --from=web-builder /web/dist/ ./src/memory_vault/api/static/

RUN pip install --no-cache-dir . \
    && sed -i 's/\r$//' ./scripts/start.sh \
    && chmod +x ./scripts/start.sh \
    && mkdir -p /var/log/memory-vault

# LOCAL FORK: nomic-bert-2048's remote modeling code calls
# PreTrainedModel.get_extended_attention_mask, removed in transformers 5.x
# ("'NomicBertModel' object has no attribute 'get_extended_attention_mask'").
# Pin 4.x until nomic updates its code. Verified: transformers 4.57.6 +
# sentence-transformers 5.7.0, pip check clean, encode -> 768-d.
RUN pip install --no-cache-dir "transformers>=4.41,<5"

RUN python -m spacy download en_core_web_sm

# The container runs as a non-root user, so put the model caches somewhere
# that user owns. HF_HOME defaults to /root/.cache, which is unreadable to
# anyone else and unwritable under a read-only root filesystem.
ENV HF_HOME=/opt/model-cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/opt/model-cache/sentence-transformers

# Non-root. Everything the process writes at runtime is either a mounted
# volume (/var/log/memory-vault), a tmpfs (/tmp, for streamed uploads), or
# read-only (the model cache below), so the image itself never needs to be
# writable — see docker-compose.yml for the read_only + tmpfs setup.
#
# The switch happens BEFORE the model download on purpose: a `chown -R` after
# it rewrites every model file into a new layer, storing the whole ~92MB cache
# twice. Downloading as the owning user avoids that.
RUN useradd --system --uid 10001 --create-home --home-dir /home/memoryvault memoryvault \
    && mkdir -p /opt/model-cache \
    && chown -R memoryvault:memoryvault /app /var/log/memory-vault /opt/model-cache

USER memoryvault

# Download the embedding model at BUILD time. Without this the first request
# after every container start reaches out to huggingface.co and writes into
# the cache — which fails outright when the root filesystem is read-only, and
# makes a cold start depend on the network.
#
# LOCAL FORK: bake nomic-embed-text-v1.5 (768-d) instead of upstream's
# all-MiniLM-L6-v2, pinned to the same revision as EMBEDDING_MODEL_REVISION.
# trust_remote_code also pulls nomic-ai/nomic-bert-2048 architecture code into
# the HF modules cache. ~550MB. Keep these ARGs in sync with .env / override.
ARG EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5
ARG EMBEDDING_MODEL_REVISION=e9b6763023c676ca8431644204f50c2b100d9aab
RUN python -c "import os; from sentence_transformers import SentenceTransformer; m = SentenceTransformer(os.environ['EMBEDDING_MODEL'], revision=os.environ['EMBEDDING_MODEL_REVISION'], trust_remote_code=True); v = m.encode(['search_query: build smoke test']); assert v.shape[1] == 768, v.shape; print('baked', os.environ['EMBEDDING_MODEL'], v.shape)"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=5)" || exit 1

ENTRYPOINT ["./scripts/start.sh"]
