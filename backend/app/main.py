from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging

configure_logging(
    log_level="DEBUG" if settings.debug else "INFO",
    json_logs=not settings.is_development,
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("app_startup", environment=settings.environment, hl_network=settings.hl_network)
    yield
    logger.info("app_shutdown")


app = FastAPI(
    title="Copy Trade API",
    description="Hyperliquid copy-trading service via Telegram Mini App",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_development else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next: object) -> Response:
    import time
    import uuid

    request_id = str(uuid.uuid4())[:8]
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)

    start = time.perf_counter()
    response: Response = await call_next(request)  # type: ignore[operator]
    duration_ms = (time.perf_counter() - start) * 1000

    logger.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration_ms, 2),
    )
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(api_router, prefix="/api")
