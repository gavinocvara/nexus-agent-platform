.PHONY: compose-clean compose-down compose-up format install-dev integration lint test typecheck validate

install-dev:
	py -m pip install -e ".[dev]"

format:
	py -m ruff format .

lint:
	py -m ruff check .

typecheck:
	py -m mypy

test:
	py -m pytest

validate: lint typecheck test

compose-up:
	docker compose up --build --detach --wait

integration:
	py -m pytest -m integration tests/integration

compose-down:
	docker compose down

compose-clean:
	docker compose down --volumes
