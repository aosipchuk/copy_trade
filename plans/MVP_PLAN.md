# Copy-Trade MVP — Детальный план (10 недель)

**Статус**: Подтверждён (обновлён по результатам Spike)  
**Дата**: 2026-05-29  
**Стек**: Python/FastAPI · Celery · PostgreSQL · Redis · React · Hyperliquid API · Telegram Mini App

---

## Решение по итогам Spike

**Проблема**: CEX leaderboard APIs (Binance, Bybit, Bitget) не работают без браузерных сессий — все возвращают 404.

**Решение**: Hyperliquid как единая экосистема:
- **Данные**: 37,540 трейдеров, полный PnL/ROI, реальные позиции — через открытый API
- **Исполнение**: Прямо на Hyperliquid order book (те же рынки: BTC, ETH, SOL...)
- **Кошелёк**: Hyperliquid Agent Wallet — встроенная делегация, проще ERC-4337

**Выигрыш от пивота**:
- На 2 недели короче MVP (10 вместо 12)
- Нет проблемы "перевода" CEX→DEX
- Нет slippage на свопах (order book)
- Встроенная делегация без смарт-контрактов
- Стабильный on-chain API, не ломается

---

## MVP Scope

**Включено:**
- Данные трейдеров: **Hyperliquid** (37k трейдеров, перпы BTC/ETH/SOL и пр.)
- Исполнение: **Hyperliquid perp DEX** (order book, USDC collateral)
- Кошелёк: **Hyperliquid Agent Wallet** (делегированные ключи)
- UI: Telegram Mini App — аналитика, подписки, кошелёк
- Пополнение: USDC через Arbitrum bridge (или напрямую)

**Исключено:**
- CEX данные (v2 full product)
- Bybit, Bitget, Huobi
- Смарт-контракты на заказ (Hyperliquid обрабатывает это)
- Внешний аудит

---

## Архитектура MVP

```
Telegram Mini App (React + TypeScript)
         │ HTTPS + WebSocket
         ▼
FastAPI Gateway (JWT auth via Telegram initData)
     │              │               │
Analytics        Wallet          CopyTrade
Service          Service          Engine
  │                │                │
ClickHouse     Agent Key         Celery
(metrics)      Manager           Workers
  │                │                │
Hyperliquid    Hyperliquid     Hyperliquid
Stats API      Exchange API    Exchange API
(leaderboard,  (place orders,  (place/close
 positions)     agent auth)     positions)

Celery Beat ──► Hyperliquid Position Tracker (every 5s)
                      │
                 Change detection
                      │
                 Redis pub/sub (signals)
                      │
                 Copy Engine fan-out
                      │
               execute_copy_trade(signal, user)
```

---

## Hyperliquid API — ключевые эндпойнты

### Info API (read-only, бесплатно)
```
POST https://api.hyperliquid.xyz/info
  {"type": "leaderboard"}               → 37k трейдеров с PnL/ROI
  {"type": "clearinghouseState", "user": "0x..."} → позиции трейдера
  {"type": "userFills", "user": "0x..."}          → история сделок
  {"type": "allMids"}                   → текущие цены
  {"type": "meta"}                      → 230 рынков

GET https://stats-data.hyperliquid.xyz/Mainnet/leaderboard → леадерборд
```

### Exchange API (торговля, требует подписи)
```
POST https://api.hyperliquid.xyz/exchange
  Action: "order"     → открыть/закрыть позицию
  Action: "cancel"    → отменить ордер
  Action: "approveAgent" → выдать права агенту (ОДИН РАЗ от пользователя)
```

### Agent Wallet — делегация
```
1. Пользователь подписывает approveAgent(agent_address) — один раз
2. Наш backend использует agent_address для торговли от его имени
3. Никаких смарт-контрактов, никакого ERC-4337
4. Ограничения: агент может только торговать (не выводить средства)
```

---

## Схема БД (MVP)

```sql
-- PostgreSQL

CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    telegram_id     BIGINT UNIQUE NOT NULL,
    username        TEXT,
    hl_address      TEXT,           -- Hyperliquid wallet address (пользователя)
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE user_agents (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT REFERENCES users(id),
    agent_address   TEXT NOT NULL,  -- наш agent address
    agent_key_enc   BYTEA NOT NULL, -- agent private key (зашифрован AES-256-GCM)
    approved_at     TIMESTAMPTZ,    -- когда пользователь подписал approveAgent
    is_active       BOOL DEFAULT true
);

CREATE TABLE traders (
    id              BIGSERIAL PRIMARY KEY,
    hl_address      TEXT UNIQUE NOT NULL,   -- Hyperliquid address
    display_name    TEXT,                   -- ENS или короткое имя
    is_active       BOOL DEFAULT true,
    last_seen_at    TIMESTAMPTZ
);

CREATE TABLE trader_stats (
    trader_id       BIGINT REFERENCES traders(id),
    period          TEXT NOT NULL,          -- 'day', 'week', 'month', 'allTime'
    pnl_usd         NUMERIC(20,4),
    roi_pct         NUMERIC(10,6),
    volume_usd      NUMERIC(20,2),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (trader_id, period)
);

CREATE TABLE signals (
    id              BIGSERIAL PRIMARY KEY,
    trader_id       BIGINT REFERENCES traders(id),
    signal_type     TEXT NOT NULL,  -- 'OPEN', 'CLOSE', 'UPDATE'
    coin            TEXT NOT NULL,  -- 'BTC', 'ETH', 'SOL'...
    side            TEXT,           -- 'long', 'short'
    size            NUMERIC(20,8),  -- in contracts
    entry_price     NUMERIC(20,4),
    leverage        NUMERIC(5,2),
    detected_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE subscriptions (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT REFERENCES users(id),
    trader_id           BIGINT REFERENCES traders(id),
    max_allocation_usd  NUMERIC(20,2) NOT NULL,
    copy_ratio_pct      NUMERIC(5,2) DEFAULT 100,
    stop_loss_pct       NUMERIC(5,2) DEFAULT 20,
    max_leverage        NUMERIC(5,2) DEFAULT 10,
    is_active           BOOL DEFAULT true,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE user_trades (
    id              BIGSERIAL PRIMARY KEY,
    subscription_id BIGINT REFERENCES subscriptions(id),
    signal_id       BIGINT REFERENCES signals(id),
    hl_order_id     BIGINT,         -- Hyperliquid order ID
    coin            TEXT,
    side            TEXT,
    size            NUMERIC(20,8),
    price           NUMERIC(20,4),
    status          TEXT DEFAULT 'pending',  -- pending|filled|failed|cancelled
    error_msg       TEXT,
    executed_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_subscriptions_trader ON subscriptions(trader_id) WHERE is_active;
CREATE INDEX idx_subscriptions_user ON subscriptions(user_id);
CREATE INDEX idx_user_trades_subscription ON user_trades(subscription_id);
CREATE INDEX idx_signals_trader ON signals(trader_id, detected_at DESC);
CREATE INDEX idx_traders_active ON traders(is_active) WHERE is_active;
```

```sql
-- ClickHouse

CREATE TABLE trader_positions (
    trader_address  String,
    coin            String,
    side            LowCardinality(String),
    szi             Float64,        -- position size
    entry_px        Float64,
    unrealized_pnl  Float64,
    leverage        Float32,
    snapshot_at     DateTime
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(snapshot_at)
ORDER BY (trader_address, coin, snapshot_at)
TTL snapshot_at + INTERVAL 90 DAY;

CREATE TABLE trader_pnl (
    trader_address  String,
    ts              DateTime,
    pnl             Float64,
    roi             Float64,
    period          LowCardinality(String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY (trader_address, period, ts);
```

---

## Week-by-Week Plan

---

### Week 1–2: Foundation

**Цель**: Монорепо, Docker, БД, CI/CD, Telegram auth.

**Структура проекта:**
```
copy_trade/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routers
│   │   ├── core/           # config, db, security, logging
│   │   ├── models/         # SQLAlchemy models
│   │   ├── schemas/        # Pydantic v2 schemas
│   │   ├── services/       # business logic
│   │   │   ├── hyperliquid/ # HL API clients
│   │   │   ├── copy_engine/ # signal → trade pipeline
│   │   │   └── analytics/   # ROI/metrics calculators
│   │   └── tasks/          # Celery tasks
│   ├── alembic/
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── api/            # API clients
│   │   ├── components/     # UI components
│   │   ├── pages/          # screens
│   │   └── store/          # Zustand state
│   ├── package.json
│   └── Dockerfile
├── plans/
├── docker-compose.yml
└── .github/workflows/ci.yml
```

**Задачи:**
- [ ] `pyproject.toml`: fastapi, sqlalchemy[asyncio], celery[redis], redis[hiredis], pydantic-settings, alembic, asyncpg, httpx, tenacity, structlog, eth-account (для подписи HL сообщений), cryptography, python-jose
- [ ] `docker-compose.yml`: postgres:16, redis:7-alpine, clickhouse/clickhouse-server:24, app, celery-worker, celery-beat
- [ ] `app/core/config.py`: Pydantic Settings — DB_URL, REDIS_URL, CLICKHOUSE_URL, TELEGRAM_BOT_TOKEN, AGENT_ENCRYPTION_KEY
- [ ] `app/core/database.py`: async SQLAlchemy + ClickHouse async client
- [ ] Alembic: начальная миграция (все таблицы из схемы выше)
- [ ] ClickHouse: DDL для `trader_positions` и `trader_pnl`
- [ ] `app/api/auth.py`: валидация Telegram `initData` (HMAC-SHA256), JWT выдача
- [ ] `GET /health`, `GET /version`
- [ ] Structlog: JSON logging, request_id middleware
- [ ] GitHub Actions CI: ruff check, mypy --strict, pytest, docker build
- [ ] `Makefile`: up, down, test, migrate, lint, shell

**Deliverable**: `make up` → все сервисы запущены, `make test` → зелёный, CI проходит.

---

### Week 3: Hyperliquid Data Tracker

**Цель**: Собирать позиции трейдеров с Hyperliquid, детектить изменения.

**Задачи:**
- [ ] `app/services/hyperliquid/info_client.py` — async httpx клиент:
  ```python
  async def get_leaderboard() -> list[LeaderboardRow]
  async def get_positions(address: str) -> list[Position]
  async def get_fills(address: str) -> list[Fill]
  async def get_all_mids() -> dict[str, str]
  ```
  - Retry через `tenacity`: 3 попытки, exponential backoff
  - Rate limit: 1200 req/min (Hyperliquid лимит)
  - Pydantic v2 модели для всех ответов

- [ ] `app/tasks/hl_leaderboard.py`: Celery Beat task, каждые 10 минут:
  - Получить топ-500 трейдеров из леадерборда
  - Upsert в `traders` и `trader_stats` (PostgreSQL)
  - Записать PnL в ClickHouse `trader_pnl`

- [ ] `app/tasks/hl_positions.py`: Celery task `track_trader_positions`:
  - Запускается для каждого активно отслеживаемого трейдера (у кого есть подписчики)
  - Celery Beat: каждые 5 секунд (через `apply_async` с countdown, не Beat для каждого)
  - Snapshot текущих позиций → ClickHouse
  - Signal detection → Redis pub/sub

- [ ] `app/services/signal_detector.py`:
  ```python
  def detect_changes(prev: list[Position], curr: list[Position]) -> list[Signal]
  ```
  - Новая позиция (coin+side появился) → `SIGNAL_OPEN`
  - Позиция исчезла → `SIGNAL_CLOSE`
  - `|size_change| / prev_size > 0.05` → `SIGNAL_UPDATE`
  - Хранит последний снэпшот в Redis (TTL 60s)

- [ ] `app/services/signal_publisher.py`:
  - `publish_signal(signal)` → Redis channel `hl:signals:{trader_id}`

**Deliverable**: 500 трейдеров отслеживаются, сигналы публикуются в Redis при изменении позиций.

---

### Week 4: Analytics API

**Цель**: REST API с метриками трейдеров для Mini App.

**Задачи:**
- [ ] `app/services/analytics/metrics.py`:
  - `get_trader_stats(trader_id, period)` → из PostgreSQL `trader_stats`
  - `get_equity_curve(trader_address, period)` → из ClickHouse `trader_pnl`, агрегация по времени
  - `get_open_positions(trader_address)` → последний снэпшот из ClickHouse
  - `get_max_drawdown(trader_address, period)` → скользящий максимум из equity curve

- [ ] Вычисляемые метрики (на основе HL данных):
  - **ROI**: напрямую из `windowPerformances[period].roi`
  - **PnL**: из `windowPerformances[period].pnl`
  - **Volume**: из `windowPerformances[period].vlm`
  - **Max Drawdown**: из ClickHouse equity curve
  - **Win Rate**: `positive_fills / total_fills` из ClickHouse
  - **Avg Trade Size**: `volume / trade_count`

- [ ] API endpoints:
  - `GET /traders?period=week&sort=roi&limit=50&offset=0` → список с пагинацией
  - `GET /traders/{id}` → детали + stats
  - `GET /traders/{id}/equity-curve?period=week` → `[{ts, pnl, roi}]`
  - `GET /traders/{id}/positions` → текущие открытые позиции
  - `GET /traders/{id}/fills?limit=50` → последние сделки

- [ ] Redis кэш:
  - `/traders` list → TTL 30s
  - `/traders/{id}/stats` → TTL 30s
  - `/traders/{id}/positions` → TTL 5s (fast refresh)

- [ ] WebSocket endpoint `WS /ws/traders/{id}/positions` → стрим обновлений позиций

**Deliverable**: API отдаёт полную аналитику, Mini App может отображать данные.

---

### Week 5: Hyperliquid Wallet + Agent Setup

**Цель**: Пользователь создаёт HL кошелёк, выдаёт агент-права нашему серверу.

**Hyperliquid Agent Wallet flow:**
```
1. Пользователь входит в Mini App
2. Frontend генерирует случайный EVM-keypair (agent keypair) — в памяти
3. Frontend показывает что будет разрешено: только торговля, не вывод средств
4. Пользователь подписывает approveAgent(agent_address) своим кошельком
   (через Telegram Mini App wallet или WalletConnect)
5. Frontend отправляет agent_private_key + signature на бэкенд
6. Бэкенд хранит agent_private_key зашифрованным, привязывает к user
7. Бэкенд может торговать от имени пользователя через agent_key
```

**Задачи:**

**Backend:**
- [ ] `app/services/wallet/agent_manager.py`:
  - `generate_agent_keypair()` → (address, private_key)
  - `encrypt_agent_key(private_key)` → bytes (AES-256-GCM, ключ из env)
  - `decrypt_agent_key(encrypted)` → private_key
  - `build_approve_agent_payload(agent_address, user_address)` → signed message для frontend

- [ ] `app/services/hyperliquid/exchange_client.py`:
  - `approve_agent(user_address, agent_address, signature)` — финализация делегации
  - `place_order(agent_key, user_address, coin, is_buy, sz, limit_px, order_type)` → order_id
  - `close_position(agent_key, user_address, coin, sz)` → order_id
  - `get_order_status(order_id)` → filled/open/cancelled
  - Подпись: `eth_account.sign_typed_data()` (EIP-712 для HL действий)

- [ ] API endpoints:
  - `POST /wallet/setup` — создать agent keypair, вернуть agent_address + payload для подписи
  - `POST /wallet/approve` — получить подпись от frontend, финализировать делегацию
  - `GET /wallet/balance` — USDC/equity баланс на HL аккаунте пользователя
  - `GET /wallet/positions` — текущие open positions пользователя
  - `DELETE /wallet/agent` — отозвать делегацию

**Frontend:**
- [ ] Wallet setup flow:
  - Шаг 1: показать HL deposit address (пользовательский EVM address) + QR
  - Шаг 2: "Authorize copy-trading" → подписать `approveAgent` через MetaMask/WalletConnect
  - Альтернатива: Privy embedded wallet (если пользователь без внешнего кошелька)
- [ ] `GET /wallet/balance` → показать баланс
- [ ] Privy SDK интеграция (опционально, для пользователей без кошелька):
  - Создаёт EVM кошелёк внутри Mini App
  - Пользователь никогда не видит приватный ключ

**Deliverable**: Пользователь авторизован на HL, бэкенд может размещать ордера от его имени.

---

### Week 6: Order Execution Engine

**Цель**: Исполнять ордера на Hyperliquid от имени пользователя.

**Задачи:**
- [ ] `app/services/copy_engine/order_builder.py`:
  - `signal_to_order(signal, subscription, user_balance)` → OrderParams
  - Position sizing: `min(subscription.max_allocation_usd, user_equity * copy_ratio / 100)`
  - Leverage: `min(signal.leverage, subscription.max_leverage)`
  - Price: current mid price ± 0.1% (limit order с immediate-or-cancel)

- [ ] `app/services/copy_engine/executor.py`:
  - `execute_copy_trade(signal_id, user_id)` → UserTrade
  - Pre-checks:
    - [ ] Agent key активен
    - [ ] HL баланс >= min trade size ($10)
    - [ ] Coin существует на HL (из meta)
    - [ ] Нет дублирующего сигнала за 30 сек (Redis dedup key)
    - [ ] Подписка не превысила stop-loss
  - Order type: IOC (immediate-or-cancel) — либо исполняется сразу, либо нет
  - Запись в `user_trades`

- [ ] `app/tasks/execution_tasks.py`:
  - `execute_copy_trade` — Celery task с retry (3 попытки, 5s delay)
  - Обновить `user_trades.status` по результату

- [ ] `app/tasks/signal_consumer.py`:
  - Redis consumer, слушает `hl:signals:*`
  - Fan-out: один сигнал → N активных подписчиков
  - Для каждого подписчика: `execute_copy_trade.delay(signal_id, user_id)`

- [ ] Transaction monitoring Celery Beat task:
  - Каждые 30 сек: проверить `pending` trades, обновить статус
  - Timeout 2 мин → статус `failed`

- [ ] Telegram Bot уведомления:
  - Сделка исполнена: `"✅ Скопирована сделка: BTC Long +0.01 @ $67,200"`
  - Ошибка: `"❌ Не удалось скопировать сделку BTC: недостаточно средств"`

**Deliverable**: Полный pipeline от сигнала до ордера на HL testnet.

---

### Week 7: Copy-Trade Engine + Risk Management

**Цель**: Subscription CRUD, stop-loss, portfolio controls.

**Задачи:**
- [ ] Subscription CRUD API:
  - `POST /subscriptions` — параметры: trader_id, max_allocation_usd, copy_ratio, stop_loss_pct, max_leverage
  - `GET /subscriptions` — список с текущим PnL (JOIN user_trades)
  - `PATCH /subscriptions/{id}` — изменить параметры
  - `DELETE /subscriptions/{id}` — отписаться + закрыть все открытые копи-позиции

- [ ] Risk Management service:
  - `check_subscription_stop_loss(subscription_id)` → bool
    - Считает cumulative PnL из `user_trades`
    - Если loss > `stop_loss_pct * max_allocation_usd` → деактивировать + уведомить
  - `check_portfolio_risk(user_id)` → bool
    - Не более 3 активных подписок по умолчанию
    - Общий allocation не более equity * 0.8

- [ ] Auto-close при SIGNAL_CLOSE:
  - Если пользователь имеет открытую HL позицию по тому же coin/side → закрыть
  - `close_position(agent_key, user_address, coin, current_size)`

- [ ] Celery Beat: `check_stop_losses` каждые 5 мин

- [ ] Symbol filter:
  - Whitelist монет для MVP (топ-20 по объёму на HL): BTC, ETH, SOL, ARB, AVAX, DOGE, LINK, BNB, OP, SUI, INJ, APT, ATOM, MATIC, LTC, NEAR, FIL, ADA, XRP, TON

**Deliverable**: Полный copy-trade цикл с risk management.

---

### Week 8–9: Telegram Mini App

**Цель**: Полный UI — аналитика, подписки, кошелёк.

**Stack**: React 18 + TypeScript + Vite + TailwindCSS + @twa-dev/sdk + lightweight-charts

**Week 8 — Auth + Traders:**
- [ ] Vite + React + TS init, TailwindCSS, @twa-dev/sdk, axios, zustand, react-router-dom v6
- [ ] Auth flow:
  - `WebApp.initData` → POST `/auth/telegram` → JWT в localStorage
  - axios interceptor: добавлять `Authorization: Bearer {jwt}`
- [ ] Bottom tab nav: **Traders** / **My Trades** / **Wallet**
- [ ] `/traders` — главный экран:
  - Карточки трейдеров: address (сокращённый), ROI%, PnL, Volume, открытые позиции
  - Период переключатель: Day / Week / Month / All Time
  - Сортировка: ROI / PnL / Volume
  - Infinite scroll (следующая страница)
- [ ] `/traders/:address` — страница трейдера:
  - Equity curve (lightweight-charts, line chart)
  - Открытые позиции (таблица: Coin, Side, Size, Entry, Unrealized PnL)
  - Статистика блок: ROI, PnL, Volume, Max DD
  - Кнопка **Subscribe** (основная Telegram кнопка `WebApp.MainButton`)

**Week 9 — Subscribe + Wallet + My Trades:**
- [ ] Subscribe modal/sheet:
  - Max allocation (USDC, slider: $50–$10,000)
  - Copy ratio (%, 10–100%)
  - Stop-loss (%, 5–50%)
  - Max leverage (1x–40x)
  - Кнопка Confirm → POST /subscriptions
- [ ] Wallet screen `/wallet`:
  - HL address (QR-код для пополнения)
  - Balance: equity + available margin
  - Pending balance (если ещё не задеплоировано)
  - Wallet Setup wizard (если агент не настроен):
    - Шаг 1: Generate / connect wallet
    - Шаг 2: Sign approveAgent
    - Шаг 3: Deposit USDC
- [ ] My Trades `/my-trades`:
  - Активные подписки: трейдер, PnL, allocation, статус
  - История сделок: Coin, Side, Size, Price, Status, Timestamp
  - Expand/collapse для каждой подписки
  - Edit/Unsubscribe actions
- [ ] Onboarding (3 шага для новых пользователей):
  - Welcome → Setup Wallet → Find Traders
- [ ] WebSocket: real-time обновление позиций трейдера на странице
- [ ] Адаптация под `WebApp.themeParams` (dark / light)
- [ ] `WebApp.BackButton` для навигации назад
- [ ] `WebApp.MainButton` как primary action (Subscribe, Confirm)

**Deliverable**: Полный Mini App, все экраны работают с реальными данными.

---

### Week 10: Testing + Testnet Launch

**Цель**: Интеграционные тесты, деплой на сервер, запуск с тестовым ботом.

**Задачи:**

**Testing:**
- [ ] `pytest` + `pytest-asyncio` настройка
- [ ] `tests/framework/` — base classes, fixtures, helpers
- [ ] Hyperliquid mock (respx): фиксированные ответы для info + exchange API
- [ ] Unit тесты:
  - Signal detector (все 3 типа сигналов)
  - Order builder (position sizing, leverage capping)
  - Agent key encrypt/decrypt
  - Subscription stop-loss logic
- [ ] Integration тесты:
  - Analytics API endpoints (реальный ClickHouse в Docker)
  - Subscription CRUD
  - Copy-trade pipeline (mock HL exchange → UserTrade запись)
  - Telegram initData валидация
- [ ] Coverage > 70% критического кода

**Deploy:**
- [ ] `.env.prod` для production env vars (не в git)
- [ ] Docker Compose prod override: без exposed ports на DB, с nginx
- [ ] Nginx config: `/api/` → backend, `/` → frontend static
- [ ] SSL через Certbot (Let's Encrypt)
- [ ] Зарегистрировать Telegram Bot через @BotFather
- [ ] Настроить WebApp URL в боте (`/setmenubutton`)
- [ ] Деплой на VPS (Digital Ocean / Hetzner $20/mo достаточно для MVP)

**Smoke test (реальный):**
- [ ] Открыть Mini App через бота
- [ ] Видеть список трейдеров с реальными данными
- [ ] Настроить кошелёк (Hyperliquid testnet)
- [ ] Подписаться на трейдера
- [ ] Дождаться сигнала или создать тестовый вручную
- [ ] Убедиться что ордер появился в HL testnet

**Deliverable**: Работающий MVP, доступный через Telegram бот.

---

## Зависимости (pyproject.toml)

```toml
[tool.poetry.dependencies]
python = "^3.12"
fastapi = "^0.115"
uvicorn = {extras = ["standard"], version = "^0.32"}
sqlalchemy = {extras = ["asyncio"], version = "^2.0"}
alembic = "^1.14"
asyncpg = "^0.30"
pydantic = "^2.9"
pydantic-settings = "^2.6"
celery = {extras = ["redis"], version = "^5.4"}
redis = {extras = ["hiredis"], version = "^5.2"}
httpx = "^0.28"
tenacity = "^9.0"
structlog = "^24.4"
clickhouse-driver = "^0.2"
cryptography = "^43.0"
python-jose = {extras = ["cryptography"], version = "^3.3"}
eth-account = "^0.13"      # EIP-712 подпись для Hyperliquid
aiogram = "^3.14"          # Telegram Bot SDK

[tool.poetry.group.dev.dependencies]
pytest = "^8.3"
pytest-asyncio = "^0.24"
pytest-cov = "^6.0"
respx = "^0.21"
ruff = "^0.8"
mypy = "^1.13"
black = "^24.10"
```

---

## Риски MVP (обновлено)

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| Hyperliquid rate limit (1200/min) | Средняя | Батчинг запросов, per-trader backoff |
| HL testnet нестабилен | Низкая | Быстрый переход на mainnet тест (малые суммы) |
| Пользователь без EVM-кошелька | Высокая | Privy embedded wallet как fallback |
| approveAgent rejection (пользователь не подпишет) | Средняя | Объяснить что агент не может выводить средства |
| Agent key компрометация | Низкая | AES-256-GCM шифрование, env key в secrets, rotatable |
| HL API изменится | Очень низкая | On-chain протокол, API стабилен |

---

## Definition of Done

- [ ] Пользователь заходит в Telegram бот → видит Mini App
- [ ] Список топ-100 трейдеров с ROI%, PnL, объёмом
- [ ] Equity curve и открытые позиции на странице трейдера
- [ ] Создаёт/подключает EVM кошелёк → подписывает `approveAgent` (одно действие)
- [ ] Видит USDC баланс на Hyperliquid
- [ ] Подписывается на трейдера с настройками лимитов
- [ ] При изменении позиции трейдера — автоматически размещается ордер на HL
- [ ] Получает Telegram уведомление об исполненной сделке
- [ ] Видит историю своих скопированных сделок
- [ ] Всё работает без выхода из Telegram
