from typing import Annotated, Any, cast

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.core.config import settings
from app.core.logging import get_logger
from app.services.analytics.export_data import fetch_trader_all_time_metrics_export_rows
from app.services.analytics.export_workbook import (
    build_hyperliquid_leaderboard_export_workbook,
    build_trader_all_time_metrics_export_workbook,
    hyperliquid_leaderboard_export_filename,
    trader_all_time_metrics_export_filename,
)
from app.services.hyperliquid.info_client import HyperliquidInfoClient

logger = get_logger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])

_CALLBACK_EXPORT_HL_ALL = "export:hl_all"
_CALLBACK_EXPORT_DB_ALLTIME = "export:db_alltime"


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    secret_token: Annotated[
        str | None,
        Header(alias="X-Telegram-Bot-Api-Secret-Token"),
    ] = None,
) -> dict[str, bool]:
    _verify_webhook_secret(secret_token)
    payload = await request.json()
    if isinstance(payload, dict):
        await _handle_update(cast(dict[str, Any], payload), background_tasks)
    return {"ok": True}


def _verify_webhook_secret(secret_token: str | None) -> None:
    if not settings.telegram_webhook_secret:
        logger.error("telegram_webhook_secret_missing")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram webhook is not configured",
        )
    if secret_token != settings.telegram_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram webhook secret",
        )


async def _handle_update(
    update: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> None:
    message = update.get("message")
    if isinstance(message, dict):
        text = message.get("text")
        chat_id = _chat_id_from_message(message)
        if chat_id is not None and isinstance(text, str) and _is_menu_command(text):
            await _send_export_menu(chat_id)
        return

    callback = update.get("callback_query")
    if isinstance(callback, dict):
        await _handle_callback(callback, background_tasks)


def _is_menu_command(text: str) -> bool:
    command = text.strip().split(maxsplit=1)[0]
    return command in {"/start", "/export", "/exports"}


async def _handle_callback(
    callback: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> None:
    callback_id = callback.get("id")
    callback_data = callback.get("data")
    chat_id = _chat_id_from_callback(callback)
    if not isinstance(callback_id, str) or chat_id is None:
        return

    if callback_data == _CALLBACK_EXPORT_HL_ALL:
        await _answer_callback(
            callback_id,
            "Готовлю полный HL leaderboard",
        )
        background_tasks.add_task(_send_hyperliquid_leaderboard_export, chat_id)
        return

    if callback_data == _CALLBACK_EXPORT_DB_ALLTIME:
        await _answer_callback(
            callback_id,
            "Готовлю allTime выгрузку из БД",
        )
        background_tasks.add_task(_send_db_all_time_metrics_export, chat_id)
        return

    await _answer_callback(
        callback_id,
        "Неизвестная кнопка",
        show_alert=True,
    )


def _chat_id_from_message(message: dict[str, Any]) -> int | None:
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    return _int_or_none(chat.get("id"))


def _chat_id_from_callback(callback: dict[str, Any]) -> int | None:
    message = callback.get("message")
    if isinstance(message, dict):
        chat_id = _chat_id_from_message(message)
        if chat_id is not None:
            return chat_id

    user = callback.get("from")
    if not isinstance(user, dict):
        return None
    return _int_or_none(user.get("id"))


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


async def _send_export_menu(chat_id: int) -> None:
    from aiogram import Bot
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="Выберите Excel-выгрузку:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Все трейдеры HL",
                            callback_data=_CALLBACK_EXPORT_HL_ALL,
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="AllTime из БД",
                            callback_data=_CALLBACK_EXPORT_DB_ALLTIME,
                        )
                    ],
                ]
            ),
        )
    finally:
        await bot.session.close()


async def _answer_callback(
    callback_id: str,
    text: str,
    *,
    show_alert: bool = False,
) -> None:
    from aiogram import Bot

    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.answer_callback_query(
            callback_query_id=callback_id,
            text=text,
            show_alert=show_alert,
        )
    finally:
        await bot.session.close()


async def _send_hyperliquid_leaderboard_export(chat_id: int) -> None:
    try:
        client = HyperliquidInfoClient(base_url=settings.hl_mainnet_api_url)
        rows = await client.get_leaderboard()
        workbook = build_hyperliquid_leaderboard_export_workbook(rows)
        await _send_excel_document(
            chat_id=chat_id,
            filename=hyperliquid_leaderboard_export_filename(),
            content=workbook,
            caption=f"Полный HL leaderboard: {len(rows)} строк.",
        )
    except Exception as exc:
        logger.warning("telegram_hl_leaderboard_export_failed", error=str(exc))
        await _send_export_failed(chat_id)


async def _send_db_all_time_metrics_export(chat_id: int) -> None:
    try:
        rows = await fetch_trader_all_time_metrics_export_rows()
        workbook = build_trader_all_time_metrics_export_workbook(rows)
        await _send_excel_document(
            chat_id=chat_id,
            filename=trader_all_time_metrics_export_filename(),
            content=workbook,
            caption=f"AllTime метрики из БД: {len(rows)} строк.",
        )
    except Exception as exc:
        logger.warning("telegram_db_alltime_export_failed", error=str(exc))
        await _send_export_failed(chat_id)


async def _send_excel_document(
    *,
    chat_id: int,
    filename: str,
    content: bytes,
    caption: str,
) -> None:
    from aiogram import Bot
    from aiogram.types import BufferedInputFile

    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(content, filename=filename),
            caption=caption,
        )
    finally:
        await bot.session.close()


async def _send_export_failed(chat_id: int) -> None:
    from aiogram import Bot

    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "Не удалось подготовить Excel-файл. "
                "Попробуйте позже."
            ),
        )
    finally:
        await bot.session.close()
