# Admin HL Trader Import Implementation Plan

> **For agentic workers:** execute task-by-task and keep the feature admin-only at every step.

**Goal:** Let configured admins import a Hyperliquid wallet address into the app for analysis and later copy-trading review.

**Architecture:** Add a backend-only admin import flow first. The API validates the current user's Telegram ID against an environment allowlist, normalizes the HL wallet address, fetches available fills from Hyperliquid, computes the same quality metrics used by the leaderboard pipeline, and upserts `traders` plus `trader_stats`. A later frontend stage adds the admin-only button and modal.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async, existing Hyperliquid client and analytics metrics code.

---

### Task 1: Backend Admin Import Foundation

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/api/deps.py`
- Create: `backend/app/services/admin_trader_import.py`
- Modify: `backend/app/schemas/trader.py`
- Create: `backend/app/api/admin_traders.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/services/analytics/metrics.py`
- Modify: `backend/app/services/subscription_service.py`
- Create: `backend/tests/api/test_admin_traders.py`
- Modify: `backend/tests/api/test_subscriptions.py`
- Modify: `backend/tests/api/test_demo_subscriptions.py`
- Modify: `.env.example`
- Modify: `.env.prod.example`

- [x] Add `ADMIN_TELEGRAM_IDS` setting parsed as a comma-separated list of integers.
- [x] Add `require_admin_user` dependency that returns 403 unless `current_user.telegram_id` is in `settings.admin_telegram_ids`.
- [x] Add address normalization and validation for `0x` plus 40 hex characters.
- [x] Allow `compute_trader_quality_metrics(..., use_available_history=True)` to use `userFillsByTime` so manual imports analyze up to HL's currently available history.
- [x] Add service function `import_hl_trader_for_analysis(db, address)` that upserts `Trader`, computes metrics, upserts four `TraderStat` rows, and returns import status.
- [x] Add `POST /api/admin/traders/import` endpoint with admin dependency and slowapi rate limit.
- [x] Require `has_perp_activity=True` for new manual/demo subscription creation.
- [x] Add focused API tests for admin access, invalid address, and successful import.
- [x] Document `ADMIN_TELEGRAM_IDS` in env examples.
- [x] Do not run local tests; project policy requires verification on the server.

### Task 2: Admin UI Entry Point

**Files:**
- Modify: `backend/app/schemas/auth.py`
- Modify: `backend/app/api/auth.py`
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/auth.ts`
- Create: `frontend/src/api/adminTraders.ts`
- Modify: `frontend/src/store/authStore.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/pages/TradersPage.tsx`

- [x] Expose an admin capability from `/auth/me`.
- [x] Add an admin-only button on the traders page.
- [x] Add a modal/input for HL wallet address.
- [x] Call the import endpoint and navigate to `/traders/{id}` after imported/refreshed.
- [x] Show clear states for importing, no fills, no perp activity, invalid address, and non-admin access.

### Task 3: Server Verification

**Files:**
- Add or modify backend API tests under `backend/tests/api/`.

- [x] Test non-admin users get 403 from `POST /api/admin/traders/import`.
- [x] Test invalid wallet addresses get 422 or 400.
- [x] Test a mocked HL import creates a trader and trader stats.
- [ ] Run focused backend tests on the server, not locally.
- [ ] Run frontend build on the server after UI work, not locally.
