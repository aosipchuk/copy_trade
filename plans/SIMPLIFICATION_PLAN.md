# Simplification Plan: 8 containers → 5 containers

**Goal**: Remove ClickHouse и Celery, сохранить весь функционал, упростить деплой.  
**Result**: `docker compose build backend && docker compose up -d --no-deps backend` — один рестарт вместо трёх.

---

## Что меняется

| Было | Станет |
|------|--------|
| PostgreSQL | PostgreSQL (без изменений) |
| Redis (broker + cache) | Redis (только cache + WS snapshots) |
| **ClickHouse** | **Удалён** |
| FastAPI backend | FastAPI backend + in-process scheduler |
| **celery-worker** | **Удалён** |
| **celery-beat** | **Удалён** |
| Frontend | Frontend (без изменений) |
| Nginx | Nginx (без изменений) |

**RAM на VPS**: ~2.25 GB → ~1.2 GB  
**Сервисов в docker-compose**: 8 → 5  
**Зависимостей**: −2 (`celery[redis]`, `clickhouse-driver`), +1 (`apscheduler`)

---

## Почему безопасно

### ClickHouse
ClickHouse читается только в двух местах:

1. **`_ch_open_positions(address)`** — "текущие позиции из снэпшота за 5 мин"  
   → Эти данные **уже лежат в Redis** как `hl:snapshot:{address}` (туда пишет `_poll_trader_positions_async`).  
   → Замена: читать из Redis напрямую; фолбэк — запрос к HL API.

2. **`_ch_avg_leverage(address)`** — средний леверейдж из истории снэпшотов  
   → `Position.leverage.value` есть в Redis-снэпшоте (поле `leverage: PositionLeverage`).  
   → Замена: среднее по текущим позициям из Redis. Не исторический avg, но достаточно точная аппроксимация.

Данные **пишутся** в ClickHouse в двух местах, но они нигде не читаются через API:
- `_write_positions_to_clickhouse()` в `hl_tracker.py` — дублирует Redis-снэпшот, удаляем.
- PnL history writes в `_refresh_leaderboard_async()` — данные уже есть в `TraderStat` PG, удаляем.

### Celery
Каждый Celery-таск уже написан как `asyncio.run(_async_function(...))`.  
Async-логика полностью изолирована в `_*_async()` функциях.  
→ Достаточно убрать Celery-декораторы и вызывать `_async_function()` напрямую.

**APScheduler** (`AsyncIOScheduler`) запускается внутри FastAPI `lifespan()` и вызывает те же async функции, никакого брокера не нужно.

### Fire-and-forget вызовы из API
`close_all_positions_for_user.delay()` (wallet.py:245) и `close_subscription_positions.delay()` (subscription_service.py:248) заменяются на `asyncio.create_task(...)`.  
Это работает потому что вызываются из async FastAPI хендлеров.

---

## Phase 1 — Удалить ClickHouse

### Шаг 1.1 — Заменить `get_open_positions()` (metrics.py)

**Файл**: `backend/app/services/analytics/metrics.py`

Удалить функции `_ch_open_positions()` и заменить `get_open_positions()`:

```python
# БЫЛО (читает из ClickHouse):
def _ch_open_positions(address: str) -> list[...]:
    ch = get_ch_client()
    rows = ch.execute("SELECT ... FROM copytrade.trader_positions WHERE ...")
    ...

async def get_open_positions(address: str) -> list[PositionItem]:
    rows = await asyncio.to_thread(_ch_open_positions, address)
    ...

# СТАНЕТ (читает из Redis-снэпшота):
async def get_open_positions(address: str) -> list[PositionItem]:
    """Latest positions from Redis snapshot (updated every 5s by hl_tracker)."""
    from pydantic import TypeAdapter
    from app.core.redis_client import get_redis_client
    from app.services.hyperliquid.models import Position

    adapter: TypeAdapter[list[Position]] = TypeAdapter(list[Position])
    r = get_redis_client()
    raw: str | None = await asyncio.to_thread(r.get, f"hl:snapshot:{address}")

    if raw:
        positions = adapter.validate_json(raw)
    else:
        # Fallback: fetch directly from HL (trader not tracked or snapshot expired)
        client = HyperliquidInfoClient()
        positions = await client.get_positions(address)

    return [
        PositionItem(
            coin=p.coin,
            side=p.side,
            size=float(p.abs_size),
            entry_px=float(p.entry_px) if p.entry_px is not None else None,
            unrealized_pnl=float(p.unrealized_pnl),
            leverage=p.leverage.value,
        )
        for p in positions
        if p.szi != Decimal("0")
    ]
```

### Шаг 1.2 — Заменить `_ch_avg_leverage()` (metrics.py)

**Файл**: `backend/app/services/analytics/metrics.py`

```python
# БЫЛО (читает из ClickHouse):
def _ch_avg_leverage(address: str) -> float | None:
    ch = get_ch_client()
    rows = ch.execute("SELECT avg(leverage) FROM copytrade.trader_positions WHERE ...")
    ...

# СТАНЕТ (читает из Redis-снэпшота):
def _redis_avg_leverage(address: str) -> float | None:
    """Average leverage from current Redis position snapshot."""
    from pydantic import TypeAdapter
    from app.core.redis_client import get_redis_client
    from app.services.hyperliquid.models import Position

    adapter: TypeAdapter[list[Position]] = TypeAdapter(list[Position])
    r = get_redis_client()
    raw: str | None = r.get(f"hl:snapshot:{address}")
    if not raw:
        return None
    positions = adapter.validate_json(raw)
    leverages = [p.leverage.value for p in positions if p.szi != Decimal("0") and p.leverage.value > 0]
    return sum(leverages) / len(leverages) if leverages else None
```

В `compute_trader_quality_metrics()` заменить строку:
```python
# БЫЛО:
all_fills, avg_leverage = await asyncio.gather(
    client.get_fills(address, limit=None),
    asyncio.to_thread(_ch_avg_leverage, address),
)

# СТАНЕТ:
all_fills, avg_leverage = await asyncio.gather(
    client.get_fills(address, limit=None),
    asyncio.to_thread(_redis_avg_leverage, address),
)
```

Удалить импорт `from app.core.clickhouse_client import get_ch_client` из `metrics.py`.

### Шаг 1.3 — Удалить ClickHouse-записи в hl_tracker.py

**Файл**: `backend/app/tasks/hl_tracker.py`

Удалить функцию `_write_positions_to_clickhouse()` (строки ~43–63).

В `_poll_trader_positions_async()` удалить блок:
```python
# УДАЛИТЬ ЭТОТ БЛОК:
if curr_positions:
    try:
        _write_positions_to_clickhouse(trader_address, curr_positions)
    except Exception as ch_err:
        logger.warning("ch_positions_write_failed", ...)
```

### Шаг 1.4 — Удалить PnL-историю в hl_tracker.py

**Файл**: `backend/app/tasks/hl_tracker.py`

В `_refresh_leaderboard_async()` удалить блок записи в ClickHouse внутри цикла по периодам:
```python
# УДАЛИТЬ ЭТОТ БЛОК:
try:
    ch.execute(
        "INSERT INTO copytrade.trader_pnl ...",
        [(row.eth_address, now, float(perf.pnl), float(perf.roi), period)],
    )
except Exception as ch_err:
    logger.warning("ch_pnl_write_failed", ...)
```

Удалить строку `ch = get_ch_client()` и импорт `from app.core.clickhouse_client import get_ch_client`.

### Шаг 1.5 — Удалить `clickhouse_client.py` и обновить `config.py`

**Удалить файл**: `backend/app/core/clickhouse_client.py`

**Файл**: `backend/app/core/config.py`  
Удалить поля:
```python
# УДАЛИТЬ:
clickhouse_host: str = "localhost"
clickhouse_port: int = 9000
clickhouse_db: str = "copytrade"
clickhouse_user: str = "default"
clickhouse_password: str = ""
```

### Шаг 1.6 — Удалить зависимость clickhouse-driver

**Файл**: `backend/pyproject.toml`
```toml
# УДАЛИТЬ строку:
"clickhouse-driver>=0.2",
```

### Шаг 1.7 — Удалить ClickHouse из docker-compose

**Файл**: `docker-compose.yml` — удалить весь сервис `clickhouse:` (включая `- clickhouse_data:` в volumes).  
Удалить переменную среды `CLICKHOUSE_HOST: clickhouse` из сервисов backend, celery-worker, celery-beat.

**Файл**: `docker-compose.prod.yml` — удалить секцию `clickhouse:`.  
Удалить переменные `CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD}` из backend и celery-worker.

**Файл**: `.env.example` — удалить строки с `CLICKHOUSE_*`.  
**Файл**: `.env.prod.example` — удалить строки с `CLICKHOUSE_*`.

### Шаг 1.8 — Удалить infra ClickHouse

**Удалить директорию**: `backend/infra/clickhouse/` (содержит только `init.sql`).

### Проверка после Phase 1

```bash
make down && make up
# Убедиться что backend запустился, нет ошибок clickhouse
curl http://localhost:8001/health
# Проверить /api/traders/{id}/positions — должен вернуть данные
# Проверить /api/traders (список трейдеров)
cd backend && uv run pytest tests/ -v
```

### ✅ Phase 1 выполнена (2026-06-25)

**Миграции БД не требуются** — Phase 1 не меняет PostgreSQL-схему, только удаляет ClickHouse.

**На сервере при деплое Phase 1:**
1. `make down` — остановить все сервисы (включая ClickHouse)
2. Убедиться что `clickhouse_data` volume можно удалить: `docker volume rm <project>_clickhouse_data`
3. `make up` — поднять обновлённый stack (без ClickHouse)
4. Никаких `alembic upgrade head` не нужно.

---

## Phase 2 — Заменить Celery на APScheduler

### ✅ Phase 2 + Phase 3 выполнены (2026-06-25)

### Шаг 2.1 — Обновить зависимости

**Файл**: `backend/pyproject.toml`
```toml
# УДАЛИТЬ:
"celery[redis]>=5.4",

# ДОБАВИТЬ:
"apscheduler>=3.10",
```

`redis[hiredis]` остаётся — Redis нужен для кэширования и WS-снэпшотов.

### Шаг 2.2 — Удалить Celery из config.py

**Файл**: `backend/app/core/config.py`
```python
# УДАЛИТЬ:
celery_broker_url: str = "redis://localhost:6379/1"
celery_result_backend: str = "redis://localhost:6379/2"
```

### Шаг 2.3 — Создать `app/core/scheduler.py`

**Новый файл**: `backend/app/core/scheduler.py`

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.logging import get_logger

logger = get_logger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


def setup_scheduler() -> None:
    """Register all periodic jobs. Called once from FastAPI lifespan."""
    from app.tasks.hl_tracker import (
        refresh_leaderboard_async,
        track_active_traders_async,
    )
    from app.tasks.analytics_tasks import compute_quality_metrics_async
    from app.tasks.execution_tasks import (
        check_stop_losses_async,
        monitor_pending_trades_async,
    )
    from app.tasks.demo_reconcile import reconcile_async

    # NOTE: hl_tracker also schedules refresh_human_scores_async inside track_active_traders
    # via a separate job registration (see hl_tracker.py setup).

    scheduler.add_job(
        refresh_leaderboard_async,
        IntervalTrigger(seconds=600),
        id="refresh_leaderboard",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        track_active_traders_async,
        IntervalTrigger(seconds=5),
        id="track_active_traders",
        replace_existing=True,
        max_instances=1,  # prevent overlap if a run takes >5s
    )
    scheduler.add_job(
        check_stop_losses_async,
        IntervalTrigger(seconds=60),
        id="check_stop_losses",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        monitor_pending_trades_async,
        IntervalTrigger(seconds=30),
        id="monitor_pending_trades",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        compute_quality_metrics_async,
        IntervalTrigger(seconds=10800),
        id="compute_quality_metrics",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        refresh_human_scores_async,
        IntervalTrigger(seconds=14400),
        id="refresh_human_scores",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        reconcile_async,
        IntervalTrigger(seconds=300),
        id="reconcile_demo_positions",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("scheduler_jobs_registered", count=len(scheduler.get_jobs()))
```

### Шаг 2.4 — Обновить `main.py`: запустить scheduler в lifespan

**Файл**: `backend/app/main.py`

```python
# БЫЛО:
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("app_startup", ...)
    yield
    logger.info("app_shutdown")

# СТАНЕТ:
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from app.core.scheduler import scheduler, setup_scheduler
    setup_scheduler()
    scheduler.start()
    logger.info("app_startup", ...)
    yield
    scheduler.shutdown(wait=False)
    logger.info("app_shutdown")
```

### Шаг 2.5 — Переписать `app/tasks/hl_tracker.py`

**Файл**: `backend/app/tasks/hl_tracker.py`

Удалить `from app.tasks.celery_app import celery_app` и все `@celery_app.task(...)` декораторы.  
Переименовать публичные функции (убрать суффикс `_async`, сделать их просто именованными async функциями):

```python
# БЫЛО: три Celery-таска, каждый оборачивает _async-версию в asyncio.run()
@celery_app.task(name="app.tasks.hl_tracker.refresh_leaderboard", ...)
def refresh_leaderboard(self) -> None:
    asyncio.run(_refresh_leaderboard_async())

@celery_app.task(name="app.tasks.hl_tracker.track_active_traders", ...)
def track_active_traders(self) -> None:
    addresses = asyncio.run(_get_tracked_addresses())
    for address in addresses:
        poll_trader_positions.delay(address)

@celery_app.task(name="app.tasks.hl_tracker.poll_trader_positions", ...)
def poll_trader_positions(self, trader_address: str) -> None:
    asyncio.run(_poll_trader_positions_async(trader_address))

@celery_app.task(name="app.tasks.hl_tracker.refresh_human_scores", ...)
def refresh_human_scores(self) -> None:
    asyncio.run(_refresh_human_scores_async())

# СТАНЕТ: публичные async функции (бывшие _async-версии сохраняют тела, просто переименовываются)
async def refresh_leaderboard_async() -> int:
    """Бывшая _refresh_leaderboard_async — тело без изменений."""
    ...

async def track_active_traders_async() -> None:
    """Опрашивает позиции всех отслеживаемых трейдеров параллельно."""
    addresses = await _get_tracked_addresses()
    if not addresses:
        return
    # Запускаем параллельно вместо отдельных Celery-тасков
    await asyncio.gather(
        *[_poll_and_execute(addr) for addr in addresses],
        return_exceptions=True,  # не ронять весь gather при ошибке одного трейдера
    )
    if addresses:
        logger.debug("tracking_dispatched", count=len(addresses))

async def _poll_and_execute(trader_address: str) -> None:
    """Poll one trader and execute copy trades for all subscribers."""
    try:
        await _poll_trader_positions_async(trader_address)
    except Exception as exc:
        logger.error("poll_positions_failed", trader=trader_address, error=str(exc))

async def refresh_human_scores_async() -> int:
    """Бывшая _refresh_human_scores_async — тело без изменений."""
    ...
```

**Важно**: `_poll_trader_positions_async()` уже вызывает `fan_out_signal.delay(sig_id)`.  
После Phase 2 этот вызов нужно заменить (см. Шаг 2.7).

### Шаг 2.6 — Переписать `app/tasks/signal_consumer.py`

**Файл**: `backend/app/tasks/signal_consumer.py`

Вся логика сворачивается в одну inline-функцию, вызываемую из `_poll_trader_positions_async`:

```python
# БЫЛО: Celery-таск fan_out_signal, внутри вызывает execute_copy_trade.delay()
@celery_app.task(name="app.tasks.signal_consumer.fan_out_signal", ...)
def fan_out_signal(self, signal_id: int) -> None:
    ids = asyncio.run(_get_active_subscriber_ids(signal_id))
    for user_id in ids["real"]:
        execute_copy_trade.delay(signal_id, user_id)
    for user_id in ids["demo"]:
        simulate_demo_trade.delay(signal_id, user_id)

# СТАНЕТ: обычная async функция, вызывается из _poll_trader_positions_async напрямую
async def fan_out_signal_async(signal_id: int) -> None:
    """Find all active subscribers for signal's trader and execute trades."""
    ids = await _get_active_subscriber_ids(signal_id)  # та же логика, без изменений

    from app.tasks.execution_tasks import execute_copy_trade_async, simulate_demo_trade_async

    tasks = [execute_copy_trade_async(signal_id, uid) for uid in ids["real"]]
    tasks += [simulate_demo_trade_async(signal_id, uid) for uid in ids["demo"]]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        logger.warning("fan_out_partial_errors", signal_id=signal_id, errors=len(errors))

    logger.info(
        "fan_out_dispatched",
        signal_id=signal_id,
        real=len(ids["real"]),
        demo=len(ids["demo"]),
    )
```

В `_poll_trader_positions_async()` заменить:
```python
# БЫЛО:
for sig_id in signal_ids:
    fan_out_signal.delay(sig_id)

# СТАНЕТ:
for sig_id in signal_ids:
    await fan_out_signal_async(sig_id)
```

### Шаг 2.7 — Переписать `app/tasks/execution_tasks.py`

**Файл**: `backend/app/tasks/execution_tasks.py`

Убрать Celery-декораторы. Добавить простой retry через `tenacity` (уже есть в зависимостях):

```python
# БЫЛО (Celery с self.retry):
@celery_app.task(name="...", bind=True, max_retries=3, default_retry_delay=5)
def execute_copy_trade(self, signal_id: int, user_id: int) -> None:
    try:
        asyncio.run(_exec(signal_id, user_id))
    except NonRetryableError as exc:
        logger.warning(...)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=5) from exc

# СТАНЕТ:
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_not_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(5),
    retry=retry_if_not_exception_type(NonRetryableError),
    reraise=True,
)
async def execute_copy_trade_async(signal_id: int, user_id: int) -> None:
    from app.services.copy_engine.executor import execute_copy_trade as _exec
    await _exec(signal_id, user_id)

async def simulate_demo_trade_async(signal_id: int, user_id: int) -> None:
    from app.services.copy_engine.demo_executor import simulate_demo_trade as _sim
    try:
        await _sim(signal_id, user_id)
    except Exception as exc:
        logger.error("simulate_demo_trade_failed", signal_id=signal_id, user_id=user_id, error=str(exc))
```

Периодические таски остаются async функциями без retry (периодичность сама по себе является "retry"):
```python
async def check_stop_losses_async() -> None:
    """Бывшая _check_stop_losses_async — тело без изменений."""
    ...

async def monitor_pending_trades_async() -> None:
    """Бывшая _monitor_pending_trades_async — тело без изменений."""
    ...
```

Одноразовые fire-and-forget операции (вызываются из API):
```python
async def close_all_positions_for_user_async(user_id: int) -> None:
    from app.services.copy_engine.executor import close_all_positions_for_user as _exec
    count = await _exec(user_id)
    logger.info("emergency_stop_complete", user_id=user_id, closed=count)

async def close_subscription_positions_async(user_id: int, subscription_id: int) -> None:
    from app.services.copy_engine.executor import close_positions_for_subscription as _exec
    await _exec(user_id, subscription_id)
    logger.info("subscription_positions_closed", user_id=user_id, subscription_id=subscription_id)
```

### Шаг 2.8 — Переписать `app/tasks/analytics_tasks.py`

**Файл**: `backend/app/tasks/analytics_tasks.py`

```python
# БЫЛО:
@celery_app.task(name="...", bind=True, max_retries=2)
def compute_quality_metrics(self) -> None:
    asyncio.run(_compute_quality_metrics_async())

# СТАНЕТ:
async def compute_quality_metrics_async() -> None:
    """Публичная версия — вызывается из APScheduler."""
    try:
        count = await _compute_quality_metrics_async()
        logger.info("quality_metrics_computed", processed=count)
    except Exception as exc:
        logger.error("quality_metrics_task_failed", error=str(exc))
        # Периодическая задача — следующий запуск будет через 3 часа, retry не нужен
```

### Шаг 2.9 — Переписать `app/tasks/demo_reconcile.py`

**Файл**: `backend/app/tasks/demo_reconcile.py`

```python
# БЫЛО:
@celery_app.task(name="...", bind=True, max_retries=3)
def reconcile_demo_positions(self) -> None:
    asyncio.run(_reconcile_async())

# СТАНЕТ:
async def reconcile_async() -> int:
    """Публичная версия _reconcile_async — тело без изменений."""
    ...
    # Убрать вызов через asyncio.run, сделать напрямую
```

### Шаг 2.10 — Удалить `app/tasks/celery_app.py`

**Удалить файл**: `backend/app/tasks/celery_app.py`

Все импорты `from app.tasks.celery_app import celery_app` во всех файлах задач — удалить.

### Шаг 2.11 — Фикс fire-and-forget вызовов из API

**Файл**: `backend/app/api/wallet.py` строка 243-245:
```python
# БЫЛО:
from app.tasks.execution_tasks import close_all_positions_for_user
close_all_positions_for_user.delay(current_user.id)

# СТАНЕТ:
import asyncio
from app.tasks.execution_tasks import close_all_positions_for_user_async
asyncio.create_task(close_all_positions_for_user_async(current_user.id))
```

**Файл**: `backend/app/services/subscription_service.py` строки 245-248:
```python
# БЫЛО:
from app.tasks.execution_tasks import close_subscription_positions
close_subscription_positions.delay(user_id, subscription_id)

# СТАНЕТ:
import asyncio
from app.tasks.execution_tasks import close_subscription_positions_async
asyncio.create_task(close_subscription_positions_async(user_id, subscription_id))
```

---

## Phase 3 — Упростить DB-сессии

### Шаг 3.1 — Убрать NullPool-вариант из database.py

**Файл**: `backend/app/core/database.py`

Удалить:
- `_task_engine = create_async_engine(..., poolclass=NullPool)`
- `_TaskSessionFactory = async_sessionmaker(bind=_task_engine, ...)`
- `get_task_db_session()` — полностью

```python
# УДАЛИТЬ весь блок:
_task_engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    poolclass=NullPool,
)
_TaskSessionFactory = async_sessionmaker(...)

@asynccontextmanager
async def get_task_db_session() -> AsyncGenerator[AsyncSession, None]:
    ...
```

Также удалить `from sqlalchemy.pool import NullPool` если больше не используется.

### Шаг 3.2 — Заменить все вызовы get_task_db_session

Grep для поиска всех мест:
```bash
grep -rn "get_task_db_session" backend/app/
```

Во всех файлах заменить:
```python
# БЫЛО:
from app.core.database import get_task_db_session
async with get_task_db_session() as db:

# СТАНЕТ:
from app.core.database import get_db_session
async with get_db_session() as db:
```

Файлы которые точно используют `get_task_db_session`:
- `app/tasks/hl_tracker.py`
- `app/tasks/analytics_tasks.py`
- `app/tasks/execution_tasks.py`
- `app/tasks/demo_reconcile.py`

---

## Phase 4 — Почистить Docker и инфраструктуру

### ✅ Phase 4 выполнена (2026-06-25)

### Шаг 4.1 — Обновить docker-compose.yml

Удалить сервисы `celery-worker` и `celery-beat`.  
Обновить сервис `backend` — убрать env-vars `CELERY_*` и `CLICKHOUSE_*`.

```yaml
# УДАЛИТЬ целиком:
celery-worker:
  ...

celery-beat:
  ...

# Из backend ENV удалить:
CELERY_BROKER_URL: redis://redis:6379/1
CELERY_RESULT_BACKEND: redis://redis:6379/2
CLICKHOUSE_HOST: clickhouse

# Из volumes удалить:
clickhouse_data:
```

### Шаг 4.2 — Обновить docker-compose.prod.yml

Удалить секции `clickhouse:`, `celery-worker:`, `celery-beat:`.  
Из backend и других сервисов убрать `CLICKHOUSE_PASSWORD` и `CELERY_*`.

### Шаг 4.3 — Обновить Makefile

```makefile
# УДАЛИТЬ targets:
worker:
    cd backend && uv run celery -A app.tasks.celery_app worker ...

beat:
    cd backend && uv run celery -A app.tasks.celery_app beat ...

# Команды deploy больше не нужно рестартить 3 сервиса:
# БЫЛО:
deploy:
    $(PROD_COMPOSE) up -d --no-deps backend celery-worker celery-beat

# СТАНЕТ:
deploy:
    $(PROD_COMPOSE) build backend
    $(PROD_COMPOSE) run --rm backend sh -c "uv run alembic upgrade head"
    $(PROD_COMPOSE) up -d --no-deps backend
```

### Шаг 4.4 — Обновить env-файлы

**`.env.example`** — удалить:
```
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=9000
CLICKHOUSE_DB=copytrade
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=
```

**`.env.prod.example`** — то же самое.

---

## Phase 5 — Удалить импорты `import asyncio` в Celery-тасках

После удаления Celery, `import asyncio` в task-файлах остаётся нужным только для `asyncio.gather()` и `asyncio.sleep()`. Убрать только строки `asyncio.run(...)`.

Все старые `import asyncio` оставить — они нужны для `asyncio.gather`, `asyncio.sleep`, `asyncio.to_thread`.

---

## Порядок выполнения

```
Phase 1 → тест → commit "remove clickhouse"
Phase 2 → Phase 3 (вместе, т.к. связаны) → тест → commit "replace celery with apscheduler"
Phase 4 → тест docker → commit "cleanup docker compose"
```

Не делать всё в одном PR — если что-то сломается, проще откатить.

---

## Тестирование после каждой фазы

### После Phase 1 (удаление ClickHouse)
```bash
make down && make up
curl http://localhost:8001/health
# Открыть в браузере: трейдер с позициями → /traders/{id}/positions должен работать
cd backend && uv run pytest tests/ -v
```

### После Phase 2+3 (замена Celery)
```bash
make down && make up
# Проверить что scheduler запустился — в логах backend должно быть "scheduler_jobs_registered"
docker compose logs backend | grep scheduler
# Подождать ~10 сек и проверить что track_active_traders отработал:
docker compose logs backend | grep "tracking_dispatched"
# Проверить WebSocket (позиции меняются в реальном времени)
cd backend && uv run pytest tests/ -v
```

### После Phase 4 (Docker cleanup)
```bash
docker compose config  # проверить синтаксис
make down && make up
docker compose ps  # убедиться что запустились именно 5 сервисов
```

---

## Rollback plan

**Phase 1** обратима: восстановить `clickhouse_client.py`, добавить обратно в docker-compose, вернуть `_ch_*` функции. Git history сохраняет всё.

**Phase 2** обратима: вернуть `celery_app.py`, обернуть функции обратно в Celery-декораторы. APScheduler просто выключить в lifespan.

**Если scheduler упал и не рестартился**: backend имеет `restart: unless-stopped`, Docker его поднимет. Scheduler стартует в lifespan при каждом запуске backend.

---

## Что НЕ меняется

- Вся бизнес-логика в `services/` — **без изменений**
- Все API routes (`api/`) — **без изменений** (кроме 2 fire-and-forget вызовов)
- Redis-снэпшоты `hl:snapshot:{address}` — **остаются**, WebSocket на них работает
- Alembic-миграции и schema БД — **без изменений**
- Frontend — **без изменений**
- Nginx — **без изменений**
- Telegram-уведомления — **без изменений**
- Все модели данных — **без изменений**
- `signal_detector.py`, `signal_publisher.py`, `risk_manager.py` — **без изменений**
- `exchange_client.py`, `info_client.py`, `order_builder.py` — **без изменений**

---

## Итоговое изменение пайплайна

```
# БЫЛО (4 хопа через Redis):
Beat (5s) → track_active_traders [Redis] → poll_trader_positions.delay() [Redis]
  → detect_changes → save_signals → fan_out_signal.delay() [Redis]
    → execute_copy_trade.delay() [Redis]

# СТАНЕТ (in-process, 0 брокерных хопов):
APScheduler (5s) → track_active_traders_async()
  → asyncio.gather([_poll_and_execute(addr) for addr in addresses])
    → _poll_trader_positions_async(addr)
      → detect_changes → save_signals → fan_out_signal_async(sig_id)
        → asyncio.gather([execute_copy_trade_async(sig_id, uid) for uid in subscribers])
```

Всё в одном Python-процессе, без сетевых хопов через Redis для задач.

---

## Деплой на сервер

> Все фазы выполнены. Ниже — единая инструкция для деплоя с нуля на сервер.

**Миграции БД не требуются** — ни одна из фаз не меняет PostgreSQL-схему.

### Предусловия

```bash
cd /path/to/copy_trade

# Убедиться что .env заполнен (не .env.example)
cat .env | grep -E "^(SECRET_KEY|TELEGRAM_BOT_TOKEN|AGENT_ENCRYPTION_KEY|REDIS_PASSWORD)" | head
```

### Шаг 1 — Остановить старый стек и почистить мусор

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down

# Удалить зомби-контейнеры celery если остались от старого деплоя
docker rm -f $(docker ps -a -q --filter name=celery) 2>/dev/null || true

# Удалить volume ClickHouse если ещё не удалён
docker volume rm $(docker volume ls -q | grep clickhouse_data) 2>/dev/null || true
```

### Шаг 2 — Получить новый код

```bash
git pull origin main
```

### Шаг 3 — Задеплоить

```bash
# Первый запуск на сервере: поднять ВСЕ сервисы (включая nginx-proxy)
make prod-up
# Внутри: docker compose up -d — запускает все 5 сервисов

# ИЛИ вручную с миграциями:
# docker compose -f docker-compose.yml -f docker-compose.prod.yml build backend frontend
# docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm backend sh -c "uv run alembic upgrade head"
# docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

> `make deploy` — только для **повторных** деплоев, когда все 5 сервисов уже запущены.  
> Он перезапускает только `backend` и `frontend`, поэтому `nginx-proxy` не стартует если был остановлен.

### Шаг 4 — Проверить

```bash
# Состав сервисов (ожидаем ровно 5: postgres, redis, backend, frontend, nginx-proxy)
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

# Scheduler и трекинг
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs backend --tail=50 \
  | grep -E "(scheduler_jobs_registered|tracking_dispatched|error|ERROR)"
# Ожидаем: "scheduler_jobs_registered" {"count": 7}
# Через ~10 сек: "tracking_dispatched"

# Health
curl -s http://localhost:8000/health
```

### Дальнейшие деплои

| Ситуация | Команда | Время |
|---|---|---|
| **Первый запуск** (все сервисы) | `make prod-up` | ~30 мин |
| Python-код или миграции | `make deploy-backend` | ~30 сек |
| Только фронтенд | `make deploy-frontend` | ~25 мин |
| Backend + frontend | `make deploy` | ~30 мин |

> Scheduler живёт внутри backend-процесса — рестарт backend автоматически перезапускает все джобы.
