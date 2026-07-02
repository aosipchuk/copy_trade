# Model Portfolio Billing Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Phase 5 billing gate so live model portfolio access and future rebalance execution require active payment or beta override.

**Architecture:** Reuse existing `user_portfolio_subscriptions` billing fields from Phase 1, without a new migration. Add a small Stripe-compatible billing service for checkout creation, webhook signature verification, billing status mapping, and beta override checks. Keep actual live subscription creation in Phase 6; Phase 5 only gates readiness.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic v2, httpx, React + Vite + TypeScript.

---

### Task 1: Backend Billing Service And Schemas

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/schemas/portfolio.py`
- Create: `backend/app/services/portfolio/billing.py`

- [ ] Add Stripe and beta override settings with safe defaults.
- [ ] Add request/response schemas for checkout, billing status, and webhook result.
- [ ] Implement Stripe signature verification using `t=...` and `v1=...` HMAC over `timestamp.payload`.
- [ ] Implement billing status helpers: `active` and `trialing` allow live; `past_due`, `paused`, and `canceled` block live/rebalance unless beta override applies.

### Task 2: Backend API

**Files:**
- Create: `backend/app/api/portfolio_billing.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/services/portfolio/activation.py`

- [ ] Add `GET /portfolio-subscriptions/billing/status`.
- [ ] Add `POST /portfolio-subscriptions/billing/checkout`.
- [ ] Add `POST /portfolio-subscriptions/billing/webhook`.
- [ ] Call the billing gate before live activation returns Phase 6 unavailable.

### Task 3: Tests

**Files:**
- Create: `backend/tests/api/test_portfolio_billing.py`
- Modify: `backend/tests/api/test_portfolio_subscriptions.py`

- [ ] Test webhook signature success and failure.
- [ ] Test checkout creates/reuses a live billing holder without generated subscriptions.
- [ ] Test active billing passes the live billing gate.
- [ ] Test `past_due` blocks live activation and rebalance readiness.
- [ ] Test `canceled` keeps local history and blocks rebalance readiness.

### Task 4: Frontend Billing UI

**Files:**
- Modify: `frontend/src/api/portfolios.ts`
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/pages/PortfolioDetailPage.tsx`

- [ ] Add billing status and checkout API client functions.
- [ ] Show pricing/payment CTA on the portfolio detail screen.
- [ ] Show current billing status, period end, beta override, and past-due/canceled blocking states.
- [ ] Keep demo activation flow unchanged.

### Task 5: Docs And Deploy Plan

**Files:**
- Modify: `.env.example`
- Modify: `.env.prod.example`
- Modify: `README.md`
- Modify: `plans/MODEL_PORTFOLIO_ASSISTANT_PLAN.md`

- [ ] Document Stripe env vars and beta override env var.
- [ ] Add Phase 5 implementation notes and deployment checklist.
- [ ] State explicitly that Phase 5 has no new Alembic migration.

### Task 6: Verification And Release

**Commands:**
- `cd backend && uv run pytest tests/unit/test_portfolio_models.py tests/api/test_portfolio_billing.py tests/api/test_portfolio_subscriptions.py -v`
- `cd backend && uv run ruff check .`
- `cd backend && uv run black --check .`
- `cd frontend && npm run build`
- `git commit -m "feat: add model portfolio billing gate"`
- `git push origin main`
- On server: `git pull --ff-only && make deploy`
- Smoke-test: `/api/health`, authenticated billing status endpoint, and unauthenticated webhook signature failure.
