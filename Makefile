.PHONY: all eval test lint format typecheck clean help ui

PYTHON ?= python

help:
	@echo "Available make targets:"
	@echo "  make ui         - Launch interactive web studio at http://localhost:8080"
	@echo "  make eval       - Run benchmark evaluation suite across eval/cases/"
	@echo "  make test       - Run pytest test suite with coverage"
	@echo "  make lint       - Run ruff linter checks"
	@echo "  make format     - Run ruff code formatter"
	@echo "  make typecheck  - Run mypy static type analysis"
	@echo "  make all        - Run lint, typecheck, test, and eval"

ui:
	$(PYTHON) -m har2mcp.web.server

eval:
	$(PYTHON) eval/run_eval.py

test:
	$(PYTHON) -m pytest -v --cov=har2mcp

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy src tests eval

all: lint typecheck test eval

clean:
	rm -rf .pytest_cache .coverage* .har2mcp_cache eval/results.md eval/results.json
