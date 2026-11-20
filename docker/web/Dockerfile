# syntax=docker/dockerfile:1
# Build from the repository root: docker build -f docker/web/Dockerfile .
FROM python:3.12-slim

WORKDIR /srv
# Only the hashed lock, the one the scans read, is installed; the package adds no dependency.
COPY docker/web/requirements.txt requirements.txt
COPY packages/web packages/web
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && pip install --no-cache-dir --no-deps --no-build-isolation ./packages/web \
    && rm -rf packages requirements.txt

USER nobody
EXPOSE 8000
CMD ["uvicorn", "--factory", "naos_web.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
