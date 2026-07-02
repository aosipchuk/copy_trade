# Repository Guidelines

## Project Structure & Module Organization

Backend code lives in `backend/app/`: `api/` has FastAPI routes, `core/` infrastructure, `models/` SQLAlchemy models, `schemas/` Pydantic DTOs, `services/` domain logic, and `tasks/` background jobs. Migrations are in `backend/alembic/`. Tests are split into `backend/tests/unit/` and `backend/tests/api/`. The React + Vite frontend lives in `frontend/src/`, with clients in `api/`, stores in `store/`, hooks in `hooks/`, and screens in `pages/`. Deployment files are in `docker-compose*.yml` and `infra/nginx/`.

## Build, Test, and Development Commands

- `make up`, `make down`, `make logs`: build/start, stop, or inspect the Docker dev stack.
- `make install`: install backend dependencies with `uv sync`.
- `make run`: run the backend locally with reload on port `8001`.
- `make migrate`, `make makemigration`, `make downgrade`: manage Alembic migrations.
- `make lint`, `make lint-fix`, `make typecheck`: run Ruff, Black, and strict mypy checks.
- `make test`, `make test-cov`: run backend tests, optionally with coverage.
- `cd frontend && npm run dev`: start the Vite dev server.
- `cd frontend && npm run build`: type-check and build the frontend.

## Coding Style & Naming Conventions

Backend code targets Python 3.12 with 4-space indentation, Black line length `88`, Ruff linting, and strict mypy. Prefer typed async SQLAlchemy and Pydantic v2 models. Use `snake_case` for Python modules, functions, and variables; use `PascalCase` for classes and models. Frontend code uses TypeScript, React JSX, strict TS settings, and the `@/*` alias. Use `PascalCase` for components and `camelCase` for hooks, stores, and helpers.

## Testing Guidelines

Use pytest for backend tests. Unit tests in `backend/tests/unit/` should not require external infrastructure. API tests in `backend/tests/api/` require Postgres on `localhost:5433`, usually from `make up`. Name test files `test_*.py` and test functions `test_*`. Run focused tests with:

```bash
cd backend && uv run pytest tests/unit/test_signal_detector.py -v
```

## Commit & Pull Request Guidelines

Git history uses short imperative summaries and prefixes such as `feat:`, `fix:`, `chore:`, and `tune:`. Keep commits focused and mention the area when useful, for example `fix(frontend): preserve tab state`. Pull requests should describe the change, note migrations or environment changes, list tests run, and include screenshots for UI changes.

## Security & Agent-Specific Instructions

Do not commit `.env`, private keys, tokens, or generated secrets. Use `.env.example` and `frontend/.env.example` as templates. Never apply fixes directly on a production server unless explicitly requested; use the normal release path of local change, commit, push, then deploy.
