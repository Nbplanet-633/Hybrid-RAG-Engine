# Ask My Docs — development tasks.
#
# Everything defaults to the `offline` profile: deterministic, no model downloads,
# no API key. Override with PROFILE=full once you have both.

# Pick the newest available Python >= 3.10 rather than hardcoding one version,
# so a fresh clone builds on whatever the machine happens to have. Override
# explicitly with: make install PYTHON=/path/to/python3.12
PYTHON  ?= $(shell for p in python3.13 python3.12 python3.11 python3.10 python3; do \
             if command -v $$p >/dev/null 2>&1 && \
                $$p -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; \
             then command -v $$p; break; fi; done)
VENV    ?= .venv
BIN     := $(VENV)/bin
PROFILE ?= offline

export ASKMYDOCS_PROFILE = $(PROFILE)

.DEFAULT_GOAL := help
.PHONY: help venv install install-full verify ingest ask serve ui stats prompts \
        test test-cov lint format validate-golden eval eval-full eval-judge \
        ragas ci clean docker-build docker-run share

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- setup ------------------------------------------------------------------

venv: ## Create the virtualenv
	@if [ -z "$(PYTHON)" ]; then \
	  echo "ERROR: no Python >= 3.10 found on PATH."; \
	  echo "  Install one (macOS: brew install python@3.12), or point at an existing"; \
	  echo "  interpreter:  make install PYTHON=/full/path/to/python3"; \
	  exit 1; \
	fi
	@echo "Using $(PYTHON) ($$($(PYTHON) --version))"
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip

install: venv ## Install the package plus dev and offline-profile extras
	$(BIN)/pip install -e ".[loaders,chroma,dev]"

install-full: venv ## Install everything, including local models and the Claude SDK (~500 MB)
	$(BIN)/pip install -e ".[full,dev,ragas]"

verify: ## Check whether the project works on this machine (start here)
	$(BIN)/python scripts/verify.py

# --- running ----------------------------------------------------------------

ingest: ## Index data/corpus (make ingest ARGS=--reset)
	$(BIN)/askmydocs ingest $(ARGS)

ask: ## Ask one question (make ask Q="How much is the dispute fee?")
	@test -n "$(Q)" || (echo 'Usage: make ask Q="your question"' && exit 1)
	$(BIN)/askmydocs ask "$(Q)" --show-retrieval

serve: ## Run the HTTP API on :8000
	$(BIN)/askmydocs serve

ui: ## Run the Streamlit demo on :8501
	$(BIN)/streamlit run ui/streamlit_app.py

stats: ## Show index state
	$(BIN)/askmydocs stats

prompts: ## List the versioned prompt registry
	$(BIN)/askmydocs prompts

# --- quality ----------------------------------------------------------------

test: ## Run the test suite
	$(BIN)/python -m pytest

test-cov: ## Run the tests with a coverage report
	$(BIN)/python -m pytest --cov=src/askmydocs --cov-report=term-missing --cov-report=xml

lint: ## Lint and check formatting
	$(BIN)/ruff check src eval tests ui
	$(BIN)/ruff format --check src eval tests ui

format: ## Auto-fix lint and formatting
	$(BIN)/ruff check --fix src eval tests ui
	$(BIN)/ruff format src eval tests ui

# --- evaluation -------------------------------------------------------------

validate-golden: ## Check the golden dataset's labels
	$(BIN)/python -m eval.validate_golden

eval: ingest ## Run the golden-set evaluation and enforce the gate
	$(BIN)/python -m eval.run_eval --profile $(PROFILE)

eval-full: ## Run the evaluation against the model-backed profile
	$(MAKE) eval PROFILE=full

eval-judge: ## Evaluate with LLM-as-judge faithfulness (needs ANTHROPIC_API_KEY)
	$(BIN)/python -m eval.run_eval --profile $(PROFILE) --llm-judge

ragas: ## Cross-check with Ragas (slow, needs an API key)
	$(BIN)/python -m eval.ragas_eval --profile $(PROFILE) --limit 25

ci: lint validate-golden test eval ## Everything CI runs, in order

# --- docker -----------------------------------------------------------------

docker-build: ## Build the container image
	docker build -t ask-my-docs:latest .

docker-run: ## Run the API in docker on :8000
	docker compose up api

# --- housekeeping -----------------------------------------------------------

share: ## Package a clean copy to hand to someone else (excludes .venv and the index)
	@rm -f ../ask-my-docs-share.zip
	@zip -qr ../ask-my-docs-share.zip . \
	  -x '.venv/*' -x 'storage/*' -x '.git/*' -x '*/__pycache__/*' \
	  -x '.pytest_cache/*' -x '.ruff_cache/*' -x '.coverage' -x '.env' \
	  -x 'eval_reports/*.json'
	@echo "Wrote ../ask-my-docs-share.zip ($$(du -h ../ask-my-docs-share.zip | cut -f1))"
	@echo "The recipient runs:  make install && make verify"

clean: ## Remove caches, build artifacts, and local indexes
	rm -rf build dist *.egg-info src/*.egg-info .pytest_cache .ruff_cache \
	       .coverage coverage.xml htmlcov storage eval_reports
	find . -type d -name __pycache__ -not -path "./$(VENV)/*" -exec rm -rf {} +
