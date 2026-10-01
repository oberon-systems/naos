# Local images for the compose stack; a deployment pulls the published ones.
# The tags come from the same .env compose reads, so what is built is what runs.

-include .env

NAOS_REGISTRY ?= ghcr.io/oberon-systems
NAOS_TAG      ?= latest

.PHONY: images image-api image-web migrate up down


# images
images: image-api image-web

image-api:
	docker build -f api/Dockerfile -t $(NAOS_REGISTRY)/naos-api:$(NAOS_TAG) ..

image-web:
	docker build -f web/Dockerfile -t $(NAOS_REGISTRY)/naos-web:$(NAOS_TAG) ..


# stack
migrate:
	docker compose run --rm api naos-api migrate

up:
	docker compose up -d

down:
	docker compose down
