.PHONY: setup lint fmt typecheck test check secrets

setup:
	uv sync
	uv run pre-commit install

lint:
	uv run ruff check
	uv run ruff format --check

fmt:
	uv run ruff check --fix
	uv run ruff format

typecheck:
	uv run pyright

test:
	uv run pytest --cov --cov-report=term-missing

secrets:
	gitleaks git . --no-banner

check: lint typecheck test
