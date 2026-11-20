# syntax=docker/dockerfile:1
# Build from the repository root: docker build -f docker/web/Dockerfile .
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

WORKDIR /srv
# Only the hashed lock, the one the scans read, is installed; the package adds no dependency.
COPY docker/web/requirements.txt requirements.txt
COPY packages/web packages/web
# zlib1g takes the security update the pinned base predates; its version moves with Debian.
# hadolint ignore=DL3008
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && pip install --no-cache-dir --no-deps --no-build-isolation ./packages/web \
    && rm -rf packages requirements.txt \
    && pip uninstall --yes pip \
    && apt-get update \
    && apt-get install --yes --no-install-recommends --only-upgrade zlib1g \
    && rm -rf /var/lib/apt/lists/*

USER nobody
EXPOSE 8000
CMD ["uvicorn", "--factory", "naos_web.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
