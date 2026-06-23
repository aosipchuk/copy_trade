# Copy-Trade — Telegram Mini App

Telegram Mini App для автоматического копирования сделок топовых трейдеров Hyperliquid. Бэкенд опрашивает лидерборд каждые 5 секунд, обнаруживает изменения в позициях и исполняет зеркальные ордера через агентские кошельки пользователей с подписью EIP-712.

## Как это работает

1. Пользователь открывает Mini App в Telegram — авторизация через HMAC-верификацию `initData`.
2. Пользователь подключает Hyperliquid-кошелёк и делегирует агентский ключ (одноразовый `signTypedData` в MetaMask).
3. Пользователь подписывается на одного или нескольких трейдеров.
4. Celery-воркеры непрерывно отслеживают позиции трейдеров; любое изменение (открытие / обновление / закрытие) фиксируется и рассылается всем подписчикам как копи-трейд ордер, исполняемый на стороне сервера.

## Стек

| Слой | Технология |
|---|---|
| Backend API | FastAPI + SQLAlchemy 2.0 (asyncpg) |
| Очередь задач | Celery 5 + Redis |
| Основная БД | PostgreSQL 16 |
| Аналитическая БД | ClickHouse 24 |
| Кэш / брокер | Redis 7 |
| Frontend | React + Vite + TypeScript (Telegram Mini App SDK) |
| Подпись | EIP-712 через `eth-account` |
| Инфра | Docker Compose, nginx, Let's Encrypt |

---

## Локальный запуск на macOS

### Что нужно установить

- **Docker Desktop** — [скачать](https://www.docker.com/products/docker-desktop/)
- **uv** — менеджер Python-пакетов
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Node.js 22+** — для разработки фронтенда
  ```bash
  brew install node
  ```

### 1. Клонировать репозиторий и настроить окружение

```bash
git clone <repo-url>
cd copy_trade

# Создать .env из примера
cp .env.example .env
```

Открыть `.env` и заполнить обязательные поля:

```dotenv
# Обязательно — получить у @BotFather
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234

# Обязательно — случайный 32-байтовый hex-ключ
# Сгенерировать: python -c "import secrets; print(secrets.token_hex(32))"
AGENT_ENCRYPTION_KEY=<64 hex символа>

# Обязательно — ключ подписи JWT, минимум 32 символа
SECRET_KEY=<случайная строка минимум 32 символа>

# Сеть: mainnet или testnet
HL_NETWORK=testnet
```

Остальные значения (URLs для Postgres, Redis, ClickHouse) работают по умолчанию без изменений.

### 2. Запустить инфраструктуру и бэкенд (Docker)

Самый простой путь — поднимает Postgres на `:5433`, Redis на `:6380`, ClickHouse на `:8123`, FastAPI-бэкенд на `:8000`, Celery-воркер и Celery-beat:

```bash
make up
```

Проверить, что всё запустилось:

```bash
make logs           # стримить логи всех контейнеров
docker compose ps   # посмотреть статус сервисов
```

Применить миграции БД (нужно только при первом запуске или после добавления новых миграций):

```bash
make migrate
```

### 3. Запустить dev-сервер фронтенда

```bash
cd frontend
cp .env.example .env.local
# Отредактировать .env.local:
#   VITE_API_URL=http://localhost:8000
#   VITE_WS_URL=ws://localhost:8000
#   VITE_DEV_JWT=<опционально: JWT для обхода Telegram-авторизации в браузере>
npm install
npm run dev
```

Фронтенд будет доступен по адресу `http://localhost:5173`.

### 4. Запустить бэкенд локально без Docker (опционально)

Если нужен hot-reload бэкенда вне Docker:

```bash
# Оставить работать только инфра-сервисы
docker compose up -d postgres redis clickhouse

# Установить Python-зависимости
make install

# Запустить API-сервер с авто-перезагрузкой
make run

# В отдельных терминалах:
make worker    # Celery-воркер
make beat      # Celery-планировщик
```

### 5. Проверить подпись Hyperliquid (опционально)

Отдельный скрипт, который отправляет тестовый `approveAgent` на testnet для проверки цепочки EIP-712 подписи:

```bash
cd backend
uv run python scripts/validate_hl_signing.py
```

### Полезные команды для разработки

```bash
make logs        # Стримить логи всех контейнеров
make shell       # bash внутри контейнера бэкенда
make down        # Остановить все контейнеры

make migrate                # Применить ожидающие миграции
make makemigration          # Сгенерировать новую миграцию (запросит имя)
make downgrade              # Откатить одну миграцию

make lint                   # Проверка ruff + black
make lint-fix               # Автоисправление форматирования
make typecheck              # Строгая проверка mypy
make test                   # Запустить все тесты
make test-cov               # Тесты + HTML-отчёт покрытия
```

### Запуск тестов

Юнит-тесты не требуют никакой инфраструктуры:

```bash
cd backend && uv run pytest tests/unit/ -v
```

Интеграционные тесты требуют Postgres на `:5433` (запустить через `make up` или `docker compose up -d postgres`):

```bash
cd backend && uv run pytest tests/api/ -v
```

Запуск одного теста:

```bash
cd backend && uv run pytest tests/unit/test_signal_detector.py::TestSignalDetector::test_open_new_position -v
```

---

## Деплой на VPS

### Что нужно на сервере

- Ubuntu 22.04+ (или любой Linux с поддержкой Docker)
- Доменное имя, указывающее на IP сервера (A-запись)
- Docker Engine + плагин Docker Compose

```bash
# Установить Docker на Ubuntu
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # затем перелогиниться
```

### 1. Клонировать репозиторий и настроить продакшн-окружение

```bash
git clone <repo-url>
cd copy_trade

cp .env.prod.example .env
```

Отредактировать `.env` — все плейсхолдеры `REPLACE_WITH_*` должны быть заполнены:

```dotenv
ENVIRONMENT=production
DEBUG=false
DOMAIN=your.domain.com

# Сгенерировать: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=<64 hex символа>

# Надёжный пароль для БД
POSTGRES_PASSWORD=<надёжный пароль>
DATABASE_URL=postgresql+asyncpg://copytrade:<POSTGRES_PASSWORD>@postgres:5432/copytrade

# Пароль ClickHouse
CLICKHOUSE_PASSWORD=<надёжный пароль>

# Настоящий токен бота от @BotFather
TELEGRAM_BOT_TOKEN=<токен>

# 32-байтовый hex — ВНИМАНИЕ: смена ключа аннулирует все сохранённые агентские ключи
AGENT_ENCRYPTION_KEY=<64 hex символа>

HL_NETWORK=mainnet
VITE_API_URL=https://your.domain.com/api
```

### 2. Получить SSL-сертификат (только при первом деплое)

Контейнер certbot использует ACME webroot challenge. Nginx должен быть доступен на порту 80 до выпуска сертификата.

Сначала запустить nginx в HTTP-режиме, затем выпустить сертификат:

```bash
# Запустить стек, чтобы порт 80 был открыт для ACME-challenge
make prod-up

# Выпустить сертификат (DOMAIN читается из .env)
make ssl
```

После успешного выпуска сертификаты Let's Encrypt хранятся в Docker-volume `certbot_certs`. Certbot автоматически обновляет их каждые 12 часов.

### 3. Собрать и запустить все сервисы

```bash
make prod-build   # Собрать образы бэкенда и фронтенда
make prod-up      # Запустить все сервисы с prod-оверлеем
```

Запускается:
- `postgres`, `redis`, `clickhouse` — только во внутренней сети (порты не проброшены)
- `backend` — FastAPI на порту `8000` (внутренний)
- `celery-worker`, `celery-beat` — фоновые воркеры задач
- `frontend` — nginx со скомпилированным React SPA (внутренний)
- `nginx-proxy` — публичная точка входа на `:80` и `:443`, проксирует `/api/` → backend, `/` → frontend
- `certbot` — демон автообновления сертификатов

### 4. Применить миграции БД

В prod-режиме миграции применяются автоматически при старте бэкенда. При необходимости можно запустить вручную:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm backend sh -c "uv run alembic upgrade head"
```

### 5. Проверить деплой

```bash
make prod-logs              # стримить логи всех сервисов

# Проверить, что API отвечает
curl https://your.domain.com/api/health

# Статус сервисов
docker compose ps
```

### Деплой обновлений

После пуша нового кода:

```bash
git pull
make deploy
```

`make deploy` выполняет три шага последовательно:
1. Пересобирает образы `backend` и `frontend`
2. Запускает `alembic upgrade head` для применения новых миграций
3. Перезапускает `backend`, `celery-worker`, `celery-beat`, `frontend` без даунтайма

### Справочник продакшн-команд

```bash
make prod-up       # Запустить prod-стек
make prod-down     # Остановить prod-стек
make prod-logs     # Стримить логи
make prod-build    # Пересобрать образы
make deploy        # Собрать + мигрировать + перезапустить (для обновлений)
make ssl           # Выпустить/обновить SSL-сертификат вручную
```

---

## Cloudflare Tunnel — HTTPS без домена

Telegram Mini App требует HTTPS. Cloudflare Tunnel даёт бесплатный HTTPS-адрес без покупки домена и без настройки SSL.

### Установка `cloudflared` на Ubuntu

```bash
curl -L --output cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
cloudflared --version
```

### Быстрый туннель (без аккаунта, для теста)

URL рандомный — меняется при каждом перезапуске. Подходит для первичной проверки.

```bash
cloudflared tunnel --url http://localhost:80
```

В выводе появится URL вида `https://rainbow-dragon-abc123.trycloudflare.com` — вписать в BotFather.

### Постоянный туннель (нужен домен в Cloudflare)

**Шаг 1.** Логин и создание туннеля:

```bash
cloudflared tunnel login
cloudflared tunnel create copytrade
# Сохранит ключ в ~/.cloudflared/<UUID>.json
```

**Шаг 2.** Создать конфиг `~/.cloudflared/config.yml`:

```yaml
tunnel: copytrade
credentials-file: /root/.cloudflared/<UUID>.json

ingress:
  - hostname: your.domain.com
    service: http://localhost:80
  - service: http_status:404
```

**Шаг 3.** Привязать DNS и запустить:

```bash
cloudflared tunnel route dns copytrade your.domain.com
cloudflared tunnel run copytrade
```

**Шаг 4.** Автозапуск через systemd:

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

### Что прописать после получения URL

В `.env` на сервере:
```dotenv
FRONTEND_URL=https://your.domain.com
VITE_API_URL=https://your.domain.com/api
```

В BotFather (`/setmenubutton` или `/setapp`):
```
https://your.domain.com
```

---

## Структура проекта

```
copy_trade/
├── backend/
│   ├── app/
│   │   ├── api/          # HTTP-роуты и WebSocket-хендлеры
│   │   ├── core/         # Конфиг, БД, безопасность, Redis, ClickHouse
│   │   ├── models/       # SQLAlchemy ORM-модели
│   │   ├── schemas/      # Pydantic DTO для запросов и ответов
│   │   ├── services/     # Бизнес-логика (Hyperliquid-клиент, copy engine, риск-менеджер)
│   │   └── tasks/        # Celery-задачи (трекер, обработчик сигналов, исполнение)
│   ├── alembic/          # Миграции БД
│   ├── scripts/          # Вспомогательные скрипты
│   └── tests/            # Юнит и интеграционные тесты
├── frontend/             # React + Vite Telegram Mini App
├── infra/nginx/          # Конфиг обратного прокси nginx
├── docker-compose.yml    # Dev-стек
├── docker-compose.prod.yml  # Prod-оверлей
├── .env.example          # Шаблон dev-окружения
├── .env.prod.example     # Шаблон prod-окружения
└── Makefile
```

## Справочник переменных окружения

| Переменная | Обязательно | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Да | Токен бота от @BotFather |
| `SECRET_KEY` | Да | Ключ подписи JWT, минимум 32 символа |
| `AGENT_ENCRYPTION_KEY` | Да | 32-байтовый hex (64 символа) для AES-256-GCM шифрования агентских ключей |
| `HL_NETWORK` | Да | `mainnet` или `testnet` |
| `DOMAIN` | Только prod | Доменное имя для SSL и nginx |
| `VITE_API_URL` | Только prod | Полный URL API, например `https://your.domain.com/api` |
| `DATABASE_URL` | Да | Строка подключения asyncpg |
| `REDIS_URL` | Да | Строка подключения Redis |
| `CLICKHOUSE_HOST` | Да | Хост ClickHouse |

> **Важно по безопасности**: `AGENT_ENCRYPTION_KEY` шифрует все сохранённые приватные ключи агентов. Смена этого ключа аннулирует все существующие делегирования — пользователям придётся повторно подтвердить агента.
