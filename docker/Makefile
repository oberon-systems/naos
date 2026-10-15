# Local images for the compose stack; a deployment pulls the published ones.
# The tags come from the same .env compose reads, so what is built is what runs.

-include .env

NAOS_API_IMAGE ?= ghcr.io/oberon-systems/naos-api:latest
NAOS_WEB_IMAGE ?= ghcr.io/oberon-systems/naos-web:latest

.PHONY: images image-api image-web migrate up down


# images
images: image-api image-web

image-api:
	docker build -f api/Dockerfile -t $(NAOS_API_IMAGE) ..

image-web:
	docker build -f web/Dockerfile -t $(NAOS_WEB_IMAGE) ..


# stack
migrate:
	docker compose run --rm api naos-api migrate

up:
	docker compose up -d

down:
	docker compose down
