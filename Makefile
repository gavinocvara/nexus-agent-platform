.PHONY: compose-clean compose-down compose-up format format-check install-dev integration lint scenarios test typecheck validate

# Override on Windows with: make PYTHON=py <target>
PYTHON ?= python

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

validate: format-check lint typecheck test scenarios

scenarios:
	$(PYTHON) -m nexus.lab.scenarios validate

compose-up:
	docker compose up --build --detach --wait

integration:
	$(PYTHON) -m pytest -m integration tests/integration

compose-down:
	docker compose down

compose-clean:
	docker compose down --volumes
