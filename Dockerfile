# Multi-stage build: deps → runtime
#
# The deps stage installs third-party packages and the ML model.  These
# rarely change and produce a ~1.6 GB layer.  In the runtime image we
# copy deps and source code as separate --link layers so that code-only
# changes never re-export the heavy deps/model layers.

# ── Stage 1: dependencies + model (stable, ~1.6 GB) ────────────────────
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS deps
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 UV_PYTHON_DOWNLOADS=0

# Sentence-transformer model is cached outside /app/data so that
# a volume mount on /app/data (for the DB + embeddings) doesn't shadow it.
ENV HF_HOME=/app/.models

WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --extra cpu

RUN uv run python -c '\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer("all-MiniLM-L6-v2"); \
print("Model cached."); \
'


# ── Stage 2: runtime image (no uv, no build tools) ────────────────────
FROM python:3.12-slim-bookworm
# It is important to use the image that matches the builder, as the path to the
# Python executable must be the same, e.g., using `python:3.11-slim-bookworm`
# will fail.

# Model cache lives outside /app/data so volume mounts don't shadow it
ENV HF_HOME=/app/.models
ENV HF_HUB_OFFLINE=1

# Setup a non-root user
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot \
 && apt-get update -qq && apt-get install -y -qq --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# ── Layer-split COPY with --link ──────────────────────────────────────
# --link creates content-addressed layers: if the source bytes haven't
# changed the layer is reused even when the stage it came from rebuilt.
#
# deps stage  → .venv (third-party only, ~1.5 GB) and .models (~90 MB)
#               These layers are cached across code-only changes.
# build context → project source code (small, changes every commit)
COPY --link --from=deps /app/.venv /app/.venv
COPY --link --from=deps /app/.models /app/.models
COPY ./src /app/src
# config.py:get_project_root() walks up from __file__ looking for pyproject.toml.
# Without this file the app crashes at startup with RuntimeError.
COPY ./pyproject.toml /app/pyproject.toml

# Ensure the data directory exists and is writable by nonroot,
# even when an empty Docker volume is mounted over it.
RUN mkdir -p /app/data && chown nonroot:nonroot /app/data

# Entrypoint fixes /app/data ownership (for files from `docker cp`)
# then drops to nonroot via setpriv.
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Place executables in the environment at the front of the path
# Make the project importable without installing it into the venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

# Use `/app` as the working directory
WORKDIR /app

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "tum_lecture_finder", "serve", "--host", "0.0.0.0"]
