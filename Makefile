.PHONY: up down build logs shell test lint typecheck migrate makemigrations install clean \
        prod-up prod-down prod-logs prod-build ssl deploy

ifneq (,$(wildcard ./.env))
  include .env
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

PROD_COMPOSE = docker compose -f docker-compose.yml -f docker-compose.prod.yml

prod-build:
	$(PROD_COMPOSE) build

prod-up:
	$(PROD_COMPOSE) up -d

prod-down:
	$(PROD_COMPOSE) down

prod-logs:
	$(PROD_COMPOSE) logs -f

# Obtain/renew SSL certificate (run once before prod-up, requires DOMAIN in .env)
ssl:
	$(PROD_COMPOSE) run --rm certbot certonly \
		--webroot -w /var/www/certbot \
		--email admin@$(DOMAIN) \
		--agree-tos --no-eff-email \
		-d $(DOMAIN)

# Full deploy: build → migrate → restart
deploy:
	$(PROD_COMPOSE) build backend frontend
	$(PROD_COMPOSE) run --rm backend sh -c "uv run alembic upgrade head"
	$(PROD_COMPOSE) up -d --no-deps backend celery-worker celery-beat frontend

# ─── Development ─────────────────────────────────────────────────────────────

install:
	cd backend && uv sync

run:
	cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

worker:
	cd backend && uv run celery -A app.tasks.celery_app worker --loglevel=info -Q default,signals,execution

beat:
	cd backend && uv run celery -A app.tasks.celery_app beat --loglevel=info

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
