# syntax=docker/dockerfile:1
# Build from the repository root: docker build -f docker/web/Dockerfile .
FROM python:3.12-slim

WORKDIR /srv
COPY packages/common packages/common
COPY packages/web packages/web
RUN pip install --no-cache-dir ./packages/common "./packages/web[server]" \
    && rm -rf packages

USER nobody
EXPOSE 8000
CMD ["uvicorn", "--factory", "naos_web.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
