# ИИ/алгоритмический помощник для готовых copy-trading портфелей

**Дата**: 2026-06-29
**Статус**: планирование
**Контекст**: текущий продукт уже умеет находить трейдеров Hyperliquid, считать метрики, создавать demo/live подписки и исполнять копи-сделки. Новая доработка должна добавить платный слой готовых портфелей поверх существующего механизма подписок.

---

## 1. Короткий вывод

Идею стоит реализовывать, но не как "ИИ угадывает прибыльных трейдеров". Правильная формулировка продукта:

> Готовый риск-контролируемый model portfolio для copy trading: алгоритм выбирает трейдеров, распределяет веса, регулярно пересматривает состав, а ИИ/текстовый помощник объясняет решения понятным языком.

Пользователь платит не за нейросеть как таковую. Он платит за:

- готовый список трейдеров;
- веса и настройки копирования;
- регулярный пересмотр портфеля;
- фильтрацию опасных трейдеров;
- backtest/paper track record;
- объяснения, почему трейдер добавлен или удален;
- экономию времени и снижение ошибки выбора по одному leaderboard.

Главное архитектурное решение: **не создавать второй execution pipeline**. Портфель должен создавать и поддерживать обычные `subscriptions`, которые уже исполняются текущим copy-engine.

---

## 2. Фокус-группа по ролям

### 2.1 Системный аналитик

**Вывод**: нужна новая доменная сущность `model_portfolio`. Портфель - это не просто список трейдеров. У него должны быть версии, состав, веса, статусы, история ребалансов, привязка к пользователям и аудит решений.

Новые доменные объекты:

- **Model portfolio** - шаблон продукта, например `Balanced`, `Conservative`, `Aggressive`.
- **Portfolio version** - неизменяемая опубликованная версия состава портфеля.
- **Portfolio allocation** - трейдер внутри версии портфеля: вес, настройки копирования, риск-настройки, причины включения.
- **User portfolio subscription** - подписка пользователя на готовый портфель.
- **User portfolio item** - связь между портфельной подпиской пользователя и обычной `Subscription`.
- **Rebalance event** - событие изменения состава или весов.
- **Backtest result** - воспроизводимый результат тестирования версии портфеля.
- **Decision audit** - какие метрики и правила привели к включению/исключению трейдера.

Ключевые правила:

- опубликованную версию портфеля нельзя менять задним числом;
- пользователь всегда должен быть привязан к конкретной версии портфеля;
- ребаланс должен быть идемпотентным;
- ручные подписки пользователя и портфельные подписки не должны смешиваться;
- отмена портфеля не должна отключать ручные подписки;
- пересечение ручной подписки и портфельного трейдера должно обрабатываться явно.

Нерешенный бизнес-вопрос:

> Если пользователь уже вручную подписан на трейдера X, а готовый портфель тоже содержит X, что делать?

Варианты:

- заблокировать live-активацию портфеля до решения конфликта;
- объединить экспозицию;
- разрешить дубль с предупреждением;
- перевести ручную подписку в управляемую портфелем.

Рекомендация для MVP: **блокировать duplicate live exposure** и показать экран решения конфликта. В demo можно разрешить дублирование.

### 2.2 Бизнес-аналитик

**Вывод**: продукт может быть востребован, но цена должна учитывать размер депозита. Для маленьких депозитов высокая подписка быстро уничтожает экономический смысл.

Сегменты пользователей:

- новичок: не понимает, кого копировать, хочет "готовое решение";
- занятый пользователь: умеет выбирать, но не хочет тратить время;
- пользователь с просадкой: уже ошибался при выборе трейдеров;
- продвинутый пользователь: хочет использовать портфель как benchmark или базовый слой;
- demo-пользователь: хочет посмотреть, как работал бы портфель без риска.

Что продавать:

- не "ИИ принесет доход";
- не "лучшие трейдеры";
- не "гарантированный профит";
- а **готовый диверсифицированный copy-trading портфель с методологией, risk limits и ребалансом**.

Основные KPI:

- conversion из страницы портфеля в demo;
- conversion из demo в paid;
- paid activation rate;
- средний размер allocation;
- 30-day retention;
- отмены после просадки;
- количество support tickets на 100 платных пользователей;
- доля пользователей, которые активировали portfolio вместо ручного выбора трейдеров;
- execution failure rate по портфельным подпискам.

### 2.3 Solution architect

**Вывод**: ИИ не должен принимать торговое решение в MVP. Выбор трейдеров должен быть детерминированным, воспроизводимым и проверяемым. ИИ можно использовать как слой объяснений.

Что уже есть в проекте:

- `backend/app/models/trader.py` уже содержит расширенные метрики трейдера: `composite_score`, Sharpe, Sortino, drawdown, profit factor, active days, average leverage.
- `backend/app/models/subscription.py` уже поддерживает `sizing_mode`, `max_per_coin_usd`, `allowed_coins`, `is_demo`.
- `backend/app/models/user.py` уже содержит `portfolio_stop_loss_pct` и `builder_fee_approved_at`.
- `backend/app/services/subscription_service.py` уже умеет создавать demo/live подписки и проверять portfolio-level risk.
- Реальные периодические задачи сейчас подключены через `backend/app/core/scheduler.py` и APScheduler. Документация в `AGENTS.md` местами еще говорит про Celery, но фактический код использует APScheduler.

Архитектурное решение:

```text
Trader metrics
  -> hard filters
  -> portfolio-specific scoring
  -> correlation/diversification checks
  -> allocation optimizer
  -> backtest
  -> draft portfolio version
  -> admin/manual approval
  -> published portfolio version
  -> user portfolio activation
  -> ordinary subscriptions
  -> existing signal/execution pipeline
```

### 2.4 Fullstack QA engineer

**Вывод**: основные риски не в UI, а в неверных backtest, дублировании экспозиции, ошибочном ребалансе и плохой идемпотентности.

Что обязательно тестировать:

- версия портфеля immutable после публикации;
- веса в версии суммируются в 100%;
- один и тот же портфель нельзя активировать live дважды;
- ручные подписки не меняются при отмене портфеля;
- портфельный ребаланс не трогает manual subscriptions;
- повторный apply одного ребаланса не применяет изменения второй раз;
- просроченный платеж блокирует live-активацию и auto-rebalance;
- demo и live подписки разделены;
- backtest не использует данные из будущего;
- performance показывает комиссии и slippage assumptions.

### 2.5 Fullstack developer

**Вывод**: MVP можно сделать эволюционно, без переписывания copy-engine.

Новые backend-модули:

- `app/models/portfolio.py`
- `app/schemas/portfolio.py`
- `app/api/portfolios.py`
- `app/services/portfolio/candidates.py`
- `app/services/portfolio/scoring.py`
- `app/services/portfolio/correlation.py`
- `app/services/portfolio/optimizer.py`
- `app/services/portfolio/backtest.py`
- `app/services/portfolio/publisher.py`
- `app/services/portfolio/rebalance.py`
- `app/services/portfolio/explanations.py`
- `app/tasks/portfolio_tasks.py`

Новые frontend-модули:

- `frontend/src/api/portfolios.ts`
- `frontend/src/pages/PortfoliosPage.tsx`
- `frontend/src/pages/PortfolioDetailPage.tsx`
- `frontend/src/pages/PortfolioSubscriptionPage.tsx`
- компоненты portfolio card, allocation row, rebalance diff, pricing gate.

### 2.6 UI/UX специалист

**Вывод**: продукту нужен интерфейс доверия, а не лендинг с обещаниями. Пользователь должен быстро понять состав, риск, track record и причины решений.

Нужные экраны:

- вкладка `Portfolios`;
- список готовых портфелей;
- detail page портфеля;
- состав и веса;
- risk/performance summary;
- backtest assumptions;
- активация demo/live;
- экран конфликта с ручными подписками;
- preview ребаланса;
- история изменений.

Что важно показать:

- 30/90/180 day performance;
- max drawdown;
- количество трейдеров;
- last rebalance date;
- min suggested balance;
- текущая версия портфеля;
- "paper/live tracked since";
- комиссии/slippage assumptions.

Не надо:

- hero-лендинг внутри Mini App;
- обещания доходности;
- текст "ИИ гарантирует";
- черный ящик без объяснений.

---

## 3. Узкие места и непродуманные этапы

### 3.1 Правовой риск

Платный список трейдеров может быть воспринят как инвестиционная рекомендация, особенно если есть автоматическое исполнение и пользователь платит за подбор.

Что сделать до публичного запуска:

- не обещать доходность;
- не использовать формулировки "безопасно", "гарантированно", "стабильный доход";
- позиционировать как model portfolio с методологией и рисками;
- не персонализировать рекомендации под финансовое положение пользователя в MVP;
- показывать risk disclosure перед live activation;
- провести legal review по целевым юрисдикциям;
- не вводить performance fee без юридической проверки.

### 3.2 Data quality

Leaderboard может обманывать:

- трейдер мог случайно попасть в топ за неделю;
- трейдер мог сменить стратегию;
- высокая доходность могла быть на огромном плече;
- fills могут быть неполными;
- часть активности может быть spot/prediction-market, а copy-engine копирует perps;
- маленькие быстрые сделки могут быть некопируемыми для пользователя.

Контрмеры:

- `has_perp_activity = true`;
- минимальный `active_trading_days`;
- минимальный `trade_count`;
- фильтр по leverage;
- фильтр по drawdown;
- фильтр по copyability;
- хранить snapshot метрик на момент выбора;
- не выбирать только по PnL/ROI.

### 3.3 Backtest

Главная опасность - показать красивый, но нереалистичный backtest.

Ошибки, которых нельзя допустить:

- lookahead bias: выбор трейдеров по будущим данным;
- survivorship bias: учет только выживших трейдеров;
- игнор комиссий;
- игнор slippage;
- предположение, что все сделки идеально исполняются;
- игнор minimum order size;
- игнор разницы между депозитом трейдера и депозитом пользователя.

MVP-правило:

- сначала показывать `simulation` и `paper track record`;
- честно указывать assumptions;
- не запускать агрессивный paid marketing без live paper history.

### 3.4 Rebalance

Самое опасное место в реализации.

Риски:

- случайно отключить ручную подписку;
- создать дубликаты;
- изменить sizing на открытой позиции без понятной политики;
- удалить трейдера, но оставить orphan position;
- повторить один и тот же ребаланс два раза;
- применить ребаланс пользователю с просроченной оплатой;
- применить auto-rebalance пользователю, который его выключил.

Контрмеры:

- `source_type = manual|model_portfolio`;
- `source_id` на `user_portfolio_subscriptions`;
- idempotency key на каждый rebalance;
- preview diff перед apply;
- отдельный статус per user rebalance;
- manual approval для MVP;
- dry-run режим.

### 3.5 Pricing

Если цена слишком высокая, маленькие аккаунты не будут покупать. Если бесплатно, продукт не проверит willingness to pay и не окупит поддержку.

Контрмеры:

- бесплатный manual copy trading оставить;
- demo portfolio бесплатный;
- live managed portfolio платный;
- начать с низкой цены;
- performance fee отложить.

### 3.6 AI

LLM может придумать объяснение, которого нет в данных.

Контрмеры:

- ИИ не принимает решение в MVP;
- ИИ получает только structured facts;
- если фактов нет, используется шаблон;
- текст не должен содержать прогноз доходности;
- все explanations сохраняются с source facts.

---

## 4. Правильная архитектура

### 4.1 Принцип

Портфельный помощник:

- выбирает трейдеров;
- создает версии портфелей;
- создает обычные `subscriptions`;
- управляет ребалансом;
- хранит аудит решений.

Он не должен:

- напрямую отправлять ордера;
- обходить risk manager;
- хранить agent keys;
- дублировать copy-engine;
- изменять manual subscriptions.

### 4.2 Новые таблицы

#### `model_portfolios`

Шаблон портфеля.

Поля:

- `id`
- `slug`
- `name`
- `risk_profile`: `conservative|balanced|aggressive`
- `status`: `draft|active|paused|retired`
- `description`
- `methodology_version`
- `rebalance_cadence`
- `min_equity_usd`
- `monthly_price_usd`
- `trial_days`
- `created_at`
- `updated_at`

#### `model_portfolio_versions`

Неизменяемая версия состава.

Поля:

- `id`
- `portfolio_id`
- `version_no`
- `status`: `draft|published|retired|rejected`
- `valid_from`
- `valid_to`
- `created_by`
- `approved_by`
- `approved_at`
- `approval_note`
- `selection_started_at`
- `selection_finished_at`
- `facts_hash`
- `summary_json`
- `created_at`

Ограничения:

- unique `(portfolio_id, version_no)`;
- только одна текущая `published` версия на портфель.

#### `model_portfolio_allocations`

Состав версии портфеля.

Поля:

- `id`
- `version_id`
- `trader_id`
- `target_weight_pct`
- `copy_ratio_pct`
- `max_leverage`
- `stop_loss_pct`
- `sizing_mode`
- `max_per_coin_usd`
- `allowed_coins`
- `reason_code`
- `reason_text`
- `score_snapshot`
- `constraint_snapshot`
- `created_at`

Валидация:

- сумма `target_weight_pct` по версии = 100%;
- unique `(version_id, trader_id)`.

#### `user_portfolio_subscriptions`

Подписка пользователя на модельный портфель.

Поля:

- `id`
- `user_id`
- `portfolio_id`
- `active_version_id`
- `status`: `trialing|active|past_due|paused|canceled`
- `is_demo`
- `auto_rebalance`
- `total_allocation_usd`
- `close_removed_positions`
- `billing_provider`
- `billing_customer_id`
- `billing_subscription_id`
- `current_period_end`
- `created_at`
- `updated_at`
- `canceled_at`

#### `user_portfolio_items`

Связь между портфельной подпиской и обычными `subscriptions`.

Поля:

- `id`
- `user_portfolio_subscription_id`
- `subscription_id`
- `portfolio_version_id`
- `allocation_id`
- `trader_id`
- `target_allocation_usd`
- `target_weight_pct`
- `status`: `active|removed|failed|paused`
- `created_at`
- `removed_at`

#### `portfolio_rebalance_events`

История ребалансов.

Поля:

- `id`
- `portfolio_id`
- `from_version_id`
- `to_version_id`
- `user_portfolio_subscription_id`
- `event_type`: `scheduled|emergency|manual|user_apply`
- `status`: `draft|pending|running|completed|failed|skipped`
- `diff_json`
- `error_msg`
- `idempotency_key`
- `created_at`
- `executed_at`

#### `portfolio_backtests`

Результаты backtest версии.

Поля:

- `id`
- `portfolio_version_id`
- `period_days`
- `initial_equity_usd`
- `total_return_pct`
- `max_drawdown_pct`
- `sharpe_ratio`
- `sortino_ratio`
- `win_rate_pct`
- `turnover_pct`
- `fees_usd`
- `slippage_usd`
- `missed_trade_count`
- `assumptions_json`
- `equity_curve_json`
- `created_at`

#### Изменения в `subscriptions`

Добавить nullable-поля:

- `source_type TEXT NOT NULL DEFAULT 'manual'`
- `source_id BIGINT NULL`
- `source_version_id BIGINT NULL`
- `managed_by_portfolio BOOLEAN NOT NULL DEFAULT false`

Это позволит портфельному движку понимать, какие подписки принадлежат ему, а какие пользователь создал вручную.

### 4.3 Backend service layer

#### `portfolio/candidates.py`

Задача:

- загрузить подходящих трейдеров;
- применить hard filters;
- вернуть кандидатов со всеми нужными метриками.

Фильтры MVP:

- `Trader.is_active = true`;
- `Trader.has_perp_activity = true`;
- `composite_score >= 70` для Balanced;
- `trade_count >= 20`;
- `active_trading_days >= 30`;
- `max_drawdown_pct <= 35`;
- `avg_leverage <= 8`;
- `avg_trades_per_day` не слишком высокий;
- essential metrics не null.

#### `portfolio/scoring.py`

Задача:

- пересчитать score именно для портфельного отбора;
- сохранить snapshot принятого решения.

Пример формулы:

```text
portfolio_score =
  0.30 * risk_adjusted_score
+ 0.25 * consistency_score
+ 0.15 * return_score
+ 0.15 * copyability_score
+ 0.10 * diversification_score
+ 0.05 * behavior_stability_score
```

`composite_score` из `TraderStat` использовать как вход, а не как единственный критерий.

#### `portfolio/correlation.py`

Задача:

- считать корреляцию между трейдерами;
- не позволять портфелю состоять из 8 трейдеров, которые фактически открывают одинаковые сделки.

MVP:

- daily realized PnL vectors;
- Pearson correlation за 30/90 дней;
- minimum overlapping days;
- если данных мало, считать correlation unknown и штрафовать.

#### `portfolio/optimizer.py`

Задача:

- выбрать N трейдеров и веса.

MVP-алгоритм:

1. Отсортировать кандидатов по `portfolio_score`.
2. Жадно добавлять трейдеров, если проходят constraints:
   - max correlation;
   - max leverage;
   - max same-coin exposure;
   - max strategy bucket concentration.
3. Посчитать первичные веса:

```text
raw_weight = portfolio_score / max(max_drawdown_pct, 5)
```

4. Применить caps:
   - Conservative: max 20% на трейдера;
   - Balanced: max 18%;
   - Aggressive: max 15%.
5. Нормализовать веса до 100%.
6. Провалидировать результат.

Позже можно добавить `scipy.optimize`, но для MVP лучше простой и объяснимый алгоритм.

#### `portfolio/backtest.py`

Задача:

- симулировать историческое поведение версии портфеля.

Assumptions:

- initial equity: `$1,000`, `$5,000`, `$10,000`;
- комиссии;
- slippage;
- minimum order size;
- missed trades;
- weekly rebalance;
- no future data.

Выход:

- equity curve;
- return;
- max drawdown;
- Sharpe/Sortino;
- turnover;
- fees/slippage;
- missed trade count;
- worst day/week.

#### `portfolio/publisher.py`

Задача:

- создать draft version;
- запустить validation;
- запустить backtest;
- отправить на approval;
- опубликовать версию;
- закрыть предыдущую версию.

MVP-правило: публикация только после ручного approval.

#### `portfolio/rebalance.py`

Задача:

- сравнить текущую версию пользователя с новой;
- создать diff;
- применить изменения к обычным `subscriptions`;
- закрыть удаленные позиции, если так настроено;
- сохранить `portfolio_rebalance_events`.

Типы diff:

- `add_trader`;
- `remove_trader`;
- `change_weight`;
- `change_risk_settings`;
- `no_change`;
- `blocked_by_user_conflict`;
- `blocked_by_payment`;
- `blocked_by_wallet`;
- `failed_risk_check`.

#### `portfolio/explanations.py`

Задача:

- генерировать объяснения.

MVP:

- шаблоны на основе метрик.

Позже:

- LLM summary из structured JSON;
- сохранение prompt version, source facts и generated text.

### 4.4 API

Публичные/read endpoints:

```http
GET /portfolios
GET /portfolios/{slug}
GET /portfolios/{slug}/versions/current
GET /portfolios/{slug}/backtests
GET /portfolios/{slug}/track-record
```

Пользовательские endpoints:

```http
POST /portfolio-subscriptions
GET /portfolio-subscriptions
GET /portfolio-subscriptions/{id}
POST /portfolio-subscriptions/{id}/preview-rebalance
POST /portfolio-subscriptions/{id}/apply-rebalance
PATCH /portfolio-subscriptions/{id}
DELETE /portfolio-subscriptions/{id}
```

Admin/internal endpoints или CLI:

```http
POST /admin/portfolios/{id}/build-draft
POST /admin/portfolio-versions/{id}/run-backtest
POST /admin/portfolio-versions/{id}/publish
POST /admin/portfolio-versions/{id}/reject
```

### 4.5 Фоновые задачи

Текущий проект использует APScheduler в `backend/app/core/scheduler.py`.

Добавить задачи:

- `build_portfolio_drafts_async`
  Daily/manual. Создает draft версии.

- `run_portfolio_backtests_async`
  Daily после draft build.

- `monitor_model_portfolio_risk_async`
  Каждые 5-15 минут. Ищет emergency triggers.

- `publish_scheduled_portfolio_versions_async`
  Weekly, но в MVP только после ручного approval.

- `apply_due_user_rebalances_async`
  Каждые 5 минут. Применяет опубликованные версии к eligible users.

Требования:

- Redis lock на каждую job;
- idempotency key на каждый user-level rebalance;
- low priority для тяжелых Hyperliquid-запросов;
- не мешать realtime polling.

---

## 5. Методология выбора трейдеров

### 5.1 Risk profiles

#### Conservative

Для пользователей, которым важнее сниженная волатильность.

Параметры:

- 5-7 трейдеров;
- max 20% на трейдера;
- `composite_score >= 78`;
- `max_drawdown_pct <= 20`;
- `avg_leverage <= 4`;
- `active_trading_days >= 45`;
- `trade_count >= 30`;
- max correlation: `0.55`.

#### Balanced

Базовый MVP-портфель.

Параметры:

- 6-10 трейдеров;
- max 18% на трейдера;
- `composite_score >= 70`;
- `max_drawdown_pct <= 35`;
- `avg_leverage <= 8`;
- `active_trading_days >= 30`;
- `trade_count >= 20`;
- max correlation: `0.65`.

#### Aggressive

Только после накопления track record.

Параметры:

- 8-12 трейдеров;
- max 15% на трейдера;
- `composite_score >= 65`;
- `max_drawdown_pct <= 50`;
- `avg_leverage <= 15`;
- `active_trading_days >= 20`;
- `trade_count >= 20`;
- max correlation: `0.75`.

Рекомендация: в MVP запускать только `Balanced`.

### 5.2 Hard exclude

Трейдер исключается, если:

- inactive;
- нет perp activity;
- мало дней активности;
- мало сделок;
- слишком высокий leverage;
- слишком большая просадка;
- слишком высокая корреляция с уже выбранными;
- много микросделок, которые плохо копируются;
- слишком резкий recent behavior shift;
- данные неполные.

### 5.3 Copyability score

Отдельная метрика, потому что profitable trader не всегда copyable trader.

Факторы:

- average position size;
- average trade duration;
- trades per day;
- coin liquidity;
- min order feasibility;
- slippage sensitivity;
- supported coin whitelist.

Пример:

```text
copyability_score =
  0.30 * size_score
+ 0.25 * holding_time_score
+ 0.20 * liquidity_score
+ 0.15 * trade_frequency_score
+ 0.10 * min_order_feasibility_score
```

### 5.4 Emergency removal

Трейдер может быть удален вне планового weekly rebalance, если:

- текущий leverage превысил emergency cap;
- recent drawdown превысил порог;
- трейдер перешел в неподдерживаемые монеты;
- нет валидного snapshot;
- copy execution по нему часто падает;
- correlation с другими трейдерами резко выросла;
- трейдер стал inactive.

MVP: emergency event создает draft и уведомляет админа. Автоматическое удаление включать позже.

---

## 6. Где использовать ИИ

### 6.1 Нужно использовать

- объяснения пользователю;
- weekly portfolio report;
- summary ребаланса;
- классификация поведения трейдера;
- внутренние аналитические заметки.

Пример объяснения:

```text
Трейдер исключен из Balanced, потому что среднее плечо выросло с 4.3x до 12.1x,
30-дневная просадка достигла 18.4%, а корреляция с двумя текущими трейдерами
превысила лимит портфеля.
```

### 6.2 Не использовать в MVP

- финальный выбор трейдеров;
- прогноз доходности;
- обход hard filters;
- персональные инвестиционные советы;
- объяснения без source facts.

---

## 7. Ценообразование

### 7.1 Принципы

Цена должна быть:

- ощутимой, чтобы проверить спрос;
- не слишком высокой для депозитов $1k-$5k;
- простой;
- без performance fee в MVP;
- с бесплатным demo.

Ручной copy trading лучше оставить бесплатным, иначе сузится входная воронка.

### 7.2 Рекомендуемые тарифы

#### Free

Цена: `$0`

Что входит:

- ручной выбор трейдеров;
- обычные manual subscriptions;
- базовые метрики;
- просмотр demo портфеля;
- ограниченный/delayed track record;
- без live managed portfolio.

#### Portfolio Basic

Цена: **$19/month**

Что входит:

- один готовый live-портфель `Balanced`;
- автоматическое создание набора подписок;
- weekly rebalance suggestions;
- risk explanations;
- portfolio performance page;
- demo без ограничений.

Это лучший MVP-тариф.

#### Portfolio Pro

Цена: **$39/month**

Запускать не сразу, а после подтверждения retention.

Что входит:

- Conservative/Balanced/Aggressive;
- auto-rebalance;
- weekly AI report;
- advanced backtests;
- priority emergency replacement;
- export/report.

#### Performance fee

Не запускать в MVP.

Возможный future вариант:

- 5-10% от net realized profit;
- только после legal review;
- только с high-water mark;
- только если PnL attribution надежный.

### 7.3 Проверка цены по депозиту

Подписка не должна съедать слишком большую часть капитала.

| Copy capital | Комфортная цена | Комментарий |
|---:|---:|---|
| $500 | $5-$9 | Тяжело монетизировать фиксированной подпиской |
| $1,000 | $9-$19 | $19 уже верхняя граница |
| $3,000 | $19-$39 | Лучший ранний сегмент |
| $5,000+ | $29-$49 | Pro становится разумным |

Рекомендация: целиться в пользователей с copy capital от `$1,000-$3,000`.

### 7.4 Trial

Рекомендация:

- 7-day trial для live;
- demo unlimited;
- annual discount: 2 месяца бесплатно;
- early adopter price lock для первых 100 платящих.

---

## 8. Пошаговый план разработки

### Phase 0 — Product/legal foundation

Цель: зафиксировать, что именно продаем.

Решения для MVP от 2026-07-02:

- Позиционирование: **готовый model portfolio для copy trading**, а не "ИИ гарантирует доход" и не персональная инвестиционная рекомендация.
- MVP: только `Balanced`, deterministic builder, demo-first flow, публикация версий только после manual approval.
- Цена: `Portfolio Basic = $19/month`; demo portfolio бесплатно; manual copy trading остается бесплатным.
- Платежный провайдер для MVP: `stripe` как значение по умолчанию в `billing_provider`; окончательное подключение webhook/checkout выполняется в Phase 5.
- Conflict policy: live activation блокируется, если у пользователя уже есть активная manual live subscription на трейдера из портфеля; demo activation может создать дублирующую demo subscription, потому что она не создает реальную экспозицию.
- Risk disclosure: перед live activation пользователь должен подтвердить, что copy trading связан с риском потери средств, исторические результаты и backtest не гарантируют будущую доходность, исполнение может отличаться из-за комиссий, slippage, ликвидности, minimum order size и задержек.
- Methodology disclosure: Balanced строится алгоритмически из публичных/собранных метрик трейдеров, применяет hard filters по активности, perp activity, drawdown, leverage, trade count и score, затем распределяет веса с caps и диверсификационными ограничениями; LLM не выбирает трейдеров в MVP.
- Legal review: обязателен до публичной платной продажи; до review запрещены формулировки "гарантированно", "безопасно", "стабильный доход" и персональные обещания доходности.

Задачи:

1. Утвердить позиционирование: model portfolio, не "ИИ гарантирует доход".
2. Утвердить MVP: только Balanced.
3. Утвердить цену: `$19/month`.
4. Выбрать платежный провайдер.
5. Утвердить conflict policy для manual vs portfolio subscriptions.
6. Подготовить risk disclosure.
7. Подготовить methodology disclosure.
8. Провести legal review перед публичной продажей.

Exit criteria:

- есть финальный текст продукта;
- есть risk disclosure;
- есть pricing;
- есть решение по конфликтам.

### Phase 1 — Database и модели

Цель: добавить доменную модель портфелей.

Задачи:

1. Создать `backend/app/models/portfolio.py`.
2. Добавить Alembic migration:
   - `model_portfolios`;
   - `model_portfolio_versions`;
   - `model_portfolio_allocations`;
   - `user_portfolio_subscriptions`;
   - `user_portfolio_items`;
   - `portfolio_rebalance_events`;
   - `portfolio_backtests`;
   - новые поля в `subscriptions`.
3. Добавить `backend/app/schemas/portfolio.py`.
4. Добавить seed/script для `Balanced`.
5. Обновить imports моделей.

Тесты:

- migration upgrade;
- model import;
- schema serialization;
- существующие manual subscriptions не сломались.

### Phase 2 — Portfolio builder MVP

Цель: автоматически собрать draft Balanced.

Задачи:

1. Реализовать `portfolio/candidates.py`.
2. Реализовать `portfolio/scoring.py`.
3. Реализовать `portfolio/correlation.py`.
4. Реализовать `portfolio/optimizer.py`.
5. Реализовать `portfolio/publisher.py`.
6. Сохранять `score_snapshot` и `constraint_snapshot`.
7. Добавить CLI/admin команду build draft.

Тесты:

- фильтры кандидатов;
- стабильность score;
- веса суммируются в 100%;
- max weight работает;
- high-correlation candidate отклоняется.

Реализация от 2026-07-02:

- добавлены сервисы `backend/app/services/portfolio/candidates.py`, `scoring.py`, `correlation.py`, `optimizer.py`, `publisher.py`;
- builder использует только существующие `Trader`/`TraderStat` метрики, deterministic scoring и greedy optimizer с caps;
- `publisher.py` создает только `draft` версии и не публикует их автоматически, manual approval boundary сохраняется;
- `score_snapshot` и `constraint_snapshot` сохраняются на allocation-level;
- добавлена CLI-команда `backend/scripts/build_model_portfolio_draft.py`;
- добавлены unit-тесты `backend/tests/unit/test_portfolio_builder.py`;
- новая Alembic migration для Phase 2 не нужна: используются таблицы и поля из Phase 1 migration `o1p2q3r4s5t6_add_model_portfolio_tables.py`.

Exit criteria:

- локально создается draft версия Balanced из реальных метрик.

### Phase 3 — Backtest и read-only UI

Цель: показать портфель пользователю без live активации.

Backend:

1. Реализовать `portfolio/backtest.py`.
2. Сохранять результаты в `portfolio_backtests`.
3. Добавить API:
   - `GET /portfolios`;
   - `GET /portfolios/{slug}`;
   - `GET /portfolios/{slug}/backtests`.
4. Подключить `app/api/portfolios.py` в router.

Frontend:

1. Добавить `frontend/src/api/portfolios.ts`.
2. Добавить `/portfolios`.
3. Добавить `/portfolios/:slug`.
4. Добавить вкладку `Portfolios`.
5. Показать allocation, metrics, assumptions.

Тесты:

- API tests;
- golden backtest fixture;
- frontend render smoke.

Exit criteria:

- пользователь видит Balanced, состав, веса и backtest assumptions.

### Phase 4 — Demo activation

Цель: пользователь может включить портфель в demo.

Backend:

1. `POST /portfolio-subscriptions` с `is_demo=true`.
2. Создать demo `subscriptions` по allocations.
3. Создать `user_portfolio_items`.
4. Реализовать cancel demo portfolio.
5. Реализовать conflict detection.

Frontend:

1. Экран demo activation.
2. Review generated subscriptions.
3. Portfolio subscription detail.
4. Отмена demo portfolio.

Тесты:

- demo activation создает правильное число subscriptions;
- cancel отключает только portfolio-owned subscriptions;
- manual subscriptions не меняются;
- повторная activation обрабатывается корректно.

Exit criteria:

- demo можно включить без платежа и без live риска.

### Phase 5 — Billing gate

Цель: live portfolio доступен только платным пользователям.

Backend:

1. Выбрать и подключить платежный провайдер.
2. Добавить webhook handler.
3. Хранить billing status.
4. Проверять оплату на live activation.
5. Блокировать rebalance при `past_due`/`canceled`.
6. Добавить admin override для beta.

Frontend:

1. Pricing screen.
2. Payment CTA.
3. Billing status.
4. Past due state.

Тесты:

- webhook signature;
- active payment allows live;
- past_due blocks live;
- canceled keeps history but blocks rebalance.

### Phase 6 — Live activation

Цель: платный пользователь включает live Balanced.

Backend:

1. Live `POST /portfolio-subscriptions`.
2. Проверка wallet/agent.
3. Проверка margin/risk через существующий `subscription_service`.
4. Создание обычных `subscriptions`.
5. Заполнение `source_type/source_id`.
6. Partial failure strategy.

Frontend:

1. Live activation flow.
2. Wallet readiness check.
3. Risk disclosure confirmation.
4. Success/failure summary.

Тесты:

- без wallet live блокируется;
- без оплаты live блокируется;
- insufficient margin блокируется;
- generated subscriptions имеют `source_type=model_portfolio`;
- manual conflict блокирует activation.

### Phase 7 — Rebalance engine

Цель: безопасно обновлять состав портфеля.

Backend:

1. Реализовать diff между версиями.
2. Реализовать preview endpoint.
3. Реализовать apply endpoint.
4. Реализовать scheduler job для auto-rebalance.
5. Добавить idempotency.
6. Добавить notifications.

Frontend:

1. Rebalance diff UI.
2. Auto-rebalance toggle.
3. Close removed positions preference.
4. Rebalance history.

Тесты:

- повторный apply безопасен;
- removed trader отключает только portfolio-owned subscription;
- manual subscription не затронута;
- past_due user пропускается;
- auto_rebalance=false показывает pending update.

### Phase 8 — AI explanations и отчеты

Цель: добавить понятные объяснения без black box.

Backend:

1. Шаблонные explanations.
2. Optional LLM provider.
3. Сохранение source facts.
4. Weekly report generation.

Frontend:

1. Reason per trader.
2. Weekly report.
3. Rebalance rationale.

Тесты:

- нет forbidden wording;
- explanation не ссылается на несуществующие факты;
- fallback работает.

### Phase 9 — Advanced optimization

Только после MVP.

Возможности:

- `scipy.optimize`;
- strategy clustering;
- anomaly detection;
- account-size-specific portfolios;
- better exposure heatmap;
- performance fee.

---

## 9. QA checklist

Backend unit:

- candidate filters;
- portfolio scoring;
- correlation;
- optimizer;
- backtest;
- rebalance diff;
- idempotency.

Backend API:

- portfolio list/detail;
- demo activation;
- live billing gate;
- cancel;
- preview rebalance;
- apply rebalance.

Frontend:

- portfolio list;
- detail page;
- activation flow;
- payment states;
- rebalance diff;
- mobile Telegram viewport;
- dark/light theme.

Production safety:

- internal dry-run;
- one internal live activation;
- cancel test;
- rebalance test;
- payment failure test;
- verify manual subscriptions untouched.

---

## 10. Observability

Логировать события:

- `portfolio_draft_built`;
- `portfolio_backtest_completed`;
- `portfolio_version_published`;
- `portfolio_activation_started`;
- `portfolio_activation_completed`;
- `portfolio_activation_failed`;
- `portfolio_rebalance_previewed`;
- `portfolio_rebalance_applied`;
- `portfolio_rebalance_failed`;
- `portfolio_payment_blocked`;
- `portfolio_manual_conflict_detected`.

Дашборды:

- active portfolio subscribers;
- trial to paid conversion;
- payment failures;
- activation failures;
- rebalance failures;
- average allocation size;
- portfolio drawdown;
- execution failure rate по portfolio-owned subscriptions.

Alerts:

- build draft не смог собрать minimum traders;
- backtest job падает;
- rebalance failure rate высокий;
- portfolio drawdown превысил порог;
- execution failures по одному трейдеру резко выросли.

---

## 11. Launch plan

### Internal alpha

Срок: 1-2 недели.

Что включено:

- Balanced draft;
- backtest;
- demo activation;
- live только на внутреннем пользователе.

Цель:

- проверить механику без публичного риска.

### Private beta

Аудитория: 20-50 пользователей.

Цена:

- бесплатно или `$9/month`.

Что включено:

- demo открыто;
- live ограниченно;
- manual approval на каждый rebalance.

Цель:

- проверить доверие, активацию, поддержку, отказы.

### Paid MVP

Цена:

- `$19/month`.

Что включено:

- Balanced only;
- live activation;
- weekly rebalance;
- paper track record;
- risk disclosures.

Цель:

- проверить willingness to pay.

### V2

Добавить:

- Conservative/Aggressive;
- `$39/month Pro`;
- auto-rebalance;
- AI weekly reports;
- более сильный optimizer.

---

## 12. MVP acceptance criteria

MVP готов, если:

- есть один активный `Balanced` model portfolio;
- published version immutable;
- версия содержит 6-10 трейдеров;
- веса = 100%;
- пользователь видит состав, веса, risk metrics и backtest assumptions;
- demo activation работает;
- live activation доступен только paid users;
- generated subscriptions являются обычными `subscriptions`;
- generated subscriptions помечены как portfolio-owned;
- cancel portfolio не трогает manual subscriptions;
- preview rebalance показывает diff;
- apply rebalance идемпотентен;
- live activation блокируется без wallet/agent/payment;
- risk disclosure показан перед live;
- есть тесты scoring/activation/rebalance.

---

## 13. Рекомендованные ближайшие действия

1. Утвердить MVP: Balanced only, deterministic builder, demo first, `$19/month`.
2. Выбрать payment provider.
3. Утвердить manual conflict policy.
4. Сделать DB models/migrations.
5. Реализовать draft builder.
6. Добавить read-only portfolio API/UI.
7. Запустить demo activation.
8. Накопить paper track record.
9. Только после этого включать paid live activation.

---

## 14. Деплой на сервер

Правило: не вносить production-правки напрямую на сервере. Все изменения проходят обычный release path: локальная реализация, тесты, commit, push, затем pull/deploy на сервере.

### Phase 1 deployment checklist

1. До деплоя убедиться, что production backup БД создан штатным способом.
2. Задеплоить backend-код с новой Alembic migration.
3. Выполнить миграцию на сервере:

```bash
cd backend
uv run alembic upgrade head
```

В Docker/prod окружении использовать эквивалентную команду внутри backend-контейнера или существующий `make deploy`, если он выполняет `alembic upgrade head`.

4. Засидить шаблон Balanced, если он еще не создан:

```bash
cd backend
uv run python -m scripts.seed_model_portfolios
```

5. Проверить после миграции:

```bash
cd backend
uv run alembic current
uv run python -m scripts.seed_model_portfolios --check
```

6. Перезапустить backend/scheduler только после успешной миграции и seed-проверки.
7. Rollback strategy: если миграция уже применена на production, не редактировать примененный revision; выпускать новую forward migration. `downgrade` использовать только на локальном/staging окружении до production rollout.

Миграции, которые нужно выполнить на сервере после Phase 1:

- `o1p2q3r4s5t6_add_model_portfolio_tables.py` — создает таблицы model portfolio, backtest/rebalance history и добавляет source-поля в `subscriptions`.

### Phase 2 deployment checklist

Phase 2 не добавляет новых миграций. Перед запуском builder на сервере должна быть применена Phase 1 migration и должен существовать seed-шаблон `Balanced`.

1. Задеплоить backend-код с portfolio builder.
2. Проверить, что Phase 1 migration применена:

```bash
cd backend
uv run alembic current
```

3. Проверить seed `Balanced`:

```bash
cd backend
uv run python -m scripts.seed_model_portfolios --check
```

4. Создать draft-версию Balanced из текущих реальных метрик:

```bash
cd backend
uv run python -m scripts.build_model_portfolio_draft --portfolio-slug balanced --period allTime
```

5. Проверить результат в БД: в `model_portfolio_versions` появилась новая запись `status='draft'`, в `model_portfolio_allocations` есть 6-10 allocation rows, сумма `target_weight_pct` равна `100.000`.
6. Не публиковать draft автоматически. Публикация остается отдельным manual approval шагом будущей Phase 3/админ-флоу.

Миграции, которые нужно выполнить на сервере после Phase 2:

- новых миграций нет;
- обязательное предварительное условие: Phase 1 migration `o1p2q3r4s5t6_add_model_portfolio_tables.py` уже применена.

---

## 15. Источники и ориентиры

Перед публичным запуском эти ссылки нужно перепроверить, потому что правила платформ и регуляторика меняются.

- eToro CopyTrader: https://www.etoro.com/copytrader/
- eToro fees: https://www.etoro.com/trading/fees/
- Hyperliquid vault docs: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults
- SEC/Investor.gov investment adviser definition: https://www.investor.gov/introduction-investing/investing-basics/glossary/investment-adviser
- NFA CTA registration overview: https://www.nfa.futures.org/registration-membership/who-has-to-register/cta.html
- ESMA copy trading guidance: https://www.esma.europa.eu/press-news/esma-news/esma-provides-guidance-supervision-copy-trading-services
