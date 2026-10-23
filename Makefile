# Bazargard - developer and operator shortcuts. `make help` lists targets.
SHELL       := /bin/bash
PYTHON      ?= python3
VENV        ?= .venv
PY          := $(VENV)/bin/python
DATA_DIR    ?= $(CURDIR)/data
PORT        ?= 8506
IMAGE       ?= bazargard
VERSION     := $(shell sed -nE "s/^APP=.*RELEASE='([0-9.]+)'.*/\1/p" backend/obs.py | head -1)
LIARA_APP   ?= bazargard-ai
DATASET_DIR ?=
TEST_CATALOG ?=
RELEASE_ID  ?=

.DEFAULT_GOAL := help
CATALOG     ?= $(DATA_DIR)/catalog.db
BASE        ?= http://127.0.0.1:8611/
.PHONY: bench bench-e2e bench-report help install install-frontend test test-full lint frontend-build run dev-frontend \
        catalog product-index docker-build docker-run liara-package deploy-liara liara-deploy \
        liara-status liara-logs liara-releases liara-rollback deploy-vm backup clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)

install: $(VENV)/bin/activate ## Create .venv and install backend + test dependencies
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements-dev.txt

install-frontend: ## npm ci for the React frontend
	npm --prefix frontend ci --no-audit --no-fund

test: ## Run tests (catalogue-dependent tests skip unless TEST_CATALOG=/path/catalog.db)
	TEST_CATALOG=$(TEST_CATALOG) WEB_GATEWAY_URL= $(PY) -m pytest -q tests

test-full: ## Run the full suite against a real catalogue (TEST_CATALOG required)
	@test -n "$(TEST_CATALOG)" || { echo "set TEST_CATALOG=/abs/path/catalog.db"; exit 1; }
	TEST_CATALOG=$(TEST_CATALOG) WEB_GATEWAY_URL= $(PY) -m pytest -q tests

lint: ## Byte-compile all Python and shell-check deploy scripts (syntax)
	$(PYTHON) -m compileall -q backend tests scripts
	@for f in scripts/*.sh deploy/*/*.sh; do bash -n "$$f" || exit 1; done; echo "lint ok"

frontend-build: ## Production build into frontend/dist (served by FastAPI)
	npm --prefix frontend run build

run: ## Run the API (and built frontend) on 127.0.0.1:$(PORT) with DATA_DIR=./data
	DATA_DIR=$(DATA_DIR) $(VENV)/bin/uvicorn server:app --app-dir backend --host 127.0.0.1 --port $(PORT)

dev-frontend: ## Vite dev server with /api proxied to 127.0.0.1:8506
	npm --prefix frontend run dev

catalog: ## Build data/catalog.db from the Torob CSVs: make catalog DATASET_DIR=/path/to/csvs
	@test -n "$(DATASET_DIR)" || { echo "set DATASET_DIR (folder with products.csv, shops.csv, offers.csv)"; exit 1; }
	$(PY) backend/catalog.py $(DATASET_DIR) $(DATA_DIR)/catalog.db

product-index: ## Optional derived spec index data/canonical-products.db (+ coverage report)
	$(PY) backend/build_product_index.py --catalog $(DATA_DIR)/catalog.db --output $(DATA_DIR)/canonical-products.db --report $(DATA_DIR)/index-report

docker-build: ## Build the production image (multi-stage: Vite build + Python runtime)
	docker build -t $(IMAGE):$(VERSION) -t $(IMAGE):latest .

docker-run: ## Run the image on :8080 with ./data mounted at /data and .env as env file
	docker run --rm -p 8080:8080 -v $(DATA_DIR):/data $(if $(wildcard .env),--env-file .env,) $(IMAGE):latest

liara-package: ## Create dist/bazargard-source.tar.gz for a Liara API/CLI deploy
	scripts/liara-package.sh

deploy-liara: ## Deploy to Liara with the official CLI (needs `liara login`; reads liara.json)
	liara deploy --app $(LIARA_APP) --port 8080 --detach

liara-deploy: ## Deploy to Liara via the REST API (LIARA_API_TOKEN, LIARA_TEAM_ID in env)
	LIARA_APP=$(LIARA_APP) $(PYTHON) scripts/liara_release.py deploy --message "$(VERSION)"

liara-status: ## Liara app state + CPU/memory/disk snapshot
	LIARA_APP=$(LIARA_APP) $(PYTHON) scripts/liara_release.py status

liara-logs: ## Last 30 minutes of Liara runtime logs
	LIARA_APP=$(LIARA_APP) $(PYTHON) scripts/liara_release.py logs --minutes 30

liara-releases: ## List Liara releases (current marked *)
	LIARA_APP=$(LIARA_APP) $(PYTHON) scripts/liara_release.py releases

liara-rollback: ## Roll back to the previous READY release (or RELEASE_ID=...)
	LIARA_APP=$(LIARA_APP) $(PYTHON) scripts/liara_release.py rollback $(RELEASE_ID)

deploy-vm: ## Optional self-hosted VM release: make deploy-vm HOST=ubuntu@<host>
	HOST=$(HOST) deploy/self-hosted/deploy-vm.sh

backup: ## Online backup of memory.db / caches from DATA_DIR into ./backups
	scripts/backup-data.sh $(DATA_DIR) backups

clean: ## Remove caches and build output (keeps data/ and .venv)
	find . -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache frontend/dist dist

bench: ## Benchmark: labels, dataset facts, retrieval (no model calls) and docs/BENCHMARK.md
	CATALOG=$(CATALOG) $(PY) bench/bench.py build
	CATALOG=$(CATALOG) $(PY) bench/bench.py dataset
	CATALOG=$(CATALOG) $(PY) bench/bench.py retrieval
	$(PY) bench/bench.py report

bench-e2e: ## Agent end-to-end benchmark against a running app (real model calls): make bench-e2e BASE=http://...
	CATALOG=$(CATALOG) $(PY) bench/bench.py e2e $(BASE)
	$(PY) bench/bench.py report

bench-report: ## Rebuild docs/BENCHMARK.md from bench/results
	$(PY) bench/bench.py report
