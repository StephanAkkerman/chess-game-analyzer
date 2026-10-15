.PHONY: help update

help:
	@echo "make update   git pull, then pull (or build) the image and restart the Docker stack"

# For a deployed stack (see README's "Deploying it on a Raspberry Pi"), not local
# dev. Pulls the prebuilt GHCR image, and builds from source only if that pull
# fails (image not published yet, package private without `docker login`,
# registry down), so a Pi doesn't download the tablebases and build the image on
# every update. `down` before `up` forces a clean restart even when
# docker-compose.yml, .env, or COMPOSE_PROFILES changed; Make already stops at
# the first failing line, so a conflicted `git pull` won't tear down a working
# deployment. Analyses survive in the analyzer-data volume.
update:
	git pull
	@if docker compose pull; then \
		docker compose down && docker compose up -d; \
	else \
		echo "Prebuilt image unavailable; building from source instead."; \
		docker compose down && docker compose up -d --build; \
	fi
