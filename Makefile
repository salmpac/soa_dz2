.PHONY: generate run up down logs psql test demo clean

generate:            ## regenerate app/generated from openapi/openapi.yaml
	sh scripts/generate.sh

run: generate        ## run the API locally (expects PostgreSQL from `make db`)
	python -m app

db:                  ## start only PostgreSQL + migrations (for local `make run`)
	docker compose up -d postgres flyway

up:                  ## build and start the whole stack
	docker compose up --build

down:                ## stop and remove the stack together with the database volume
	docker compose down -v

logs:
	docker compose logs -f app

psql:                ## open psql inside the database container
	docker compose exec postgres psql -U marketplace -d marketplace

test:                ## end-to-end tests against a running stack (BASE_URL=http://localhost:8000)
	ORDER_RATE_LIMIT_SECONDS=$${ORDER_RATE_LIMIT_SECONDS:-60} pytest -q tests

demo:                ## scripted E2E walk-through with curl
	sh scripts/demo.sh

clean:
	rm -rf app/generated .pytest_cache
