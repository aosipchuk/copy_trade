# Copy-Trade Telegram Service — Master Development Plan

**Дата создания**: 2026-05-29  
**Статус**: Ожидает подтверждения  

---

## 1. Постановка задачи

Копитрейдинг-сервис, работающий исключительно внутри Telegram. Пользователи:
- Видят аналитику топ-трейдеров с CEX (Binance, Bybit, Bitget, Huobi): ROI, просадка, P&L, кривая доходности
- Подписываются на копирование сделок
- Все сделки исполняются через DEX (1inch, Jupiter и пр.) напрямую из встроенного кошелька
- Приватные ключи не передаются серверу — используется MPC-кошелёк или Smart Wallet с Session Key

**Ключевое ограничение**: только Telegram Mini App, никаких внешних приложений, никакого CEX-исполнения.

---

## 2. Критические риски и ограничения

### 2.1 Доступ к данным CEX (HIGH RISK)

**Проблема**: CEX не раскрывают реальные позиции/сделки трейдеров в реальном времени через публичный API. Публичные леадербоарды показывают только агрегированную статистику.

**Что реально доступно**:
| Биржа | Публичный API | Что даёт |
|-------|--------------|----------|
| Binance | `/futures/leaderboard` | Top traders list, snapshots позиций (с задержкой) |
| Bybit | `/v5/copy-trading` | Public master traders, PnL stats |
| Bitget | `/api/mix/v1/trace/public-traders` | Elite trader positions (если публичный профиль) |
| Huobi/HTX | ограниченно | Только агрегированная статистика |

**Решение**: Опрашивать снэпшоты открытых позиций каждые 5-15 секунд. Изменение позиции = сигнал. Задержка 5-30 секунд неизбежна.

**Fallback**: Платный доступ к data-провайдерам (Nansen, Hyperliquid публичные данные, CoinGlass) для более богатых данных.

### 2.2 Signal Translation (HIGH RISK)

**Проблема**: CEX трейдер торгует фьючерсами с плечом на BTC. DEX должен повторить эквивалентную позицию.

**Маппинг**:
- CEX спот → DEX спот (Uniswap, 1inch)
- CEX фьючерс/перп → DEX perp (GMX v2, dYdX v4, Synthetix)
- CEX pleco с плечом → ограниченно через GMX/dYdX (до 50x)
- CEX экзотические пары → могут отсутствовать на DEX (риск пропуска сигнала)

### 2.3 Wallet Architecture (MEDIUM RISK)

**Проблема**: MPC сложен в инфраструктуре. Полноценный MPC (TSS) требует кастомной разработки.

**Рекомендуемое решение**: **ERC-4337 Smart Account + Session Keys**
- Пользователь создаёт Smart Wallet (Account Abstraction) один раз
- Подписывает Session Key с ограниченными правами (только торговля, лимит суммы, срок действия)
- Бэкенд использует Session Key для исполнения сделок без участия пользователя
- Библиотеки: ZeroDev, Biconomy, Alchemy Account Kit

**Альтернатива для Solana**: Delegated Authority через Program Derived Addresses

### 2.4 Регуляторные риски (MEDIUM RISK)

- Copy-trading может квалифицироваться как инвестиционный совет в ряде юрисдикций
- Telegram-кошелёк с торговлей — потенциальный KYC/AML вопрос
- Решение: Юридическая консультация до запуска, геоблокировка запрещённых юрисдикций

---

## 3. Архитектура системы

```
┌─────────────────────────────────────────────────────────────┐
│                    TELEGRAM MINI APP                         │
│  React SPA + Telegram WebApp SDK + Wagmi/viem               │
│  - Trader analytics dashboard                                │
│  - Portfolio & positions                                     │
│  - Subscription management                                   │
│  - Embedded wallet UI (ERC-4337 / Session Key)              │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS/WebSocket
┌──────────────────────▼──────────────────────────────────────┐
│                    API GATEWAY                               │
│  FastAPI + WebSocket + JWT Auth (Telegram initData)         │
└──────┬────────────────┬────────────────┬────────────────────┘
       │                │                │
┌──────▼──────┐  ┌──────▼──────┐  ┌─────▼───────┐
│  Analytics  │  │   Wallet    │  │  CopyTrade  │
│   Service   │  │   Service   │  │   Engine    │
│  (FastAPI)  │  │  (FastAPI)  │  │  (FastAPI)  │
└──────┬──────┘  └──────┬──────┘  └─────┬───────┘
       │                │                │
┌──────▼──────┐  ┌──────▼──────┐  ┌─────▼───────┐
│  ClickHouse │  │  ERC-4337   │  │  Execution  │
│  (metrics)  │  │  Bundler    │  │   Engine    │
└─────────────┘  └──────┬──────┘  └─────┬───────┘
                        │                │
              ┌─────────▼────┐   ┌───────▼───────┐
              │  Blockchain  │   │  1inch / GMX  │
              │  (Arbitrum,  │   │  Jupiter etc. │
              │  Base, Sol)  │   └───────────────┘
              └──────────────┘

┌────────────────────────────────────────────────────────────┐
│                  CEX TRACKER (Celery Workers)               │
│  Binance / Bybit / Bitget / Huobi leaderboard polling      │
│  Position change detection → Signal queue (Redis)          │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│                  SHARED INFRASTRUCTURE                      │
│  PostgreSQL (users, subscriptions, trades)                 │
│  Redis (cache, pub/sub, task queue)                        │
│  ClickHouse (time-series: positions, P&L, ROI)             │
└────────────────────────────────────────────────────────────┘
```

---

## 4. Технологический стек

### Backend
| Компонент | Технология |
|-----------|-----------|
| API Framework | FastAPI (async) |
| Task Queue | Celery + Redis |
| Database | PostgreSQL 16 |
| Time-series | ClickHouse |
| Cache / Pub-Sub | Redis 7 |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Config | Pydantic Settings |
| Logging | structlog |
| Monitoring | Prometheus + Grafana |

### Frontend (Mini App)
| Компонент | Технология |
|-----------|-----------|
| Framework | React 18 + TypeScript |
| Build | Vite |
| Styling | TailwindCSS |
| Charts | Recharts / Lightweight Charts |
| Web3 | wagmi v2 + viem |
| Telegram SDK | @twa-dev/sdk |
| State | Zustand |

### Blockchain / Smart Contracts
| Компонент | Технология |
|-----------|-----------|
| Smart Contracts | Solidity 0.8.x (Foundry) |
| Smart Accounts | ZeroDev (ERC-4337) |
| EVM DEX | 1inch Fusion+, Uniswap v3, GMX v2 |
| Solana DEX | Jupiter Aggregator |
| Chains Phase 1 | Arbitrum One, Base |
| Chains Phase 2 | Solana |
| Bundler | ZeroDev Bundler / Stackup |
| Paymaster | ZeroDev Paymaster (gas sponsorship) |

### Infrastructure
| Компонент | Технология |
|-----------|-----------|
| Containers | Docker + Docker Compose |
| Orchestration | Kubernetes (prod) |
| CI/CD | GitHub Actions |
| Secrets | HashiCorp Vault / AWS Secrets Manager |
| Cloud | AWS (ECS / EKS) |

---

## 5. Модель данных (основные сущности)

```
Users
├── id (bigint PK)
├── telegram_id (bigint UNIQUE)
├── wallet_address (text) — Smart Account address
├── session_key (text encrypted) — delegated session key
├── created_at (timestamptz)

Traders (CEX tracked traders)
├── id (bigint PK)
├── exchange (enum: binance|bybit|bitget|huobi)
├── trader_id (text) — exchange-specific ID
├── display_name (text)
├── is_public (bool)

TraderStats (ClickHouse — time series)
├── trader_id
├── timestamp
├── pnl_usd
├── roi_pct
├── max_drawdown_pct
├── win_rate
├── trade_count

TraderPositions (ClickHouse — snapshots)
├── trader_id
├── symbol
├── side (LONG/SHORT)
├── size
├── entry_price
├── leverage
├── snapshot_at

Signals (PostgreSQL)
├── id
├── trader_id
├── type (OPEN|CLOSE|UPDATE)
├── symbol
├── side
├── detected_at
├── executed_count

Subscriptions
├── id
├── user_id
├── trader_id
├── max_allocation_usd
├── max_leverage
├── stop_loss_pct
├── is_active
├── created_at

UserTrades
├── id
├── subscription_id
├── signal_id
├── chain
├── tx_hash
├── token_in, token_out
├── amount_in, amount_out
├── status (pending|confirmed|failed)
├── executed_at
```

---

## 6. Этапы разработки

### MVP (12 недель) vs Full Product (26 недель)

**MVP**: Один CEX (Binance), одна сеть (Arbitrum), только спот-трейдинг, базовая аналитика, ERC-4337 кошелёк.  
**Full**: Все 4 CEX, мульти-чейн, перпетуальные позиции, расширенная аналитика, Solana.

---

### Phase 1: Foundation & Infrastructure (2 недели)

**Цель**: Настроить монорепо, инфраструктуру, CI/CD, базовые сервисы.

**Задачи**:
- [ ] Монорепо структура: `backend/`, `frontend/`, `contracts/`, `infra/`
- [ ] `pyproject.toml` с зависимостями (FastAPI, SQLAlchemy, Celery, etc.)
- [ ] Docker Compose: PostgreSQL, Redis, ClickHouse, App
- [ ] Alembic: начальная миграция базовой схемы
- [ ] FastAPI skeleton: health check, CORS, auth middleware
- [ ] Telegram initData валидация (JWT replacement)
- [ ] GitHub Actions CI: ruff, mypy, pytest
- [ ] Prometheus + Grafana setup
- [ ] Базовая структура Celery: воркеры, beat scheduler
- [ ] ClickHouse: схемы таблиц для time-series

**Deliverable**: Запущенный docker-compose, прошедший CI pipeline.

---

### Phase 2: CEX Data Aggregation (3 недели)

**Цель**: Собирать данные о трейдерах с 4 CEX, хранить позиции и метрики.

**Задачи**:

**Week 1 — Binance integration**:
- [ ] Binance Futures Leaderboard API client
  - `GET /futures/leaderboard/getLeaderboard` — список топ-трейдеров
  - `GET /futures/leaderboard/getUserInformation` — детали трейдера
  - `GET /futures/leaderboard/getOtherPosition` — позиции (если публичные)
- [ ] Celery task: polling каждые 10 сек для топ-100 трейдеров
- [ ] Position snapshot storage в ClickHouse
- [ ] Change detection: diff между снэпшотами → сигнал

**Week 2 — Bybit + Bitget**:
- [ ] Bybit Copy Trading API client
  - Master trader list, positions, PnL history
- [ ] Bitget Elite Traders API client
  - Public profile positions endpoint
- [ ] Унифицированный TraderPosition model (Pydantic)
- [ ] Adapter pattern: каждый CEX → унифицированная модель

**Week 3 — Huobi + Signal Detection**:
- [ ] HTX (Huobi) API клиент
- [ ] Signal detection алгоритм:
  - Новая позиция открыта (появилась в снэпшоте)
  - Позиция увеличена (size вырос > 5%)
  - Позиция закрыта (исчезла из снэпшота)
- [ ] Signal publishing в Redis pub/sub
- [ ] Rate limiting и backoff для CEX API

**Deliverable**: Система собирает позиции со всех 4 CEX и публикует сигналы в Redis.

---

### Phase 3: Analytics Engine (2 недели)

**Цель**: Вычислять и хранить метрики трейдеров для отображения в Mini App.

**Задачи**:

**Week 1 — Core Metrics**:
- [ ] ROI calculator: `(current_value - initial_value) / initial_value * 100`
- [ ] Max Drawdown: rolling maximum drawdown из equity curve
- [ ] Win Rate: закрытые прибыльные позиции / всего закрытых
- [ ] Sharpe Ratio: (ROI - risk_free_rate) / std_deviation
- [ ] P&L timeline: агрегация по часам/дням/неделям

**Week 2 — Equity Curve & API**:
- [ ] Equity curve построение из ClickHouse данных
- [ ] REST API endpoints:
  - `GET /traders` — список с фильтрацией/сортировкой
  - `GET /traders/{id}/stats` — текущие метрики
  - `GET /traders/{id}/positions` — открытые позиции
  - `GET /traders/{id}/equity-curve` — история P&L
- [ ] Celery periodic task: пересчёт метрик каждые 15 мин
- [ ] Redis кэширование метрик (TTL 60 сек)

**Deliverable**: API возвращает полную аналитику по трейдерам.

---

### Phase 4: Wallet Infrastructure (3 недели)

**Цель**: Встроить некастодиальный ERC-4337 Smart Wallet с Session Keys.

**Задачи**:

**Week 1 — Smart Account Setup**:
- [ ] ZeroDev SDK интеграция (backend + frontend)
- [ ] Smart Account creation flow:
  - Пользователь входит через Telegram
  - Генерируется deterministic wallet address (из Telegram ID + salt)
  - Smart Account деплоится при первой транзакции (counterfactual)
- [ ] API endpoint: `POST /wallet/create` → возвращает Smart Account address
- [ ] API endpoint: `GET /wallet/balance` — балансы токенов

**Week 2 — Session Key Delegation**:
- [ ] Session Key generation: frontend генерирует ephemeral keypair
- [ ] User signs session key permission:
  - Allowed contracts: только наши роутеры + DEX
  - Max amount per tx: настраивается пользователем
  - Expiry: 30 дней, обновляемый
- [ ] Backend хранит session key (зашифрованный) + permissions
- [ ] Session Key validation middleware

**Week 3 — Gas Abstraction**:
- [ ] Paymaster integration (спонсируем газ или USDC-оплата)
- [ ] UserOperation construction: backend строит UserOp, подписывает Session Key
- [ ] Bundler submission: отправка через ZeroDev Bundler
- [ ] Transaction status polling: `GET /wallet/tx/{hash}`
- [ ] Frontend wallet UI: баланс, история транзакций, управление Session Key

**Deliverable**: Пользователь создаёт кошелёк в Mini App, делегирует права — бэкенд может исполнять транзакции без подтверждения.

---

### Phase 5: DEX Execution Engine (3 недели)

**Цель**: Исполнять своп-ордера и перпетуальные позиции на DEX.

**Задачи**:

**Week 1 — 1inch Integration (Spot)**:
- [ ] 1inch Fusion+ API клиент:
  - `GET /swap/v6.0/{chainId}/quote` — получить котировку
  - `POST /swap/v6.0/{chainId}/swap` — построить calldata
- [ ] Order routing: выбор лучшего маршрута (1inch vs Uniswap v3)
- [ ] Slippage protection: dynamic slippage based on liquidity
- [ ] Price impact check: отклонение > 2% → отмена ордера

**Week 2 — GMX v2 Integration (Perpetuals)**:
- [ ] GMX v2 Router контракт интеграция на Arbitrum
  - `createIncreasePosition` — открыть лонг/шорт
  - `createDecreasePosition` — закрыть/уменьшить
- [ ] Position sizing: масштабирование к пользовательскому портфелю
- [ ] Leverage calculation: не превышать пользовательский лимит
- [ ] Funding rate check: предупреждение при высоком funding rate

**Week 3 — Execution Engine Core**:
- [ ] Trade type detector: спот vs перп на основе символа и типа CEX сделки
- [ ] Execution pipeline:
  ```
  Signal → Validate → Size → Quote → Build UserOp → Submit → Monitor
  ```
- [ ] Retry logic: exponential backoff при network ошибках
- [ ] Transaction monitoring: polling до подтверждения (max 3 min)
- [ ] Failed trade handling: уведомление + логирование
- [ ] API: `GET /trades/history` — история исполненных сделок пользователя

**Deliverable**: Система может исполнять реальные сделки на Arbitrum через GMX + 1inch.

---

### Phase 6: Copy-Trade Engine (3 недели)

**Цель**: Связать сигналы от CEX с исполнением на DEX для каждого подписчика.

**Задачи**:

**Week 1 — Subscription Management**:
- [ ] CRUD для подписок:
  - `POST /subscriptions` — подписаться на трейдера
  - `DELETE /subscriptions/{id}` — отписаться
  - `PATCH /subscriptions/{id}` — изменить параметры
- [ ] Параметры подписки:
  - `max_allocation_usd` — макс. объём в USD
  - `max_leverage` — макс. плечо (1-50x)
  - `stop_loss_pct` — стоп-лосс в % от суммы подписки
  - `copy_ratio` — коэффициент от позиции трейдера (1-100%)
  - `allowed_symbols` — белый список токенов

**Week 2 — Signal Processing Pipeline**:
- [ ] Redis consumer: подписка на сигналы от CEX Tracker
- [ ] Signal fan-out: один сигнал → N пользователей-подписчиков
- [ ] Pre-execution checks:
  - [ ] Пользователь активен и has session key
  - [ ] Баланс достаточен (мин. 10 USDC)
  - [ ] Символ доступен на DEX
  - [ ] Position size в пределах лимита пользователя
  - [ ] Подписка не превысила stop-loss
- [ ] Celery task: `execute_copy_trade(signal_id, user_id)`

**Week 3 — Risk Management**:
- [ ] Portfolio-level stop-loss: если пользователь потерял X% от общего, пауза
- [ ] Duplicate signal filter: дедупликация за 30 сек окно
- [ ] Symbol mapping: BTC/USDT на CEX → WBTC/USDC на DEX (маппинг таблица)
- [ ] Unavailable symbol handling: логировать и уведомить, не падать
- [ ] Celery Beat: мониторинг открытых позиций каждые 5 мин
- [ ] Auto-close: если CEX трейдер закрыл позицию → закрыть на DEX

**Deliverable**: Полный copy-trade цикл: CEX сигнал → проверки → DEX исполнение → запись.

---

### Phase 7: Telegram Mini App (3 недели)

**Цель**: Построить UI внутри Telegram с аналитикой, подписками и кошельком.

**Задачи**:

**Week 1 — Project Setup + Auth**:
- [ ] Vite + React + TypeScript + TailwindCSS setup
- [ ] Telegram WebApp SDK интеграция:
  - `WebApp.initData` → авторизация на бэкенде
  - `WebApp.themeParams` → адаптация к теме Telegram
  - `WebApp.BackButton`, `MainButton` для навигации
- [ ] Роутинг: React Router v6
- [ ] API клиент: `axios` + intercept для auth токена
- [ ] Zustand stores: user, wallet, traders, subscriptions

**Week 2 — Trader Analytics UI**:
- [ ] Главный экран: список топ-трейдеров
  - Карточка трейдера: avatar, exchange badge, ROI%, Drawdown%, Win Rate
  - Сортировка: ROI, Drawdown, Win Rate, Subscribers
  - Фильтры: биржа, срок (7d/30d/90d)
- [ ] Страница трейдера:
  - Equity curve (Lightweight Charts)
  - Открытые позиции (таблица)
  - Статистика: Sharpe, Sortino, P&L разбивка
  - Кнопка "Subscribe"
- [ ] WebSocket: real-time обновление позиций и P&L

**Week 3 — Wallet + Subscriptions UI**:
- [ ] Wallet screen:
  - Текущий баланс (USDC, ETH, WBTC...)
  - История транзакций
  - Кнопки: пополнить, вывести
  - Session Key статус и управление
- [ ] My Subscriptions screen:
  - Список активных подписок с P&L
  - Настройки каждой подписки (лимиты, stop-loss)
  - История скопированных сделок
- [ ] Notifications: Telegram Bot push при исполнении сделки
- [ ] Onboarding: шаги для новых пользователей (создание кошелька → пополнение → подписка)

**Deliverable**: Полнофункциональный Mini App, работающий в Telegram.

---

### Phase 8: Smart Contracts (2 недели)

**Цель**: Написать и протестировать смарт-контракты для безопасного copy-trade роутинга.

**Задачи**:

**Week 1 — Core Contracts (Foundry)**:
- [ ] `CopyTradeRouter.sol`:
  - Принимает UserOp от Session Key
  - Валидирует что получатель — whitelist DEX
  - Форвардит свопы в 1inch / GMX Router
  - Проверяет slippage параметры
- [ ] `SessionKeyValidator.sol`:
  - ERC-4337 compatible validator plugin
  - Хранит permissions: allowed targets, max amount, expiry
  - Валидирует подпись Session Key
- [ ] Unit тесты (Foundry): 100% coverage критических путей

**Week 2 — Security + Audit Prep**:
- [ ] `EmergencyStop.sol`: admin может приостановить все операции
- [ ] Slippage guard: max 3% by default, configurable
- [ ] Reentrancy guards (OpenZeppelin ReentrancyGuard)
- [ ] Formal verification (через Halmos или Certora basic)
- [ ] Internal audit checklist (OWASP Smart Contract Top 10)
- [ ] Deploy scripts: Arbitrum testnet (Goerli → Sepolia)
- [ ] Верификация на Arbiscan

**Deliverable**: Задеплоенные и верифицированные контракты на Arbitrum testnet.

---

### Phase 9: Testing & Security (3 недели)

**Цель**: Комплексное тестирование, нагрузочные тесты, аудит безопасности.

**Задачи**:

**Week 1 — Integration & E2E Tests**:
- [ ] pytest интеграционные тесты для всех API эндпойнтов
- [ ] CEX mock сервер: имитация Binance/Bybit API для тестов
- [ ] Copy-trade E2E: от фейкового сигнала до транзакции на testnet
- [ ] Wallet tests: создание, Session Key, UserOp submission
- [ ] Coverage: > 80% для критического бэкенда

**Week 2 — Load Testing + Security**:
- [ ] Locust нагрузочные тесты:
  - 1000 одновременных пользователей в Mini App
  - 100 сигналов/сек fan-out (10,000 подписчиков)
- [ ] `bandit -r backend/` — статический анализ
- [ ] OWASP ZAP scan на API
- [ ] Telegram initData tampering tests
- [ ] Session Key privilege escalation tests
- [ ] SQL injection тесты на все параметры

**Week 3 — Bug Fix + Audit Prep**:
- [ ] Исправление найденных уязвимостей
- [ ] Smart contract: подготовка к внешнему аудиту
- [ ] Penetration testing (если бюджет) или Bug Bounty программа
- [ ] Disaster recovery: процедуры при компрометации

**Deliverable**: Все критические баги закрыты, система готова к продакшну.

---

### Phase 10: Beta Launch & Production (2 недели)

**Цель**: Запуск в продакшн, мониторинг, итеративные улучшения.

**Задачи**:

**Week 1 — Prod Infrastructure**:
- [ ] Kubernetes кластер (EKS или GKE)
- [ ] Prod DB: PostgreSQL RDS с replicas
- [ ] ClickHouse Cloud или self-hosted cluster
- [ ] Redis Cluster (ElastiCache)
- [ ] CDN для Mini App статики (CloudFront)
- [ ] SSL, domain для Mini App URL
- [ ] Secrets в AWS Secrets Manager
- [ ] Alerts: PagerDuty при даунтайме

**Week 2 — Launch**:
- [ ] Mainnet деплой контрактов (Arbitrum One)
- [ ] Closed beta: 100 пользователей
- [ ] Мониторинг: Prometheus + Grafana дашборды
- [ ] Error tracking: Sentry
- [ ] Логи: ELK Stack / Loki
- [ ] Runbook: процедуры on-call
- [ ] Public launch announcement

**Deliverable**: Работающий продукт на mainnet с первыми реальными пользователями.

---

## 7. MVP vs Full Product

### MVP (12 недель, ~$50-80k dev budget)

Включает:
- Фазы 1, 2 (только Binance), 3, 4, 5 (только 1inch spot), 6 (базовый), 7 (базовый UI), 10 (упрощённый)
- Только Arbitrum
- Только спот-трейдинг (без перпетуальных позиций)
- ERC-4337 кошелёк (без Solana)
- Базовая аналитика (ROI, P&L)

Исключает из MVP:
- Перпетуальные позиции (GMX, dYdX)
- Bybit/Bitget/Huobi (только Binance)
- Solana / Jupiter
- Смарт-контракты (используем прямые DEX роутеры)
- Внешний аудит

### Full Product (26 недель)

Все 10 фаз, включая:
- Все 4 CEX
- Перпетуальные позиции через GMX v2
- Solana / Jupiter
- ERC-4337 + Session Keys
- Кастомные смарт-контракты с аудитом
- Расширенная аналитика (Sharpe, Sortino, equity curve)

---

## 8. Команда и роли

| Роль | Что делает | Фазы |
|------|-----------|------|
| Backend Lead (Python) | FastAPI, Celery, DB, CEX integrations | 1-3, 6, 9 |
| Blockchain Dev | Smart contracts (Solidity), ERC-4337, DEX integrations | 4, 5, 8 |
| Frontend Dev | React Mini App, Telegram SDK | 7 |
| DevOps | Docker, K8s, CI/CD, monitoring | 1, 10 |
| (optional) Security Auditor | Smart contract audit | 8-9 |

Минимальная команда для MVP: 2 человека (Backend + Blockchain/Frontend fullstack).

---

## 9. Зависимости и интеграции

| Сервис | Цель | Критичность |
|--------|------|-------------|
| Binance Public API | Trader data | CRITICAL |
| Bybit API | Trader data | HIGH |
| Bitget API | Trader data | HIGH |
| 1inch Fusion+ API | DEX spot routing | CRITICAL |
| GMX v2 contracts | DEX perpetuals | HIGH |
| ZeroDev SDK | ERC-4337 wallets | CRITICAL |
| Alchemy / Infura | RPC provider | CRITICAL |
| Telegram Bot API | Notifications | HIGH |
| Telegram Mini App | Frontend host | CRITICAL |
| CoinGecko API | Token prices (fallback) | MEDIUM |

---

## 10. Метрики успеха

| Метрика | MVP target | 6-month target |
|---------|-----------|----------------|
| Активных пользователей | 100 | 5,000 |
| Скопированных сделок/день | 50 | 5,000 |
| Среднее время исполнения сигнала | < 30 сек | < 10 сек |
| Uptime | 99% | 99.9% |
| Smart contract TVL | $10k | $500k |

---

## 11. Open Questions (нужны решения до старта)

1. **CEX Data Granularity**: Нужно проверить реальные API Binance/Bybit — насколько детальны публичные позиции топ-трейдеров? Провести spike за 2 дня.

2. **Chain Priority**: Arbitrum для EVM — ок, но какой приоритет у Solana? Это влияет на сложность wallet-уровня.

3. **Perps or Spot First**: Перпетуальные контракты (GMX) значительно усложняют Phase 5. Для MVP рекомендую начать только со спотом.

4. **Subscription Pricing Model**: Бесплатно / % от прибыли / фиксированная плата — это влияет на смарт-контракты в Phase 8.

5. **Legal Entity**: Нужна юридическая консультация до публичного запуска — особенно по юрисдикциям США/ЕС.

---

*Ожидает подтверждения перед началом реализации.*
