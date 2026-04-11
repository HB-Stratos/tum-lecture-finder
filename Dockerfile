# An example using multi-stage image builds to create a final image without uv.

# First, build the application in the `/app` directory.
# See `Dockerfile` for details.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Omit development dependencies
ENV UV_NO_DEV=1

# Sentence-transformer model is cached outside /app/data so that
# a volume mount on /app/data (for the DB + embeddings) doesn't shadow it.
ENV HF_HOME=/app/.models

# Disable Python downloads, because we want to use the system interpreter
# across both images. If using a managed Python version, it needs to be
# copied from the build image into the final image; see `standalone.Dockerfile`
# for an example.
ENV UV_PYTHON_DOWNLOADS=0

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

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --extra cpu


# Then, use a final image without uv
FROM python:3.12-slim-bookworm
# It is important to use the image that matches the builder, as the path to the
# Python executable must be the same, e.g., using `python:3.11-slim-bookworm`
# will fail.

# Model cache lives outside /app/data so volume mounts don't shadow it
ENV HF_HOME=/app/.models
ENV HF_HUB_OFFLINE=1

# Setup a non-root user
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot

# Copy the application from the builder (no --chown: .venv and .models are
# read-only at runtime, and chowning thousands of small files is very slow).
COPY --from=builder /app /app

# Ensure the data directory exists and is writable by nonroot,
# even when an empty Docker volume is mounted over it.
RUN mkdir -p /app/data && chown nonroot:nonroot /app/data

# Entrypoint fixes /app/data ownership (for files from `docker cp`)
# then drops to nonroot via setpriv.
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Use `/app` as the working directory
WORKDIR /app

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["tlf", "serve", "--host", "0.0.0.0"]