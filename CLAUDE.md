# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Copy-trade Telegram Mini App: users subscribe to Hyperliquid top-traders, positions are mirrored automatically via agent-key delegation. The backend polls the Hyperliquid leaderboard and position snapshots, detects changes, and executes matching trades through per-user agent wallets.

**Current state**: Phase 3 complete — the full execution pipeline is live. Signal detection, order building, risk management, Hyperliquid EIP-712 signing, and trade execution are all implemented. The React Mini App frontend (Phase 4) is built. Production deployment infrastructure (Phase 5) is in place.

## Commands

All commands run from the repo root via `make` or directly in `backend/` with `uv run`.

```bash
# Infrastructure
make up          # Start Postgres :5433, Redis :6380, ClickHouse :8123/:9000, backend, workers
make down        # Stop all containers
make logs        # Stream logs

# Backend dev (local, no Docker)
make install     # uv sync — install all deps including dev group
make run         # uvicorn with --reload on :8000
make worker      # Celery worker (queues: default, signals, execution)
make beat        # Celery beat scheduler

# Database
make migrate           # alembic upgrade head
make makemigration     # prompts for name, autogenerates revision
make downgrade         # alembic downgrade -1

# Quality gates (all must pass before commit)
make lint        # ruff check + black --check
make lint-fix    # ruff --fix + black (auto-format)
make typecheck   # mypy app/
make test        # pytest tests/ -v --tb=short
make test-cov    # pytest with --cov=app + HTML report

# Production
make prod-build  # Build all images
make prod-up     # Start with prod overlay (no exposed DB ports, nginx-proxy, certbot)
make ssl         # Issue cert via ACME webroot challenge (run once before prod-up)
make deploy      # Build → alembic upgrade → rolling restart of app services

# Frontend dev (in frontend/)
npm run dev      # Vite dev server on :5173
npm run build    # Production build to dist/
npx tsc --noEmit # Type check without emitting
```

Single test: `cd backend && uv run pytest tests/path/test_file.py::ClassName::test_method -v`

Unit tests only (no Postgres): `cd backend && uv run pytest tests/unit/ -v`

## Architecture

```
copy_trade/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app, CORS, structlog request middleware
│   │   ├── api/                       # HTTP routes (router.py aggregates all)
│   │   │   ├── auth.py                # POST /auth/telegram → JWT
│   │   │   ├── traders.py             # GET /traders (cursor pagination), /traders/{id},
│   │   │   │                          #   /traders/{id}/equity-curve, /positions
│   │   │   ├── ws_traders.py          # WS /ws/traders/{id}/positions (Redis snapshot poll)
│   │   │   ├── subscriptions.py       # CRUD /subscriptions
│   │   │   ├── wallet.py              # /wallet/setup, /approve, /balance, /positions, /status
│   │   │   └── deps.py                # CurrentUser, DBSession typed aliases
│   │   ├── core/
│   │   │   ├── config.py              # Pydantic Settings, lru_cache singleton: `settings`
│   │   │   ├── database.py            # AsyncSessionFactory; get_db() Depends; get_db_session() ctx mgr;
│   │   │   │                          #   get_task_db_session() NullPool variant for Celery
│   │   │   ├── cache.py               # cached_json() read-through; wraps sync Redis in asyncio.to_thread
│   │   │   ├── security.py            # JWT encode/decode, Telegram initData HMAC verification
│   │   │   ├── redis_client.py        # Sync Redis client (get_redis_client())
│   │   │   └── clickhouse_client.py   # clickhouse-connect async client
│   │   ├── models/                    # SQLAlchemy 2.0 ORM (PostgreSQL, asyncpg)
│   │   ├── schemas/                   # Pydantic request/response DTOs
│   │   ├── services/
│   │   │   ├── hyperliquid/
│   │   │   │   ├── info_client.py     # Async httpx: leaderboard, positions, mids, meta
│   │   │   │   ├── exchange_client.py # EIP-712 signing + order/approveAgent submission
│   │   │   │   └── models.py          # Pydantic DTOs for HL API responses
│   │   │   ├── copy_engine/
│   │   │   │   ├── order_builder.py   # signal_to_order(), build_close_order() → OrderParams
│   │   │   │   ├── executor.py        # execute_copy_trade(), close_positions_for_subscription()
│   │   │   │   └── constants.py       # COIN_WHITELIST, MIN_TRADE_USD, IOC_SLIPPAGE, etc.
│   │   │   ├── wallet/
│   │   │   │   └── agent_manager.py   # generate_agent_keypair(), AES-256-GCM encrypt/decrypt
│   │   │   ├── notifications/
│   │   │   │   └── telegram.py        # send_trade_notification() via Bot API
│   │   │   ├── analytics/
│   │   │   │   └── metrics.py         # compute_trader_quality_metrics(), get_trader_stats(),
│   │   │   │                          #   equity curve, closed trades from ClickHouse fills
│   │   │   ├── signal_detector.py     # Pure fn: detect_changes(prev, curr) → [SignalEvent]
│   │   │   ├── signal_publisher.py    # save_signals() → Signal rows in PG
│   │   │   ├── risk_manager.py        # check_subscription_stop_loss() — async, PG queries
│   │   │   └── subscription_service.py
│   │   └── tasks/
│   │       ├── celery_app.py          # Celery config + beat schedule
│   │       ├── hl_tracker.py          # refresh_leaderboard, track_active_traders, poll_trader_positions
│   │       ├── signal_consumer.py     # fan_out_signal → execute_copy_trade.delay()
│   │       ├── analytics_tasks.py     # compute_quality_metrics — top-200 traders, batched 20/2s
│   │       └── execution_tasks.py     # execute_copy_trade, check_stop_losses, monitor_pending_trades
│   ├── alembic/                       # Migrations
│   ├── scripts/
│   │   └── validate_hl_signing.py     # Standalone testnet EIP-712 validation script
│   ├── infra/clickhouse/init.sql      # ClickHouse DDL (trader_positions 90d TTL, trader_pnl 365d TTL)
│   └── tests/
│       ├── conftest.py                # Session-scoped PG test DB + AsyncClient + db_session fixtures
│       ├── unit/
│       │   ├── conftest.py            # No-op DB fixture override — unit tests need no Postgres
│       │   ├── test_security.py
│       │   ├── test_signal_detector.py
│       │   ├── test_hl_info_client.py
│       │   ├── test_hl_signing.py     # EIP-712: connection_id, _sign_l1_action, approveAgent payload
│       │   ├── test_order_builder.py  # signal_to_order, build_close_order
│       │   └── test_risk_manager.py
│       └── api/                       # Integration tests — require Postgres on localhost:5433
│           ├── test_auth.py
│           ├── test_traders.py
│           └── test_subscriptions.py
├── frontend/                          # React + Vite + TypeScript Telegram Mini App
│   ├── src/
│   │   ├── App.tsx                    # Theme → CSS vars, initData auth, onboarding gate
│   │   ├── api/                       # axios wrappers; http.ts has JWT interceptor + 401 reload
│   │   ├── store/authStore.ts         # Zustand: jwt, login(initData), logout
│   │   ├── hooks/
│   │   │   ├── useTelegram.ts         # useMainButton, useBackButton
│   │   │   └── useWebSocket.ts        # useTraderPositionsWS<T> — streams Redis snapshots via WS
│   │   └── pages/                     # TradersPage, TraderDetailPage, WalletPage, MyTradesPage
│   ├── Dockerfile                     # node:22-alpine builder → nginx:1.27-alpine
│   └── nginx.conf                     # SPA try_files, gzip, 1y cache for hashed assets
├── infra/nginx/nginx.conf             # Reverse proxy: HTTP→HTTPS, /api/ → backend:8000, WS upgrade
├── docker-compose.yml                 # Dev stack
├── docker-compose.prod.yml            # Prod overlay: no exposed DB ports, nginx-proxy, certbot
├── .env.example                       # Dev env template
├── .env.prod.example                  # Prod env template with key-generation instructions
└── Makefile
```

### Signal → Execution pipeline

```
Celery Beat (every 5s)
  └─► track_active_traders
          └─► poll_trader_positions.delay(trader_address)
                  ├─ HyperliquidInfoClient.get_positions()
                  ├─ Redis GET hl:snapshot:{address}
                  ├─ detect_changes(prev, curr) → [SignalEvent]
                  ├─ Redis SETEX hl:snapshot:{address} 60s
                  ├─ ClickHouse INSERT trader_positions      [best-effort, never aborts]
                  ├─ save_signals() → Signal rows in PG
                  └─► fan_out_signal.delay(signal_id)
                          └─► execute_copy_trade.delay(signal_id, user_id)
                                  ├─ Redis dedup check (copy:dedup:{signal}:{user})
                                  ├─ risk_manager.check_subscription_stop_loss()
                                  ├─ signal_to_order() → OrderParams | None
                                  ├─ HyperliquidExchangeClient.place_order()
                                  └─ UserTrade record in PG

Celery Beat (every 5 min)   check_stop_losses → deactivate + close positions + Telegram notify
Celery Beat (every 10 min)  refresh_leaderboard → upsert traders in PG + ClickHouse pnl
Celery Beat (every 30s)     monitor_pending_trades → poll HL order status, update UserTrade
Celery Beat (every 1 hr)    compute_quality_metrics → Sharpe/Sortino/win-rate for top-200 traders
```

### Key design decisions

**Auth**: Telegram Mini App sends `initData` (HMAC-SHA256 verified against `TELEGRAM_BOT_TOKEN`, max 24h old). On success a 30-day JWT is issued. Protected routes use `CurrentUser` / `DBSession` typed aliases in `deps.py`.

**Hyperliquid EIP-712 — two distinct signing domains**:
- **L1 actions** (orders, cancels): `chainId=1337`, `name="Exchange"`. Action hash = `keccak256(msgpack(action) + nonce_be64 + vault_flag)`. Signed by the agent private key server-side.
- **`approveAgent`** (user-facing): `chainId=42161` (mainnet) or `421614` (testnet), `primaryType="HyperliquidTransaction:ApproveAgent"`. Signed by the user in-browser via MetaMask `signTypedData`. r/s must be zero-padded to 32 bytes.

**Agent wallet**: Private key AES-256-GCM encrypted with `AGENT_ENCRYPTION_KEY` (32-byte hex). Stored as `nonce || ciphertext || tag` in `user_agents.agent_key_enc`. Rotating `AGENT_ENCRYPTION_KEY` invalidates all stored keys.

**Cursor pagination** (`/traders`): `encode_cursor/decode_cursor` uses base64+JSON of `(sort_value, trader_id)` pairs. Query params: `?cursor=<token>&limit=<n>&period=<week|month|all>&sort=<pnl|roi|volume>`.

**WebSocket auth**: `HTTPBearer` Depends can't be used in WS routes — auth via `?token=<jwt>` query param. Handler polls Redis every 2.5s and sends only on snapshot change.

**Async DB sessions**: Three variants in `core/database.py`:
- `get_db()` — `Depends` for API routes (pooled engine)
- `get_db_session()` — async context manager for service code called from async paths (pooled engine)
- `get_task_db_session()` — **must be used in all Celery tasks** (NullPool engine); `asyncio.run()` creates a new event loop per task invocation and pooled asyncpg connections are bound to the previous loop, causing "Future attached to a different loop" errors

**Signal detection threshold**: `|Δsize| / prev_size ≥ 5%` triggers UPDATE. Side flip (long → short) emits CLOSE + OPEN pair.

### SQLAlchemy model relationships

`users` → `user_agents` (1:N), `subscriptions` (1:N)
`traders` → `trader_stats` (1:N by period), `signals` (1:N), `subscriptions` (1:N)
`subscriptions` → `user_trades` (1:N)
`signals` → `user_trades` (1:N)

## Testing

Unit tests run without any infrastructure. The `tests/unit/conftest.py` overrides the root `setup_database` autouse fixture with a no-op.

Integration tests (`tests/api/`) require Postgres on `localhost:5433`.

**Async SQLAlchemy mock pattern** (required — other patterns fail):
```python
db.execute = AsyncMock(side_effect=[
    MagicMock(scalar_one_or_none=MagicMock(return_value=obj)),
    MagicMock(scalar_one=MagicMock(return_value=val)),
    MagicMock(all=MagicMock(return_value=[row])),
])
```

Test env vars for integration tests:
```
DATABASE_URL=postgresql+asyncpg://copytrade:copytrade@localhost:5433/copytrade_test
REDIS_URL=redis://localhost:6380/0
SECRET_KEY=<min 32 chars>
TELEGRAM_BOT_TOKEN=<any non-empty string>
AGENT_ENCRYPTION_KEY=<64 hex chars>
```

## Environment variables

See `.env.example` for dev, `.env.prod.example` for production. Required non-defaults:
- `TELEGRAM_BOT_TOKEN` — must match real bot token for end-to-end auth
- `AGENT_ENCRYPTION_KEY` — 32-byte hex (64 chars); rotating this invalidates all stored agent keys
- `SECRET_KEY` — JWT signing key, min 32 chars
- `HL_NETWORK` — `mainnet` or `testnet`
- `VITE_DEV_JWT` — (frontend only, `.env.local`) skip Telegram initData auth in local dev
