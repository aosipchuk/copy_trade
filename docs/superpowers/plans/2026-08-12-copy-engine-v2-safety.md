# Copy Engine v2 Safety Implementation Plan

> **Статус:** подготовлен по утверждённой спецификации. Реализация начинается
> только после проверки этого плана. Все тесты, lint, mypy, frontend build и
> database verification выполняются на сервере, не локально.

**Цель:** заменить небезопасное ордерное копирование на идемпотентный
target-state движок, нормализовать ROI, применить лимиты к конечной экспозиции,
поддержать default DEX и HIP-3 и при миграции остановить все существующие
live-подписки до ручной сверки позиций.

**Архитектура:** `Signal Detector v2` сохраняет signed target лидера. `Target
Calculator` переводит его в target подписки и применяет per-coin/aggregate caps.
`Account Reconciler` под PostgreSQL advisory lock суммирует совместимые targets,
сверяет их с фактической позицией и создаёт durable intent до вызова exchange.
`MarketRegistry` динамически разрешает рынки по метаданным Hyperliquid. Любая
необъяснённая активность на выделенном аккаунте блокирует всё live-исполнение
пользователя.

**Стек:** Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL, Alembic, Redis,
APScheduler, Pydantic v2, httpx, React, TypeScript, Vite, Hyperliquid Info и
Exchange API.

**Утверждённый дизайн:**
[2026-08-12-copy-engine-v2-safety-design.md](../specs/2026-08-12-copy-engine-v2-safety-design.md)

---

## Неподлежащие изменению правила реализации

- `COPY_ENGINE_V2_LIVE_ENABLED` по умолчанию равен `false` во всех шаблонах.
- Ни один v1 signal или `UserTrade` не может инициировать v2 order.
- Live работает только для `engine_version = 2`, активных subscription/account
  execution states и после server-side preflight.
- Старые live-подписки миграция делает inactive/paused. Автоматического resume
  нет.
- Resume требует flat copy account без open orders и pending/unknown intents.
- Текущие позиции лидера при create/resume/new DEX становятся baseline-only.
- Target/intent записывается до exchange call; подтверждение позиции приходит
  только из Hyperliquid state/fills.
- Уменьшение всегда `reduceOnly`; flip проходит через подтверждённый ноль.
- Один active intent допускается на `(user_id, dex, coin)`.
- Противоположные targets разных подписок не netting-уются и блокируют account.
- `max_per_coin_usd` и `max_allocation_usd` применяются к конечным targets.
- Static `COIN_WHITELIST` не участвует в live или demo execution.
- Standard и Unified поддерживаются; legacy DEX abstraction и Portfolio Margin
  pre-alpha блокируются.
- Временная ошибка API останавливает исполнение, но не объявляется drift без
  фактов. Неизвестный order/fill/position delta блокирует account.
- Production меняется только через local commit, push и server pull/deploy.

## Зависимости и границы коммитов

```text
ROI shadow column
        │
        ├──> V2 schema + migration pause
        │          │
        │          ├──> HL typed clients ──> MarketRegistry
        │          │                           │
        │          ├──> leader state/signals ─┤
        │          │                           ├──> targets ──> reconciler
        │          └──> account/preflight ────┘                    │
        │                                                          ├──> drift
        │                                                          ├──> demo
        │                                                          └──> lifecycle/API/UI
        └──────────────────────────────────────────────────────────────> release
```

Каждый task ниже заканчивается отдельным focused commit. Коммиты можно объединять
только если следующая задача исправляет невозможность сборки предыдущей; live
feature flag при этом всё равно остаётся выключенным.

## Зафиксированные архитектурные решения

| Решение | Плюсы | Цена/альтернатива |
|---|---|---|
| PostgreSQL — источник истины для targets/intents, Redis — только cache/metrics | Crash-safe idempotency и аудит | Больше записей, чем у Redis-only варианта; это приемлемо для финансового исполнения |
| Короткая DB-транзакция создаёт intent, exchange call идёт после commit | Intent переживает crash и network timeout | Lock отпускается до HTTP; duplicate предотвращает partial unique active-intent index |
| REST polling durable known DEX + fills discovery | Не упирается в лимит user-specific WebSocket subscriptions | Новый DEX обнаруживается с задержкой и поэтому первая позиция baseline-only |
| ROI shadow column и dual-write | Старую версию можно безопасно вернуть до cleanup | Временная двойная схема; in-place `*100` отвергнуто как rollback-опасное |
| Один dedicated account на пользователя | Внешний drift однозначно обнаруживается | Ручная торговля и несколько параллельных copy accounts не поддерживаются в v2 |
| Противоположные targets блокируются | Нет скрытого переноса риска/PnL между стратегиями | Hedge/netting режим отложен до отдельного дизайна |

---

### Task 1: Добавить rollback-safe канонический ROI

**Files:**

- Create: `backend/alembic/versions/t6u7v8w9x0y1_add_roi_percent.py`
- Modify: `backend/app/models/trader.py`
- Modify: `backend/app/tasks/hl_tracker.py`
- Modify: `backend/app/tasks/analytics_tasks.py`
- Modify: `backend/app/services/analytics/metrics.py`
- Modify: `backend/app/services/analytics/export_data.py`
- Modify: `backend/app/services/analytics/export_workbook.py`
- Modify: `backend/app/services/admin_trader_import.py`
- Modify: `backend/app/services/portfolio/advanced.py`
- Modify: `backend/app/services/portfolio/backtest.py`
- Modify: `backend/app/services/portfolio/candidates.py`
- Modify: `backend/app/services/portfolio/explanations.py`
- Modify: `backend/app/services/portfolio/scoring.py`
- Modify: `backend/app/services/portfolio/types.py`
- Modify: `backend/app/api/traders.py`
- Modify: `backend/app/schemas/trader.py`
- Modify: `backend/tests/api/test_traders.py`
- Modify: `backend/tests/unit/test_hl_info_client.py`
- Modify: `backend/tests/unit/test_portfolio_backtest.py`
- Modify: `backend/tests/unit/test_portfolio_builder.py`
- Modify: `backend/tests/unit/test_trader_export_workbook.py`
- Create: `backend/tests/unit/test_roi_units.py`

- [ ] Сначала добавить тесты: raw `0.05` преобразуется в canonical `5.0`, API
  продолжает отдавать поле `roi_pct=5.0`, scoring использует диапазоны процентов,
  export не умножает повторно.
- [ ] Добавить nullable `TraderStat.roi_percent NUMERIC(20, 8)` и Alembic
  backfill `roi_percent = roi_pct * 100` только для строк, где canonical значение
  отсутствует.
- [ ] Сделать upgrade идемпотентным на уровне данных и добавить проверки
  `NULL`/finite/range; downgrade удаляет только shadow column и никогда не меняет
  legacy `roi_pct`.
- [ ] В leaderboard ingest dual-write: `roi_pct=raw ratio`,
  `roi_percent=raw ratio * 100` через `Decimal`, без промежуточного float до
  записи.
- [ ] Перевести все filter/order/scoring/backtest/candidate/export query на
  `roi_percent`. Legacy `roi_pct` разрешён только в dual-write и миграции.
- [ ] Сохранить внешнее имя Pydantic/TypeScript поля `roi_pct`; mapper явно берёт
  `roi_percent`.
- [ ] Оставить `_MIN_30D_ROI = 0.03` только на raw leaderboard boundary и
  переименовать в `_MIN_30D_ROI_RATIO`, чтобы единицы были видны из кода.
- [ ] Добавить repository-wide test/assertion, не позволяющий новому business
  code читать `TraderStat.roi_pct`.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_roi_units.py tests/unit/test_hl_info_client.py \
  tests/unit/test_portfolio_backtest.py tests/unit/test_portfolio_builder.py \
  tests/unit/test_trader_export_workbook.py tests/api/test_traders.py -v
```

**Commit:** `fix: normalize leaderboard ROI as percent`

---

### Task 2: Добавить v2 execution schema и миграционную паузу

**Files:**

- Create: `backend/alembic/versions/u7v8w9x0y1z2_add_copy_engine_v2.py`
- Create: `backend/app/models/copy_execution.py`
- Modify: `backend/app/models/subscription.py`
- Modify: `backend/app/models/signal.py`
- Modify: `backend/app/models/trade.py`
- Modify: `backend/app/models/portfolio.py`
- Modify: `backend/app/models/new_wallet.py`
- Modify: `backend/app/models/user.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/tests/unit/test_copy_engine_models.py`
- Create: `backend/tests/unit/test_copy_engine_migration_contract.py`

- [ ] Сначала описать ORM/migration contract tests: constraints, unique indexes,
  enum-like checks, nullable/FK semantics и pause data statements.
- [ ] Добавить в `subscriptions`: `engine_version`, `execution_status`,
  `pause_reason`, `execution_status_details`, `resumed_at`, `blocked_at`.
- [ ] Добавить те же execution-control поля в
  `user_portfolio_subscriptions` и `user_new_wallet_subscriptions`, не используя
  payment/lifecycle `status` как источник истины исполнения.
- [ ] Разрешить `paused` для `user_new_wallet_items`; portfolio item уже
  поддерживает этот статус.
- [ ] Расширить `signals`: `dex`, signed `previous_size`, `target_size`,
  `delta_size`, `engine_version`, `dedupe_key`, `dispatch_status`, snapshot
  version. Добавить unique index на `dedupe_key`.
- [ ] Создать `trader_market_scopes` и `trader_position_states` для durable DEX
  discovery, observed/accepted signed state и монотонной версии лидера.
- [ ] Создать `copy_account_execution_states` с master/account/vault address,
  dedicated-account acknowledgement, mode, status/reason/details, fill cursor,
  last preflight/reconcile и optimistic version.
- [ ] Создать `copy_position_targets` с unique
  `(subscription_id, dex, coin)`, raw/capped signed targets, baseline state,
  source signal и target version.
- [ ] Создать `copy_account_positions` с unique `(user_id, dex, coin)`, aggregate
  target, confirmed actual, explained pending delta, hash/version и reconcile
  status.
- [ ] Создать `copy_execution_orders` с `kind=exchange|internal_reallocation|emergency`,
  128-bit hex `cloid`, exchange oid,
  before/target/delta, reduce-only, target version, placement/fill status и
  idempotency key. Добавить unique `cloid`, unique idempotency key и partial
  unique index, разрешающий один active exchange/emergency
  `pending|submitted|partial|unknown` intent на рынок пользователя.
- [ ] Создать `copy_execution_allocations` для requested/filled allocation по
  subscription target. Каждая allocation имеет родительский intent; когда
  aggregate exchange position не меняется, используется сразу terminal local
  `internal_reallocation` intent без вызова Hyperliquid.
- [ ] Сделать `UserTrade.signal_id` nullable для emergency/internal projections;
  добавить `dex`, nullable links на v2 order/allocation и не использовать
  `UserTrade` как position source of truth.
- [ ] Расширить допустимые trade/intent статусы значением `unknown`; legacy
  timeout больше не маскируется как окончательный `failed`.
- [ ] Data migration: все active live manual/model-portfolio/new-wallet children
  сделать `is_active=false`, `engine_version=1`, execution `paused`, reason
  `engine_v2_reconciliation_required`.
- [ ] У связанных live parent execution state установить `paused` с той же
  причиной; active items перевести в `paused`, чтобы фоновые задачи не могли
  снова активировать children.
- [ ] Existing demo history не удалять. Legacy active demos завершить как
  `engine_v2_demo_restart_required`; новые demos будут создаваться только на v2.
- [ ] Все v1 signals пометить `skipped_legacy`; pending legacy `UserTrade`
  сохранить для мониторинга и preflight.
- [ ] Для пользователей старого live создать paused account execution state с
  `account_address=users.hl_address`, но без dedicated acknowledgement.
- [ ] Downgrade не реактивирует ни одну подписку. Operational rollback не
  выполняет Alembic downgrade и сохраняет additive v2 data.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_copy_engine_models.py \
  tests/unit/test_copy_engine_migration_contract.py -v
# Только в заранее проверенной disposable server test DB:
uv run alembic upgrade head
uv run alembic current
```

Дополнительно на disposable DB выполнить upgrade дважды через `stamp`/restore
fixture и SQL assertions по каждому source type; production DB для эксперимента
не использовать.

**Commit:** `feat: add copy engine v2 execution schema`

---

### Task 3: Расширить typed Hyperliquid clients

**Files:**

- Modify: `backend/app/services/hyperliquid/models.py`
- Modify: `backend/app/services/hyperliquid/info_client.py`
- Modify: `backend/app/services/hyperliquid/exchange_client.py`
- Modify: `backend/tests/unit/test_hl_info_client.py`
- Modify: `backend/tests/unit/test_hl_signing.py`
- Create: `backend/tests/unit/test_hl_exchange_orders.py`

- [ ] Сначала добавить response fixtures/tests для default DEX, HIP-3 meta,
  DEX-specific positions/mids/open orders, abstraction state, fills и status по
  `cloid`.
- [ ] Добавить typed модели `PerpDex`, `PerpMeta`, asset contexts, open order,
  order status, user abstraction и placement result. В `Fill` добавить
  `startPosition`, hash/tid и поля, необходимые для стабильного dedupe.
- [ ] Добавить методы `get_perp_dexs`, `get_all_perp_metas`, `get_meta(dex)`,
  `get_meta_and_asset_contexts(dex)`, `get_all_mids(dex)`,
  `get_clearinghouse_state(user, dex)`, `get_positions(user, dex)`,
  `get_open_orders(user, dex)` и `get_user_abstraction(user)`.
- [ ] Сохранить default `dex=""`; не смешивать Redis cache keys разных DEX.
- [ ] Разрешить `get_order_status` принимать oid или `cloid`.
- [ ] Изменить `place_order`: принимать обязательный `cloid`, optional
  `vault_address`, добавлять `c` в order wire payload и `vaultAddress` во внешний
  запрос/подпись для subaccount.
- [ ] Возвращать typed placement result (`oid`, immediate status/error), а не
  `int|None`; error response не считать доказательством отсутствия ордера при
  транспортном timeout.
- [ ] Добавить cancel/query-by-cloid recovery primitive, но не выполнять
  автоматический cancel до выяснения exchange status.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_hl_info_client.py tests/unit/test_hl_signing.py \
  tests/unit/test_hl_exchange_orders.py -v
```

**Commit:** `feat: support idempotent HIP-3 exchange requests`

---

### Task 4: Реализовать динамический MarketRegistry

**Files:**

- Create: `backend/app/services/copy_engine/market_registry.py`
- Create: `backend/app/services/copy_engine/market_identity.py`
- Modify: `backend/app/services/copy_engine/constants.py`
- Create: `backend/app/tasks/market_registry.py`
- Modify: `backend/app/core/scheduler.py`
- Create: `backend/tests/unit/test_market_registry.py`
- Create: `backend/tests/unit/test_market_identity.py`

- [ ] Сначала добавить fixtures/tests для default DEX, двух HIP-3 DEX с
  одинаковым ticker, halted/delisted asset, stale registry и rounding.
- [ ] Ввести immutable `MarketId(dex, coin)` и canonical serializer: default
  хранится с `dex=""`, HIP-3 coin хранится в форме, возвращаемой API
  (`dex:SYMBOL`). Любой lookup всегда включает DEX.
- [ ] Вычислять perp asset id: default — index in default meta; HIP-3 —
  `100000 + perp_dex_index * 10000 + index_in_meta`, где index DEX берётся из
  одного атомарного registry snapshot.
- [ ] Registry snapshot содержит `szDecimals`, max leverage, collateral token,
  active/halted/delisted status, asset context и timestamps.
- [ ] Кэшировать snapshot в Redis с version/hash и коротким in-process cache;
  при cache miss/staleness делать single-flight refresh. Ошибка refresh не
  разрешает новый риск по неизвестному/stale рынку.
- [ ] Удалить `COIN_WHITELIST` из execution gates; оставить в `constants.py`
  только min-notional/slippage/backoff safety constants.
- [ ] `allowed_coins` нормализовать через `MarketId`; legacy unprefixed values
  разрешают только default DEX, чтобы `XYZ` случайно не разрешил `xyz:XYZ`.
- [ ] Добавить scheduler refresh registry, например каждые 300 секунд, с
  `max_instances=1` и метриками cache age/refresh errors.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_market_registry.py \
  tests/unit/test_market_identity.py -v
```

**Commit:** `feat: add dynamic Hyperliquid market registry`

---

### Task 5: Добавить copy-account selection, mode adapter и preflight

**Files:**

- Create: `backend/app/services/copy_engine/account_state.py`
- Create: `backend/app/services/copy_engine/preflight.py`
- Create: `backend/app/services/copy_engine/execution_state.py`
- Create: `backend/app/services/copy_engine/locking.py`
- Create: `backend/app/schemas/copy_execution.py`
- Create: `backend/app/api/copy_execution.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/core/config.py`
- Modify: `.env.example`
- Modify: `.env.prod.example`
- Modify: `backend/tests/api/test_wallet.py`
- Create: `backend/tests/api/test_copy_execution.py`
- Create: `backend/tests/unit/test_copy_account_state.py`
- Create: `backend/tests/unit/test_copy_preflight.py`

- [ ] Сначала добавить tests для master account, selected subaccount,
  Standard/Unified, unsupported legacy/Portfolio Margin и каждой причины
  preflight failure.
- [ ] Добавить `copy_engine_v2_live_enabled: bool = False`, registry/reconcile
  intervals и staleness thresholds в Settings и env templates.
- [ ] Реализовать PostgreSQL advisory xact lock с устойчивым строковым ключом,
  отдельно для user-account state и `(user,dex,coin)`.
- [ ] Добавить `PUT /copy-execution/account`: выбрать master либо один из
  реально принадлежащих master subaccounts и подтвердить dedicated-account rule.
  Не принимать произвольный address без server-side ownership check.
- [ ] Хранить `master_address=User.hl_address`, `account_address` для запросов
  Info API и `vault_address=account_address` только при subaccount execution.
- [ ] Реализовать `AccountStateReader`: Standard читает per-DEX margin;
  Unified читает spot/unified collateral и суммирует margin/positions по DEX.
  Не использовать default `marginSummary` как equity Unified account.
- [ ] Реализовать `GET/POST /copy-execution/preflight`, обходящий все DEX текущего
  registry и проверяющий agent, ownership, acknowledgement, mode, positions,
  open orders, legacy/v2 pending intents, metadata/prices и feature flag.
- [ ] Возвращать структурированный список checks и blocking positions/orders с
  `dex`, `coin`, side, size, oid/cloid; не включать private data.
- [ ] Реализовать user-level `pause_account`, `block_account`,
  `clear_block_after_flat_preflight`. Clear не активирует подписки.
- [ ] При неизвестном/неполном abstraction response считать mode unsupported и
  fail closed.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_copy_account_state.py \
  tests/unit/test_copy_preflight.py tests/api/test_copy_execution.py \
  tests/api/test_wallet.py -v
```

**Commit:** `feat: add dedicated copy account preflight`

---

### Task 6: Перевести leader tracking на durable signed targets и HIP-3 discovery

**Files:**

- Modify: `backend/app/services/signal_detector.py`
- Modify: `backend/app/services/signal_publisher.py`
- Modify: `backend/app/tasks/hl_tracker.py`
- Modify: `backend/app/tasks/signal_consumer.py`
- Create: `backend/app/services/copy_engine/leader_state.py`
- Create: `backend/app/services/copy_engine/leader_discovery.py`
- Create: `backend/tests/unit/test_leader_state.py`
- Create: `backend/tests/unit/test_leader_discovery.py`
- Modify: `backend/tests/unit/test_signal_detector.py`
- Modify: `backend/tests/unit/test_signal_detector_empty_baseline.py`

- [ ] Сначала переписать detector tests на signed previous/target/delta для
  open/increase/decrease/close/flip и invalid snapshot.
- [ ] `detect_changes` должен сравнивать observed size с последним **accepted**
  state, не с предыдущим poll. Несколько малых increase накапливаются до порога.
- [ ] Reduction, close и flip принимать без процентного порога после market
  precision; missing/error/stale response никогда не означает CLOSE.
- [ ] Транзакционно обновлять `trader_position_states` и вставлять signal через
  deterministic dedupe key. Повтор poll/retry не создаёт новый signal/version.
- [ ] Первый valid snapshot trader/DEX сохранять как baseline без signal.
- [ ] Poll default и durable known DEX каждые 5 секунд; ограничить concurrency и
  учитывать существующий HL rate limiter.
- [ ] Периодически читать leader fills для discovery новых prefixed HIP-3 DEX,
  добавлять `trader_market_scopes` и делать первый positions snapshot baseline.
- [ ] Fan-out выбирает только v2 executable subscriptions. Для baseline-only
  subscription target UPDATE игнорируется до valid zero; следующий OPEN после
  zero проходит.
- [ ] Любой v1 signal получает `skipped_legacy` и не вызывает старый executor.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_signal_detector.py \
  tests/unit/test_signal_detector_empty_baseline.py \
  tests/unit/test_leader_state.py tests/unit/test_leader_discovery.py -v
```

**Commit:** `feat: detect durable signed leader targets`

---

### Task 7: Реализовать TargetCalculator и итоговые лимиты

**Files:**

- Create: `backend/app/services/copy_engine/target_calculator.py`
- Create: `backend/app/services/copy_engine/target_service.py`
- Create: `backend/app/services/copy_engine/account_risk.py`
- Rewrite: `backend/app/services/copy_engine/order_builder.py`
- Modify: `backend/app/services/copy_engine/exceptions.py`
- Modify: `backend/app/tasks/signal_consumer.py`
- Rewrite: `backend/tests/unit/test_order_builder.py`
- Create: `backend/tests/unit/test_account_risk.py`
- Create: `backend/tests/unit/test_target_calculator.py`
- Create: `backend/tests/unit/test_target_service.py`

- [ ] Сначала добавить table-driven tests для long/short всех sizing modes,
  price changes, precision, min-notional и caps.
- [ ] `fixed_ratio = leader_signed_size * copy_ratio_pct / 100`.
- [ ] `fixed_usd`: raw notional каждого active market равен
  `max_allocation_usd` со стороной лидера до portfolio scaling; повторный UPDATE
  заменяет target и ничего не накапливает.
- [ ] `equity_pct`: raw notional равен свежему copy-account equity умноженному на
  `copy_ratio_pct / 100`; stale/unsupported equity блокирует новый target.
- [ ] Сначала применить `max_per_coin_usd`, затем пропорционально масштабировать
  сумму абсолютных notionals всех targets подписки до `max_allocation_usd`.
- [ ] Округлять size вниз по `szDecimals`; rounding никогда не превышает cap.
  Sub-minimum остаток хранить в target, не отправляя dust order.
- [ ] Для итогового account target консервативно суммировать initial-margin
  requirement каждой subscription allocation как
  `notional / min(subscription.max_leverage, market.max_leverage)` и сравнивать со
  свежим Standard/Unified collateral. Reduction разрешать даже когда новый риск
  уже нельзя увеличить.
- [ ] Атомарно version-ить все targets подписки, потому что изменение одного
  рынка или equity может пропорционально изменить остальные.
- [ ] Обновить `order_builder` до pure function `delta -> OrderParams`; он не
  рассчитывает подписочный размер и не знает static whitelist.
- [ ] На target changes ставить idempotent reconcile request по каждому
  затронутому `(user,dex,coin)`.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_target_calculator.py \
  tests/unit/test_target_service.py tests/unit/test_order_builder.py \
  tests/unit/test_account_risk.py -v
```

**Commit:** `feat: calculate capped subscription position targets`

---

### Task 8: Реализовать durable live AccountReconciler

**Files:**

- Create: `backend/app/services/copy_engine/reconciler.py`
- Create: `backend/app/services/copy_engine/intent_service.py`
- Create: `backend/app/services/copy_engine/allocation_service.py`
- Rewrite: `backend/app/services/copy_engine/executor.py`
- Rewrite: `backend/app/tasks/execution_tasks.py`
- Modify: `backend/app/core/scheduler.py`
- Create: `backend/tests/unit/test_copy_reconciler.py`
- Create: `backend/tests/unit/test_execution_intents.py`
- Create: `backend/tests/unit/test_execution_allocations.py`

- [ ] Сначала добавить state-machine tests: no-op, open, increase, reduce, close,
  two-phase flip, retry, immediate fill, partial, cancel, reject и unknown.
- [ ] Reconcile request получает fresh exchange state, затем в короткой DB
  транзакции берёт advisory lock, повторно проверяет account/subscription status,
  existing active intent и target version.
- [ ] Суммировать только same-direction targets. Opposing non-zero targets
  атомарно блокируют user account и все его live subscriptions.
- [ ] Рассчитывать `delta = aggregate_target - confirmed_actual`; не использовать
  signal size как order size.
- [ ] До exchange call создать/commit intent и deterministic 128-bit `cloid`.
  После commit другой worker видит active intent и не создаёт второй.
- [ ] Увеличение отправлять обычным IOC; уменьшение/close — только reduce-only;
  flip создаёт close intent и ждёт confirmed actual zero перед opposite open.
- [ ] После HTTP response сохранить typed placement status. При timeout оставить
  `unknown` и сначала query status по `cloid`, не отправлять новый order.
- [ ] После partial/cancel следующий intent создаётся только на confirmed
  residual. Retry того же неизвестного intent использует тот же `cloid`.
- [ ] Распределять filled delta между subscription targets детерминированно и
  пропорционально требуемому изменению; создавать `UserTrade` projections.
- [ ] Если aggregate target не изменился, но ownership targets изменились,
  записывать terminal local `internal_reallocation` intent и его allocations по
  свежему mid без exchange call, чтобы stop/delete одной подписки не присвоил
  позицию другой молча.
- [ ] Scheduler обрабатывает dirty account positions и active/unknown intents с
  bounded batch, backoff и `max_instances=1`.
- [ ] Старые `execute_copy_trade`, `_handle_close` и
  `close_positions_for_subscription` удалить/заменить fail-closed compatibility
  guards. Ни одна ветка не закрывает full user coin position по signal CLOSE.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_copy_reconciler.py \
  tests/unit/test_execution_intents.py \
  tests/unit/test_execution_allocations.py -v
```

**Commit:** `feat: reconcile copy targets idempotently`

---

### Task 9: Добавить continuous drift detection и legacy pending monitor

**Files:**

- Create: `backend/app/services/copy_engine/drift_detector.py`
- Create: `backend/app/services/copy_engine/fill_ingestion.py`
- Create: `backend/app/tasks/copy_reconcile.py`
- Modify: `backend/app/tasks/execution_tasks.py`
- Modify: `backend/app/core/scheduler.py`
- Create: `backend/tests/unit/test_drift_detector.py`
- Create: `backend/tests/unit/test_fill_ingestion.py`
- Create: `backend/tests/unit/test_legacy_pending_monitor.py`

- [ ] Сначала добавить tests для known fill, fill after crash, duplicate fill,
  unknown fill, unknown open order, unexplained position delta и network outage.
- [ ] На successful preflight зафиксировать fill cursor/high-water mark; fills до
  него не объявлять drift.
- [ ] Dedupe fills стабильным exchange identity, сопоставлять по oid известного
  intent. Intent без oid после timeout восстанавливать через `orderStatus(cloid)`.
- [ ] Каждый open order copy account должен совпасть с known v2 oid/cloid.
- [ ] Изменение confirmed actual должно объясняться последовательностью известных
  fills. Unknown fill/order/delta вызывает один idempotent user-level block.
- [ ] При block остановить новые intents для всех DEX, пометить live
  subscriptions blocked и сохранить безопасные details для UI/аудита. Не
  отправлять corrective orders.
- [ ] API/transport outage переводит reconcile в retryable stalled state, но не
  drift. Пока состояние неизвестно, новый риск запрещён.
- [ ] Legacy `UserTrade.status=pending` продолжать проверять по oid до terminal;
  timeout/unknown помечать `unknown`, не `failed`. Preflight блокируется, пока
  ambiguity не разрешена вручную/данными exchange.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_drift_detector.py \
  tests/unit/test_fill_ingestion.py \
  tests/unit/test_legacy_pending_monitor.py -v
```

**Commit:** `feat: block copy accounts on external drift`

---

### Task 10: Перевести demo на тот же target-state core

**Files:**

- Rewrite: `backend/app/services/copy_engine/demo_executor.py`
- Rewrite: `backend/app/tasks/demo_reconcile.py`
- Modify: `backend/app/services/demo_service.py`
- Modify: `backend/app/services/subscription_service.py`
- Modify: `backend/tests/unit/test_demo_executor.py`
- Modify: `backend/tests/api/test_demo_subscriptions.py`

- [ ] Сначала добавить tests: repeated UPDATE no-op, increase, reduce, close,
  flip, aggregate cap, HIP-3 market и baseline-only.
- [ ] Demo использует тот же `TargetCalculator` и `MarketRegistry`, но отдельный
  instant-fill adapter без account aggregation между пользователями.
- [ ] Demo actual/allocated size хранить в v2 target/allocation state; перестать
  выводить открытую позицию из «последний open без close».
- [ ] На каждый signed delta создавать корректные open/reduce/close UserTrade
  projections и realized PnL; повтор signal/version не создаёт fill.
- [ ] New demo subscription сразу `engine_version=2`; первый текущий leader state
  baseline-only.
- [ ] Legacy demo history остаётся read-only и видимой; завершённые миграцией
  v1 demos не возобновляются через старый simulator.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_demo_executor.py \
  tests/api/test_demo_subscriptions.py -v
```

**Commit:** `feat: run demo subscriptions on target state`

---

### Task 11: Перевести stop/delete/stop-loss/emergency lifecycle на targets

**Files:**

- Create: `backend/app/services/copy_engine/lifecycle.py`
- Create: `backend/app/services/copy_engine/emergency.py`
- Modify: `backend/app/services/subscription_service.py`
- Modify: `backend/app/services/risk_manager.py`
- Modify: `backend/app/tasks/execution_tasks.py`
- Modify: `backend/app/api/wallet.py`
- Modify: `backend/app/schemas/wallet.py`
- Modify: `backend/tests/unit/test_subscription_execution_guards.py`
- Create: `backend/tests/unit/test_copy_lifecycle.py`
- Modify: `backend/tests/api/test_subscriptions.py`
- Modify: `backend/tests/api/test_wallet.py`

- [ ] Сначала добавить tests: остановка одной из двух same-coin subscriptions,
  stop-loss, delete with/without close, pending stop и emergency all DEX.
- [ ] `stop_subscription`: поставить `stopping`, атомарно zero только targets
  этой подписки и reconcile затронутые markets; `stopped` после allocations/fills.
- [ ] `close_positions=false` для live не может оставить unmanaged position в
  dedicated account. Заменить его на «detach запрещён при non-zero target» либо
  обязательный stop-to-zero; сохранить параметр только для demo/history, если
  нужен backward compatibility.
- [ ] Subscription stop-loss zero-ит её targets. Portfolio stop-loss zero-ит все
  v2 targets пользователя и ждёт reconcile, а не вызывает legacy full-position
  close.
- [ ] Emergency `/wallet/close-all` — отдельное явное действие: pause/block все
  subscriptions/parents, прочитать позиции всех DEX, создать известные
  reduce-only emergency intents и потребовать новый flat preflight.
- [ ] Emergency response показывает accepted intents, а не преждевременное
  `closed`; финальный статус берётся из fills.
- [ ] Halted/stale market не открывается. Reduce-only выполняется только при
  подтверждённых metadata/precision; иначе account block с понятной причиной.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_copy_lifecycle.py \
  tests/unit/test_subscription_execution_guards.py \
  tests/api/test_subscriptions.py tests/api/test_wallet.py -v
```

**Commit:** `fix: close only subscription target allocations`

---

### Task 12: Добавить manual preflight/resume API и baseline initialization

**Files:**

- Modify: `backend/app/schemas/subscription.py`
- Modify: `backend/app/api/subscriptions.py`
- Modify: `backend/app/services/subscription_service.py`
- Create: `backend/app/services/copy_engine/resume.py`
- Create: `backend/tests/unit/test_copy_resume.py`
- Modify: `backend/tests/api/test_subscriptions.py`

- [ ] Сначала добавить tests для каждой resume block reason, успешного resume,
  повторного resume и baseline существующих leader positions.
- [ ] Расширить `SubscriptionResponse`: engine version, execution status/reason,
  details, last reconcile и baseline market count.
- [ ] Live create всегда создаёт paused v2 subscription. Нельзя создать active
  mainnet live при выключенном feature flag.
- [ ] Добавить `POST /subscriptions/{id}/preflight` и `/resume`. Resume повторно
  выполняет server preflight в той же операции; результат старого GET не является
  разрешением.
- [ ] Под advisory lock создать baseline-only targets для всех текущих leader
  positions на всех known DEX, затем атомарно включить subscription.
- [ ] Resume требует явного dedicated-account acknowledgement и возвращает
  warning, что существующие leader positions не копируются.
- [ ] Повторный request идемпотентен; paused/blocked/stopping transitions
  проверяются server-side state machine.

**Server verification:**

```bash
cd backend
uv run pytest tests/unit/test_copy_resume.py \
  tests/api/test_subscriptions.py -v
```

**Commit:** `feat: require manual preflight before live resume`

---

### Task 13: Интегрировать portfolio и new-wallet parents

**Files:**

- Modify: `backend/app/services/portfolio/activation.py`
- Modify: `backend/app/services/portfolio/subscription_lifecycle.py`
- Modify: `backend/app/services/portfolio/rebalance.py`
- Modify: `backend/app/tasks/portfolio_tasks.py`
- Modify: `backend/app/api/portfolio_subscriptions.py`
- Modify: `backend/app/schemas/portfolio.py`
- Modify: `backend/app/services/new_wallets/activation.py`
- Modify: `backend/app/tasks/new_wallets.py`
- Modify: `backend/app/api/new_wallets.py`
- Modify: `backend/app/schemas/new_wallet.py`
- Modify: `backend/tests/api/test_portfolio_subscriptions.py`
- Modify: `backend/tests/api/test_new_wallets.py`
- Modify: `backend/tests/unit/test_new_wallet_tasks.py`
- Modify: `backend/tests/unit/test_subscription_execution_guards.py`

- [ ] Сначала добавить tests, доказывающие, что paused/blocked parent нельзя
  активировать background job-ом или rebalance-ом.
- [ ] Live portfolio/new-wallet create создаёт paused parent/children v2; demo
  остаётся сразу исполняемым через v2 demo adapter.
- [ ] Добавить parent `/preflight` и `/resume` endpoints. Portfolio resume
  атомарен: preflight/baseline всех children или ни одного.
- [ ] New-wallet parent resume атомарно возвращает разрешённые paused items;
  auto-attach после resume создаёт новый child baseline-only и не включает его до
  успешного account/leader initialization.
- [ ] Rebalance удаляет/добавляет targets через v2 lifecycle. `close_removed_positions`
  означает zero доли removed child, не full user coin close.
- [ ] Expiry new-wallet zero-ит target и ждёт reconcile; item становится
  `expired` после terminal allocation либо `failed/blocked` с причиной.
- [ ] Все queries фоновых задач требуют parent execution active, child v2 active
  и user account active.
- [ ] Сохранить billing/lifecycle `status` независимо от `execution_status`, чтобы
  миграционная пауза не подменяла факт оплаты.

**Server verification:**

```bash
cd backend
uv run pytest tests/api/test_portfolio_subscriptions.py \
  tests/api/test_new_wallets.py tests/unit/test_new_wallet_tasks.py \
  tests/unit/test_subscription_execution_guards.py -v
```

**Commit:** `feat: gate managed subscriptions on v2 execution state`

---

### Task 14: Обновить frontend для pause/preflight/manual resume

**Files:**

- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/subscriptions.ts`
- Modify: `frontend/src/api/portfolios.ts`
- Modify: `frontend/src/api/newWallets.ts`
- Modify: `frontend/src/api/wallet.ts`
- Create: `frontend/src/components/ExecutionStatusCard.tsx`
- Create: `frontend/src/components/CopyAccountPreflight.tsx`
- Modify: `frontend/src/components/SubscribeModal.tsx`
- Modify: `frontend/src/pages/MyTradesPage.tsx`
- Modify: `frontend/src/pages/WalletPage.tsx`
- Modify: `frontend/src/pages/PortfolioDetailPage.tsx`
- Modify: `frontend/src/pages/NewWalletSubscriptionDetailPage.tsx`
- Modify: `frontend/src/index.css`

- [ ] Добавить strict types для execution state, checks, positions/orders,
  account selection, baseline warning и resume responses.
- [ ] На Wallet page дать выбрать подтверждённый master/subaccount из server
  списка и принять dedicated-account rule; не позволять ввод arbitrary address.
- [ ] Показывать execution status отдельно от subscription/billing lifecycle.
- [ ] Для migration pause объяснить: сверить/закрыть позиции и open orders,
  повторить preflight, затем вручную Resume.
- [ ] Preflight card показывает каждый check и blocking `dex:coin`, side, size,
  oid/cloid; ошибки API не маскировать как success.
- [ ] Resume button доступна только после свежего успешного ответа, но сервер всё
  равно повторяет preflight.
- [ ] Portfolio/new-wallet UI вызывает parent atomic resume и показывает child
  failures. Background refresh не меняет status оптимистично.
- [ ] Показать baseline-only notice после resume/new DEX.
- [ ] ROI UI остаётся без дополнительного `*100`; добавить frontend fixture/build
  assertion, что `roi_pct=5` отображается как `+5.00%`.

**Server verification:**

```bash
cd frontend
npm run build
```

**Commit:** `feat(frontend): add copy execution reconciliation flow`

---

### Task 15: Добавить аудит, метрики и operator runbook

**Files:**

- Create: `backend/app/services/copy_engine/telemetry.py`
- Modify: `backend/app/core/logging.py`
- Modify: `backend/app/core/scheduler.py`
- Modify: `backend/app/api/health.py`
- Modify: `README.md`
- Create: `docs/copy_engine_v2_runbook.md`
- Modify: `.env.example`
- Modify: `.env.prod.example`
- Modify: `Makefile`
- Create: `backend/tests/api/test_copy_engine_health.py`

- [ ] Ввести structured event helpers с correlation keys: user, subscription,
  dex/coin, signal, target version, intent, cloid/oid. Не логировать agent key,
  signatures и чувствительные payload.
- [ ] Добавить Redis counters/gauges: accepted/skipped signals, target changes,
  intent states, dedupe hits, drift blocks, reconcile lag, registry age/errors,
  v1-at-v2-boundary и unexplained delta.
- [ ] Health/readiness показывает v2 flag, registry freshness, pending unknown
  count и reconcile lag без раскрытия пользовательских данных.
- [ ] Runbook описывает pause reasons, preflight/remediation, block investigation,
  feature flag, testnet smoke, rollout, rollback и запрет автоматического resume.
- [ ] Добавить one-time `make deploy-copy-engine-v2`: build images, **stop old
  backend/scheduler before migration**, run Alembic, start with flag false. Не
  менять обычный deploy так, чтобы миграция незаметно шла параллельно v1 worker.
- [ ] Runbook явно запрещает Alembic downgrade как operational rollback после
  появления v2 intents; rollback — flag off + pause + old image без v1 resume.

**Server verification:**

```bash
cd backend
uv run pytest tests/api/test_copy_engine_health.py -v
```

**Commit:** `chore: add copy engine v2 operations controls`

---

### Task 16: Полная серверная регрессия и статическая проверка

**Files:**

- Modify as failures require, без ослабления safety assertions.

- [ ] Push implementation branch/commits по normal release path.
- [ ] На сервере подтвердить clean worktree и точный commit SHA.
- [ ] Запустить focused v2 suite, затем весь backend suite.
- [ ] Запустить Ruff, Black check, strict mypy и frontend build только на сервере.
- [ ] Проверить Alembic single head и upgrade на disposable DB snapshot.
- [ ] Выполнить SQL audit migration pause counts по manual/model/new-wallet,
  parents/items, v1 signals и pending legacy trades.
- [ ] Исправлять причины, а не удалять/ослаблять failing tests.

**Server commands:**

```bash
cd backend
uv run pytest tests/unit/test_roi_units.py \
  tests/unit/test_market_registry.py \
  tests/unit/test_signal_detector.py \
  tests/unit/test_target_calculator.py \
  tests/unit/test_copy_reconciler.py \
  tests/unit/test_drift_detector.py \
  tests/unit/test_copy_resume.py \
  tests/api/test_copy_execution.py \
  tests/api/test_subscriptions.py \
  tests/api/test_portfolio_subscriptions.py \
  tests/api/test_new_wallets.py -v
uv run pytest tests/ -v --tb=short
uv run ruff check .
uv run black --check .
uv run mypy app/
uv run alembic heads
cd ../frontend
npm run build
```

**Commit:** `test: cover copy engine v2 safety invariants`

---

### Task 17: Hyperliquid testnet smoke без mainnet риска

**Files:**

- Extend: `backend/scripts/validate_hl_signing.py`
- Create: `backend/scripts/smoke_copy_engine_v2.py`
- Modify: `docs/copy_engine_v2_runbook.md`

- [ ] Скрипт требует `HL_NETWORK=testnet`, отдельный test master/subaccount и
  явный CLI acknowledgement; при mainnet немедленно завершается.
- [ ] Проверить registry/default DEX/HIP-3 asset id, mids, account mode,
  subaccount `vaultAddress`, `cloid` query и open-orders listing.
- [ ] Маленьким допустимым notional пройти open, same-target retry, increase,
  reduce-only decrease, partial/cancel recovery, close и two-phase flip.
- [ ] На отдельном disposable test account создать manual order и доказать
  unknown-order/fill drift block без corrective order.
- [ ] Проверить Standard и Unified test accounts; legacy abstraction/Portfolio
  Margin должны быть отклонены preflight.
- [ ] После smoke закрыть testnet positions, отменить orders и сохранить только
  redacted результаты/oid/cloid correlation в release notes.

**Server command:**

```bash
cd backend
HL_NETWORK=testnet COPY_ENGINE_V2_LIVE_ENABLED=true \
  uv run python scripts/smoke_copy_engine_v2.py --ack-testnet
```

**Commit:** `test: add copy engine v2 testnet smoke`

---

### Task 18: Безопасный production rollout с обязательной ручной сверкой

**Предусловия:**

- Все Task 16 checks зелёные на сервере.
- Testnet smoke Task 17 зелёный.
- Implementation commits pushed; production worktree clean.
- `.env.prod` содержит `COPY_ENGINE_V2_LIVE_ENABLED=false`.
- Подготовлен DB backup/restore point и список текущих live/pending counts.

- [ ] До миграции снять read-only audit: active live subscriptions по source,
  parent/items, actual positions/open orders по всем DEX и pending trades.
- [ ] Собрать новые images, затем остановить старый backend, чтобы scheduler/v1
  executor не работал одновременно с data migration.
- [ ] Выполнить `make deploy-copy-engine-v2`; проверить Alembic head и что backend
  поднялся с live flag false.
- [ ] SQL audit: каждая существовавшая live subscription inactive/paused v1;
  parents/items paused; v2 targets пусты; ни одна подписка не активировалась.
- [ ] Проверить health, logs и UI migration notice. Ни одного exchange order на
  этом этапе быть не должно.
- [ ] На одном внутреннем canary dedicated account выполнить preflight/resume и
  убедиться, что текущие leader positions baseline-only.
- [ ] Включить `COPY_ENGINE_V2_LIVE_ENABLED=true` только отдельным явным config
  change/restart после canary approval.
- [ ] Возобновлять остальные subscriptions только вручную пользователем после
  flat-account preflight. Не выполнять SQL/API bulk resume.
- [ ] Наблюдать unknown intents, drift, rejects, lag и registry freshness. При
  нарушении выключить flag и pause v2; не запускать v1 для resumed subscriptions.

**Production release commit:** код не меняется. Release фиксирует deployed SHA,
migration revision и redacted audit counts в operator log.

---

## Финальная матрица приёмки

| Инвариант | Доказательство |
|---|---|
| ROI `0.05` означает `5%` | Migration + ingest/API/scoring/export tests |
| UPDATE не накапливает full size | Signal/target/reconciler repeated-update tests |
| Reduction уменьшает позицию | Signed delta + reduce-only fill test |
| Caps относятся к конечной экспозиции | Multi-market proportional-scaling tests |
| HIP-3 не блокируется whitelist | Registry/asset-id/default+HIP-3 tests |
| Повтор/restart не дублирует order | Durable intent/cloid crash tests |
| Partial fill исполняет только остаток | Reconcile state-machine tests + testnet |
| Остановка одной подписки не закрывает чужую долю | Multi-sub allocation test |
| Manual trade не автокорректируется | Drift block test + testnet manual order |
| Legacy live после migration не торгует | SQL audit + v1 boundary test |
| Resume только вручную и с flat account | API/preflight tests + rollout audit |
| Existing leader position не догоняется | Baseline create/resume/new-DEX tests |
| Mainnet выключен до явного rollout | Config default + health + deployment audit |

## Официальные API-контракты, используемые при реализации

- [Hyperliquid Info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Hyperliquid perpetual Info methods](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)
- [Hyperliquid Exchange endpoint and cloid](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint)
- [Hyperliquid account abstraction modes](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/account-abstraction-modes)
- [Hyperliquid WebSocket subscriptions](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)
