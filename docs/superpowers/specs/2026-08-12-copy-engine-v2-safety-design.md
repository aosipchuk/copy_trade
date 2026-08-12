# Copy Engine v2: безопасное target-state копирование

Дата: 2026-08-12
Статус: согласовано для подготовки плана реализации

## 1. Контекст

Текущий live-движок нельзя безопасно масштабировать на реальные аккаунты из-за
четырёх системных ошибок:

1. ROI Hyperliquid приходит как отношение (`0.05 == 5%`), но хранится и
   отображается как уже готовый процент.
2. `UPDATE` позиции лидера интерпретируется как новый ордер на полный текущий
   размер, поэтому увеличение, уменьшение и повторное наблюдение могут накапливать
   позицию подписчика.
3. `max_allocation_usd` и `max_per_coin_usd` ограничивают отдельный ордер, а не
   итоговую позицию и совокупную экспозицию подписки.
4. Статический список монет исключает HIP-3 и не учитывает DEX, метаданные,
   точность размера, доступное плечо и состояние рынка.

Исправление этих ошибок требует не локальных проверок вокруг отправки ордера, а
перехода к движку желаемого состояния позиции. В рамках миграции все существующие
live-подписки приостанавливаются. Возобновление возможно только вручную после
проверки позиций и ордеров.

## 2. Цели и границы

### Цели

- Каждая подписка хранит желаемую позицию, а движок исполняет только разницу
  между совокупной целью и подтверждённой фактической позицией аккаунта.
- Уменьшение позиции лидера уменьшает, а не увеличивает позицию подписчика.
- Лимиты применяются к итоговым позициям и всему портфелю подписки.
- Поддерживаются default DEX и все активные HIP-3 DEX, доступные через официальные
  метаданные Hyperliquid.
- Повторы задач, перезапуск worker, частичное исполнение и задержки API не создают
  повторных ордеров.
- Ручная торговля и любое не объяснённое движком изменение выделенного аккаунта
  приводят к блокировке live-исполнения.
- Миграция не открывает и не закрывает позиции автоматически и не возобновляет
  старые подписки без действия пользователя.

### Не цели

- Движок не обещает прибыль за неделю, месяц или любой другой период.
- Portfolio Margin pre-alpha и устаревший DEX abstraction mode не поддерживаются.
- Один торговый аккаунт нельзя одновременно использовать для CopyTrade и ручной
  торговли.
- Первая уже открытая позиция лидера при создании или возобновлении подписки не
  догоняется.
- Очистка старого ROI-столбца не входит в первый релиз и выполняется отдельной
  миграцией после периода стабильной работы.

## 3. Инварианты безопасности

1. **Выделенный аккаунт.** Live CopyTrade работает только на отдельном
   Hyperliquid account/subaccount. Пользователь подтверждает это правило, а движок
   непрерывно проверяет отсутствие внешних ордеров, fills и изменений позиции.
2. **Target-state.** Сигнал задаёт целевую подписочную позицию, а не команду
   «купить ещё». Повтор одного и того же состояния не создаёт ордер.
3. **Один сериализованный writer.** Планирование и отправка по ключу
   `(user_id, dex, coin)` выполняются под распределённой/DB-блокировкой.
4. **Intent до exchange call.** Запись ордерного намерения и уникальный `cloid`
   создаются до вызова Hyperliquid.
5. **Fill-confirmed state.** Фактическая позиция меняется только из данных
   Hyperliquid; успешный HTTP-ответ не считается исполнением.
6. **Fail closed.** Неизвестный fill, неизвестный открытый ордер, необъяснимое
   изменение позиции, неоднозначная сторона или устаревшие критичные данные
   блокируют новые live-ордера.
7. **Нет неявного netting конфликтов.** Цели разных подписок по одному рынку могут
   суммироваться только при одинаковом направлении. Одновременный long и short
   по одному `(user, dex, coin)` блокируется и требует решения пользователя.
8. **Закрытие только своей доли.** Закрытие/удаление одной подписки обнуляет её
   target; оно не закрывает всю фактическую позицию пользователя по монете.
9. **DEX является частью идентичности рынка.** Ключ рынка — `(dex, coin)`, а не
   только тикер.
10. **Live включается явно.** Новый движок не отправляет mainnet-ордера, пока
    `COPY_ENGINE_V2_LIVE_ENABLED` не включён после серверных проверок.

## 4. Компоненты

### 4.1 Market Registry

`MarketRegistry` заменяет `COIN_WHITELIST` как допуск к исполнению. Он:

- получает список DEX и метаданные через `perpDexs`, `allPerpMetas`, `meta` и
  `metaAndAssetCtxs`;
- хранит `dex`, canonical coin name, asset id, `szDecimals`, максимальное плечо,
  collateral token, торговый статус и время обновления;
- вычисляет HIP-3 asset id по официальному правилу
  `100000 + perp_dex_index * 10000 + index_in_meta`;
- использует prefixed canonical name для HIP-3 и не смешивает одинаковые тикеры
  разных DEX;
- предоставляет DEX-specific mids, позиции, open orders и margin context;
- обновляет запись при cache miss или просрочке до расчёта/отправки ордера.

Рынок допускается только если metadata и цена свежие, perpetual активен, рынок не
остановлен и не delisted, размер можно округлить по `szDecimals`, а требуемое плечо
не превышает market/account limit. `allowed_coins` остаётся пользовательским
ограничением поверх registry, но статического глобального whitelist больше нет.

Поддерживаются Standard и Unified account modes. Для legacy DEX abstraction и
Portfolio Margin pre-alpha preflight возвращает блокирующую ошибку.

### 4.2 Leader Market Discovery

Полный опрос каждого пользователя по каждому HIP-3 DEX не масштабируется. Поэтому:

- default DEX и уже известные для лидера DEX опрашиваются каждые 5 секунд;
- глобальный registry периодически обновляет список DEX;
- новые рынки лидера обнаруживаются периодическим чтением fills и затем
  добавляются в его polling set;
- первая позиция на вновь обнаруженном DEX записывается как baseline-only и не
  догоняется;
- закрытие baseline-позиции снимает baseline, а следующая новая позиция уже
  копируется.

Для ручного preflight подписчика допустим полный последовательный обход всех DEX
registry, потому что он выполняется редко и должен доказать отсутствие позиций и
open orders на выделенном аккаунте.

### 4.3 Signal Detector v2

Событие содержит:

- `dex` и `coin`;
- `previous_size` — последнее принятое signed-состояние лидера;
- `target_size` — новое signed-состояние лидера;
- `delta_size = target_size - previous_size`;
- snapshot/version и детерминированный dedupe key;
- `engine_version = 2`.

Положительный size означает long, отрицательный — short. Detector сравнивает
снимок не с предыдущим poll, а с последним принятым target/baseline, чтобы серия
малых изменений не терялась.

- Закрытие, смена стороны и любое уменьшение, которое после округления торгово
  значимо, принимаются без процентного порога.
- Для увеличения применяется текущий `min_position_change_pct`; накопленная
  разница считается от последнего принятого target.
- Остаток меньше lot/min-notional сохраняется и будет исполнен после накопления.
- Пропавший, невалидный или просроченный snapshot не трактуется как нулевая
  позиция. Для CLOSE нужен валидный снимок с нулевым размером.
- События v1 не могут попасть в v2 executor.

### 4.4 Target Calculator

Для каждой активной подписки и рынка сначала рассчитывается signed target:

- `fixed_ratio`: `leader_signed_size * copy_ratio`;
- `fixed_usd`: фиксированный target-notional на каждый активный рынок со стороной
  лидера; повторный `UPDATE` меняет target, но не добавляет тот же notional;
- `equity_pct`: заданная доля актуального equity выделенного аккаунта со стороной
  лидера.

Затем применяются ограничения к конечному состоянию:

1. `max_per_coin_usd` ограничивает абсолютный notional target данной подписки по
   одному `(dex, coin)`.
2. `max_allocation_usd` ограничивает сумму абсолютных notionals всех targets
   подписки. Если сумма превышена, targets пропорционально масштабируются.
3. Плечо и доступная маржа проверяются для совокупного account target, а не для
   единичного ордера.
4. Размер округляется вниз по market precision; округление не может увеличить
   риск сверх лимита.

Пересчёт equity, цены, лимита или одного target может изменить несколько targets.
Все изменённые версии сохраняются атомарно до постановки reconciliation jobs.

### 4.5 Account Reconciler

Для `(user, dex, coin)` reconciler под блокировкой:

1. проверяет user execution state, feature flag, account mode и свежесть registry;
2. читает targets всех live-подписок;
3. отклоняет противоположные направления;
4. суммирует targets одинаковой стороны;
5. получает фактическую позицию, open orders и свежие fills;
6. сопоставляет все изменения с известными intents;
7. учитывает активный partial/pending intent;
8. рассчитывает остаточный signed delta до aggregate target;
9. создаёт intent и `cloid`, затем отправляет не более одного нового ордера;
10. повторяет reconciliation после fill/cancel/timeout.

Поведение по направлению delta:

- увеличение существующей стороны — обычный IOC/marketable-limit ордер;
- уменьшение без перехода через ноль — `reduceOnly`;
- полное закрытие — `reduceOnly` до нуля;
- flip — сначала отдельное `reduceOnly` закрытие, ожидание подтверждённого нуля,
  затем новый intent на противоположную сторону.

Partial fill не приводит к повтору полного ордера: следующий intent создаётся
только на подтверждённый остаток. Retry существующего intent использует тот же
`cloid`; новый `cloid` появляется только после терминального состояния предыдущего
intent и новой версии остатка.

### 4.6 Drift Detector и блокировка аккаунта

Движок хранит последнее подтверждённое account state. Изменение actual size
считается объяснённым только известными fills по v2 intents. Также каждый open
order должен принадлежать известному `cloid`.

Следующие события переводят весь выделенный аккаунт и все его live-подписки в
`blocked`:

- неизвестный fill или open order;
- изменение позиции без известного fill;
- несовместимые long/short targets разных подписок;
- неподдерживаемый account mode;
- невозможность однозначно сопоставить order/fill после timeout;
- повреждённые либо противоречивые target/execution records.

Обычная временная ошибка сети не признаётся drift: отправка новых ордеров
останавливается, intent остаётся `unknown/pending`, а reconciler сначала выясняет
его состояние. Автоматического «исправляющего» ордера при drift нет.

Разблокировка требует ручного preflight. Аккаунт должен быть flat, без open orders
и неизвестных pending intents. После этого targets снова создаются только из
новых позиций лидеров; существующие позиции лидеров остаются baseline-only.

## 5. Модель данных

Названия могут быть адаптированы к существующим ORM conventions, но семантика и
уникальные ограничения обязательны.

### 5.1 Изменения подписки

В `subscriptions` добавляются:

- `engine_version SMALLINT NOT NULL DEFAULT 1`;
- `execution_status`: `active | paused | blocked | stopping | stopped`;
- `pause_reason` — стабильный machine-readable code;
- `execution_status_details JSONB` — безопасные диагностические данные;
- `resumed_at`, `blocked_at`.

`is_active` сохраняет бизнес-жизненный цикл и совместимость. Исполнение разрешено
только при `is_active = true`, `engine_version = 2`,
`execution_status = active`, активном user execution state и включённом feature
flag.

### 5.2 User execution state

Новая таблица `copy_account_execution_states`:

- уникальный `user_id`/торговый account;
- `status: active | paused | blocked`;
- `reason`, `details`, `detected_at`, `cleared_at`;
- последняя успешная reconciliation и account mode;
- версия состояния для optimistic locking.

User-level block имеет приоритет над статусом отдельной подписки.

### 5.3 Subscription targets

Новая `copy_position_targets` с уникальностью
`(subscription_id, dex, coin)`:

- leader signed size и рассчитанный follower signed target;
- target notional, price snapshot и sizing mode;
- `state: baseline_only | active | blocked | zero | stopping`;
- source signal/snapshot id и монотонная target version;
- timestamps и причины последнего пересчёта.

Baseline-only всегда имеет follower target `0` до подтверждённого закрытия позиции
лидера.

### 5.4 Account position state

Новая `copy_account_positions` с уникальностью `(user_id, dex, coin)`:

- aggregate target size/version;
- last confirmed actual size;
- pending explained delta;
- reconciliation status/reason/timestamps;
- hash/version использованного набора subscription targets.

### 5.5 Execution intents и распределение fills

Новая `copy_execution_orders`:

- UUID, уникальный deterministic `cloid`, user/account, dex, coin;
- aggregate target version, before/target size, requested signed delta;
- order side, `reduce_only`, rounded size, reference/limit price;
- exchange oid;
- `pending | submitted | partial | filled | cancelled | failed | unknown`;
- filled size, average price, error code и timestamps;
- уникальный idempotency key на одну попытку исполнения target version.

Новая `copy_execution_allocations` связывает intent с targets подписок и хранит
запрошенную/исполненную signed-долю. Она нужна для корректной истории, PnL и
удаления подписки при одном агрегированном exchange order.

Существующий `UserTrade` остаётся пользовательской проекцией истории и получает
ссылку на v2 intent/allocation. Он больше не является источником истины для
позиции.

## 6. Жизненный цикл подписки

### Создание новой v2-подписки

1. При выключенном live feature flag API может создать demo, но не активировать
   mainnet live-подписку.
2. Выполняется account preflight.
3. Текущие ненулевые позиции лидера фиксируются как baseline-only.
4. Подписка становится live только после успешного preflight и явного
   подтверждения пользователя.
5. Копируются только позиции, открытые после выхода соответствующего baseline в
   ноль.

### Остановка или удаление

Остановка, stop-loss, take-profit либо удаление сначала переводит подписку в
`stopping` и атомарно обнуляет только её targets. Reconciler уменьшает aggregate
account target на её долю. `stopped`/окончательное удаление разрешено после
подтверждения fills либо после явной account block с доступной диагностикой.

### Portfolio и new-wallet

Parent и дочерние элементы используют те же targets и reconciler. Возобновление
portfolio выполняется атомарно: если хотя бы один обязательный preflight не
пройден, ни один child не активируется. Background tasks не имеют права
самостоятельно менять `paused/blocked` на `active`.

## 7. Миграция существующего live

Миграция выполняется во время остановленного live worker/очереди, чтобы старый
executor не мог отправить ордер параллельно.

В одной транзакции:

- все существующие live `manual`, `model_portfolio` и `new_wallet` подписки
  получают `is_active = false`, `execution_status = paused`,
  `pause_reason = engine_v2_reconciliation_required`, `engine_version = 1`;
- соответствующие parent и активные child/items также помечаются paused;
- demo-история сохраняется, а новые demo-запуски используют target-state core;
- v2 target/account-position таблицы создаются пустыми;
- существующие фактические позиции не распределяются задним числом между
  подписками.

Очередь:

- queued v1 signals помечаются skipped и никогда не передаются v2 executor;
- уже отправленные legacy pending orders только отслеживаются до терминального
  статуса;
- если legacy order остаётся неоднозначным, preflight не разрешает resume.

### Ручной resume

Preflight проверяет весь выделенный account по всем DEX registry:

- agent wallet активен и имеет нужные права;
- нет фактических позиций;
- нет open orders;
- нет legacy/v2 pending или unknown intents;
- account mode поддерживается;
- market metadata и цены доступны;
- выбранные trader/subscription/portfolio items существуют и разрешены.

Если account не flat, UI показывает `dex`, `coin`, side и size. Движок не закрывает
их автоматически: пользователь вручную приводит выделенный account в ноль и
повторяет проверку.

Успешный resume атомарно устанавливает `engine_version = 2`, `is_active = true`,
`execution_status = active`. Все текущие позиции лидера становятся baseline-only.
Их UPDATE не копируются до полного закрытия; следующая новая позиция копируется.
То же правило применяется к впервые обнаруженному HIP-3 DEX.

Автоматического массового resume после deploy нет.

## 8. ROI: единицы и rollback-safe cutover

Каноническая единица во всём приложении — процент:

- `5.0` означает `5%`;
- на границе Hyperliquid `raw_roi_ratio * 100 = roi_percent`;
- scoring, фильтры, сортировка, backtest, API, UI и экспорт работают с процентом.

Чтобы rollback старого приложения не начал читать проценты как ratio, первый
релиз не меняет смысл существующего `roi_pct` in-place:

1. Добавляется nullable `roi_percent NUMERIC(20, 8)`, чтобы не наследовать узкий
   диапазон legacy-столбца после умножения на 100.
2. Backfill выполняет `roi_percent = roi_pct * 100` для legacy rows и проверяет
   non-finite/invalid данные.
3. Новый код читает `roi_percent` как источник истины.
4. Ingest временно dual-write: legacy `roi_pct` получает raw ratio, новый
   `roi_percent` — канонический процент.
5. Внешнее API для совместимости может сохранить имя поля `roi_pct`, но значение
   в нём после cutover имеет документированную процентную семантику.
6. Frontend не умножает значение повторно и только добавляет `%`.
7. После наблюдаемого стабильного периода отдельный релиз удаляет/переименовывает
   legacy-столбец и прекращает dual-write.

Миграция и backfill идемпотентны. До cleanup rollback приложения безопасен, потому
что старый код продолжает видеть raw ratio в старом столбце.

## 9. API и интерфейс

Минимальные новые операции:

- preflight конкретной подписки/portfolio;
- явный resume после успешного preflight;
- чтение subscription и user execution status;
- чтение последней reconciliation, блокирующей причины и фактических позиций;
- ручная повторная проверка/разблокировка только при flat account.

UI показывает:

- `active`, `paused`, `blocked`, `stopping`, `stopped` отдельно от общей активности;
- причину миграционной паузы и требование выделенного аккаунта;
- результат preflight по каждому условию;
- все позиции/open orders, мешающие resume;
- последний успешный reconcile и понятную причину fail-closed block;
- предупреждение, что текущие позиции лидера baseline-only и не будут догоняться.

Кнопка resume недоступна до успешного серверного preflight. Клиент не может
обойти проверку подстановкой статуса.

## 10. Ошибки и восстановление

- Временный timeout: не создавать новый intent, пока exchange state предыдущего
  не выяснен.
- Stale price/metadata: заблокировать только новый риск по рынку; если безопасное
  reduce-only закрытие невозможно подтвердить, перевести аккаунт в blocked и
  уведомить пользователя.
- Halted/delisted market: не открывать и не увеличивать; сохранить target и
  диагностический статус. Закрывать только если exchange явно допускает
  reduce-only и параметры известны.
- Недостаточная маржа или отклонение ордера: intent получает terminal error,
  подписка/рынок не зацикливаются на агрессивных retry; нужен backoff и видимая
  причина.
- Crash между intent и API call: после рестарта reconciler проверяет `cloid`/oid и
  только затем решает, отправлять ли сохранённый intent.
- Crash после fill: fill ingestion обновляет intent и actual; повторный reconcile
  видит только остаток.
- Emergency close-all является отдельным явным действием владельца: оно закрывает
  account по всем DEX, затем блокирует/ставит подписки на паузу и требует нового
  preflight. Обычная остановка подписки не использует close-all.

## 11. Наблюдаемость и аудит

Структурированные события и метрики должны включать:

- signal accepted/skipped с version и причиной;
- target created/changed/baseline/zero;
- aggregate target и actual до/после reconcile;
- intent created/submitted/partial/terminal/unknown;
- idempotency dedupe hits;
- drift и account block reason;
- preflight result и ручной resume actor/time;
- registry refresh/cache age и неизвестные рынки;
- ROI source ratio и converted percent без скрытого повторного умножения.

Ключи корреляции: `user_id`, `subscription_id`, `(dex, coin)`, signal id, target
version, intent id, `cloid`, exchange oid. Секреты, private keys и полные подписи в
логах запрещены.

Обязательные алерты:

- любой unknown intent или external drift;
- рост failed/rejected orders;
- reconciliation lag выше порога;
- registry/price staleness;
- v1 signal, дошедший до v2 execution boundary;
- расхождение aggregate target/actual без pending explained delta.

## 12. Проверка

По правилам проекта все tests, lint, mypy, frontend build и интеграционные проверки
выполняются только на сервере. Реальные подписи и exchange orders проверяются на
Hyperliquid testnet, не на mainnet.

### ROI

- ingest `0.05` записывает legacy ratio `0.05` и canonical percent `5.0`;
- backfill, повторный backfill и rollback-read старого столбца;
- API display/filter/sort, scoring boundaries, backtest и exports;
- отсутствие двойного умножения frontend;
- отрицательные, нулевые и экстремальные валидные ROI.

### Signals и targets

- open, increase, decrease, close, flip long/short;
- несколько малых increase относительно последнего принятого target;
- reduction без процентного порога;
- invalid/missing snapshot не создаёт CLOSE;
- `fixed_ratio`, `fixed_usd`, `equity_pct`;
- per-coin cap, aggregate cap, proportional scaling, precision/min-notional;
- повторный UPDATE не накапливает позицию.

### Reconciliation

- одинаковый target дважды не создаёт ордер;
- crash/retry до и после exchange call;
- partial fill, cancel, reject, timeout/unknown;
- уменьшение только reduce-only;
- flip двумя фазами через подтверждённый ноль;
- несколько same-direction subscriptions;
- opposing targets вызывают block;
- остановка одной подписки закрывает только её aggregate-долю;
- unknown fill/order/position delta блокирует весь account;
- delete, stop-loss и emergency close-all;
- конкурентные workers сериализуются по `(user, dex, coin)`.

### HIP-3

- registry refresh и canonical market identity;
- asset-id formula и `szDecimals` rounding;
- DEX-specific mids, positions, open orders и margin;
- default DEX плюс несколько HIP-3 DEX;
- halted/delisted/stale/unknown market;
- Standard и Unified account modes;
- блокировка legacy abstraction и Portfolio Margin pre-alpha;
- discovery через fills и baseline первого обнаружения.

### Миграция и resume

- все типы live-подписок и parent/items становятся paused/inactive;
- queued v1 signals skipped;
- demo history сохранена;
- resume не проходит при позиции, open order, pending intent или unsupported mode;
- portfolio resume атомарен;
- успешный resume не копирует существующую позицию лидера;
- background jobs не могут снять pause/block;
- при выключенном feature flag mainnet execution невозможен.

## 13. Rollout и rollback

1. Локально реализовать изменения, review, commit и push.
2. На сервере остановить live workers/consumers и исключить исполнение v1 во время
   миграции.
3. Pull/deploy обычным release path; применить additive migration, которая ставит
   старые live-подписки на паузу.
4. Запустить приложение с `COPY_ENGINE_V2_LIVE_ENABLED=false`.
5. Выполнить серверные unit/API tests, lint, strict mypy и frontend build.
6. Прогнать migration/backfill проверки на копии/контролируемых данных.
7. Выполнить testnet smoke: open, increase, decrease, partial/timeout recovery,
   close, flip, HIP-3 и drift block.
8. Проверить метрики и аудит, затем явно включить v2 live feature flag.
9. Пользователи вручную запускают preflight и resume; массовой активации нет.

Rollback до cleanup:

- выключить feature flag и остановить v2 execution;
- перевести все v2 live-подписки в paused до возврата старой версии;
- не запускать старый executor для v2/resumed подписок;
- старое приложение продолжает читать legacy raw-ratio `roi_pct`;
- additive v2 tables/columns остаются до отдельного безопасного cleanup.

Rollback не должен автоматически возвращать пользователей на небезопасный v1.

## 14. Критерии приёмки

Работа считается готовой только если одновременно выполнено следующее:

- `UPDATE` никогда не исполняется как повтор полного текущего размера;
- конечная позиция сходится к aggregate target после повторов и partial fills;
- лимиты доказуемо применяются к target positions и общей экспозиции;
- static whitelist не участвует в разрешении исполнения, HIP-3 проходит через
  динамический registry;
- ROI `0.05` от Hyperliquid в API/UI/scoring означает ровно `5%`, а rollback до
  cleanup сохраняет старую семантику БД;
- неизвестная активность на выделенном аккаунте не вызывает автокоррекцию и
  блокирует все live-подписки пользователя;
- миграция ставит все существующие live-подписки и связанные parent/items на
  паузу;
- ни одна старая подписка не возобновляется автоматически;
- resume возможен только вручную после flat-account preflight;
- существующие позиции лидера после resume остаются baseline-only;
- все обязательные серверные проверки и testnet сценарии проходят;
- mainnet v2 остаётся выключенным до явного решения о rollout.

## 15. Официальные источники Hyperliquid

- [Info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [HIP-3 permissionless perpetuals](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/hip-3)
- [Account abstraction modes](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/account-abstraction-modes)
