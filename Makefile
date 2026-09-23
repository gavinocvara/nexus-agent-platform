.PHONY: install-dev format lint typecheck test validate

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
