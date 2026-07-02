.PHONY: up down build logs shell test lint typecheck migrate makemigrations install clean \
        prod-up prod-down prod-logs prod-build prod-target prod-check-target \
        deploy deploy-backend deploy-frontend run

PROD_ENV_FILE ?= $(if $(wildcard ./.env.prod),.env.prod,.env)

ifneq (,$(wildcard ./$(PROD_ENV_FILE)))
  include $(PROD_ENV_FILE)
  export
endif

# ─── Docker ──────────────────────────────────────────────────────────────────

up:
	docker compose up -d --build

down:
	docker compose down

build:
	docker compose build

rebuild:
	docker compose build frontend backend
	docker compose up -d --no-deps frontend backend

logs:
	docker compose logs -f

shell:
	docker compose exec backend bash

# ─── Production ───────────────────────────────────────────────────────────────

PROD_COMPOSE = APP_ENV_FILE=$(PROD_ENV_FILE) docker compose --env-file $(PROD_ENV_FILE) -f docker-compose.yml -f docker-compose.prod.yml
PROD_REQUIRED_VARS = ENVIRONMENT SECRET_KEY TELEGRAM_BOT_TOKEN AGENT_ENCRYPTION_KEY \
	TELEGRAM_WEBHOOK_SECRET POSTGRES_PASSWORD DATABASE_URL REDIS_PASSWORD HL_NETWORK \
	BUILDER_ADDRESS \
	VITE_WALLETCONNECT_PROJECT_ID VITE_API_URL VITE_WS_URL VITE_APP_URL \
	DEPLOY_TARGET DEPLOY_HOST DEPLOY_USER DEPLOY_PATH DEPLOY_BRANCH DEPLOY_EDGE \
	DOMAIN PUBLIC_URL HEALTHCHECK_URL TELEGRAM_MINI_APP_URL

prod-build:
	$(PROD_COMPOSE) build

prod-up:
	$(PROD_COMPOSE) up -d

prod-down:
	$(PROD_COMPOSE) down

prod-logs:
	$(PROD_COMPOSE) logs -f

prod-target:
	@echo "Env file: $(PROD_ENV_FILE)"
	@echo "Target: $${DEPLOY_TARGET:-<unset>}"
	@echo "SSH: $${DEPLOY_USER:-<unset>}@$${DEPLOY_HOST:-<unset>}"
	@echo "Path: $${DEPLOY_PATH:-<unset>}"
	@echo "Branch: $${DEPLOY_BRANCH:-<unset>}"
	@echo "Edge: $${DEPLOY_EDGE:-<unset>}"
	@echo "Public URL: $${PUBLIC_URL:-<unset>}"
	@echo "Health check: $${HEALTHCHECK_URL:-<unset>}"

prod-check-target:
	@missing=0; \
	for var in $(PROD_REQUIRED_VARS); do \
		val=$$(printenv "$$var" || true); \
		if [ -z "$$val" ]; then \
			echo "missing $$var"; missing=1; \
		elif printf '%s\n' "$$val" | grep -Eq '(REPLACE_WITH|YOUR_|your\.domain\.com|https://your\.domain\.com|wss://your\.domain\.com|change-me)'; then \
			echo "placeholder $$var=$$val"; missing=1; \
		fi; \
	done; \
	if [ "$$ENVIRONMENT" != "production" ]; then \
		echo "invalid ENVIRONMENT=$$ENVIRONMENT"; missing=1; \
	fi; \
	if [ "$(PROD_ENV_FILE)" = ".env" ]; then \
		echo "warning: using .env for prod; prefer .env.prod"; \
	fi; \
	if [ "$$missing" -ne 0 ]; then \
		echo "Fill $(PROD_ENV_FILE) using .env.prod.example."; exit 1; \
	fi; \
	echo "Production target OK"; \
	echo "Target: $$DEPLOY_TARGET ($$DEPLOY_USER@$$DEPLOY_HOST:$$DEPLOY_PATH, branch $$DEPLOY_BRANCH)"; \
	echo "Public URL: $$PUBLIC_URL"; \
	echo "Health check: $$HEALTHCHECK_URL"

# Full deploy: build → migrate → restart (use when frontend changed)
deploy:
	$(PROD_COMPOSE) build backend frontend
	$(PROD_COMPOSE) run --rm backend sh -c "uv run alembic upgrade head"
	$(PROD_COMPOSE) up -d --no-deps backend frontend

# Backend-only deploy: ~30s (use when only Python code or migrations changed)
deploy-backend:
	$(PROD_COMPOSE) build backend
	$(PROD_COMPOSE) run --rm backend sh -c "uv run alembic upgrade head"
	$(PROD_COMPOSE) up -d --no-deps backend

# Frontend-only deploy: ~25 min on VPS (use when only UI changed, no migrations)
deploy-frontend:
	$(PROD_COMPOSE) build frontend
	$(PROD_COMPOSE) up -d --no-deps frontend

# ─── Development ─────────────────────────────────────────────────────────────

install:
	cd backend && uv sync

run:
	cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# ─── Database ────────────────────────────────────────────────────────────────

migrate:
	cd backend && uv run alembic upgrade head

makemigration:
	@read -p "Migration name: " name; \
	cd backend && uv run alembic revision --autogenerate -m "$$name"

downgrade:
	cd backend && uv run alembic downgrade -1

# ─── Quality ──────────────────────────────────────────────────────────────────

lint:
	cd backend && uv run ruff check .
	cd backend && uv run black --check .

lint-fix:
	cd backend && uv run ruff check --fix .
	cd backend && uv run black .

typecheck:
	cd backend && uv run mypy app/

test:
	cd backend && uv run pytest tests/ -v --tb=short

test-cov:
	cd backend && uv run pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=html

# ─── Cleanup ─────────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	cd backend && rm -rf .coverage htmlcov/ .mypy_cache/ .ruff_cache/ .pytest_cache/
