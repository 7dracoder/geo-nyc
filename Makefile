# Convenience targets for local development.
# Run `make help` to see all targets.

PYTHON ?= /opt/homebrew/bin/python3.12
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
PY     := $(VENV)/bin/python
UVICORN := $(VENV)/bin/uvicorn

.PHONY: help venv install install-dev install-modeling clean fmt lint test run ollama-up

help:
	@echo "geo-nyc backend make targets:"
	@echo "  make venv             Create .venv with Python 3.12"
	@echo "  make install          Install runtime deps (no GemPy, no dev)"
	@echo "  make install-dev      Install runtime + dev deps"
	@echo "  make install-modeling Install GemPy extra (heavy)"
	@echo "  make run              Start FastAPI on \$$GEO_NYC_API_HOST:\$$GEO_NYC_API_PORT"
	@echo "  make test             Run pytest"
	@echo "  make lint             Ruff check"
	@echo "  make fmt              Ruff format"
	@echo "  make ollama-up        Pull default model and run \`ollama serve\` (foreground)"
	@echo "  make clean            Remove caches"

venv:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip wheel setuptools

install: venv
	$(PIP) install -e .

install-dev: venv
	$(PIP) install -e ".[dev]"

install-modeling: venv
	$(PIP) install -e ".[dev,modeling]"

run:
	$(UVICORN) api.main:app --host $${GEO_NYC_API_HOST:-127.0.0.1} --port $${GEO_NYC_API_PORT:-8000} --reload

test:
	$(PY) -m pytest -ra

lint:
	$(VENV)/bin/ruff check .

fmt:
	$(VENV)/bin/ruff format .

ollama-up:
	ollama pull $${GEO_NYC_OLLAMA_MODEL:-llama3.1:8b}
	ollama serve

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
