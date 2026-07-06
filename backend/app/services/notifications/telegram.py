from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def configure_telegram_webhook() -> None:
    """Register the bot webhook and command menu when PUBLIC_URL is configured."""
    if settings.environment == "test":
        logger.info("telegram_webhook_skipped", reason="test environment")
        return
    if not settings.public_url:
        logger.info("telegram_webhook_skipped", reason="PUBLIC_URL not set")
        return
    if not settings.telegram_webhook_secret:
        logger.warning("telegram_webhook_skipped", reason="secret not set")
        return

    from aiogram import Bot
    from aiogram.types import BotCommand

    bot = Bot(token=settings.telegram_bot_token)
    webhook_url = f"{settings.public_url.rstrip('/')}/api/telegram/webhook"
    try:
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=["message", "callback_query"],
        )
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Открыть меню"),
                BotCommand(command="export", description="Excel выгрузки"),
            ]
        )
        logger.info("telegram_webhook_configured", url=webhook_url)
    except Exception as exc:
        logger.warning("telegram_webhook_configure_failed", error=str(exc))
    finally:
        await bot.session.close()


async def send_trade_notification(telegram_id: int, text: str) -> None:
    """Send a Telegram message to a user via Bot API."""
    if not settings.telegram_bot_token:
        logger.debug("telegram_notifications_disabled")
        return

    from aiogram import Bot

    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.send_message(chat_id=telegram_id, text=text, parse_mode="HTML")
        logger.debug("telegram_notification_sent", telegram_id=telegram_id)
    except Exception as exc:
        logger.warning(
            "telegram_notification_failed", telegram_id=telegram_id, error=str(exc)
        )
    finally:
        await bot.session.close()


def format_trade_filled(coin: str, side: str, size: float, price: float) -> str:
    direction = "Long" if side == "long" else "Short"
    return f"✅ <b>Trade copied</b>\n" f"{coin} {direction}: {size} @ ${price:,.2f}"


def format_trade_failed(coin: str, reason: str) -> str:
    return f"❌ <b>Trade failed</b>\n{coin}: {reason}"


def format_stop_loss_hit(trader_name: str | None, trader_address: str) -> str:
    name = trader_name or trader_address[:10] + "..."
    return f"🛑 <b>Stop-loss triggered</b>\nSubscription to {name} deactivated."


def format_portfolio_stop_loss_hit(loss_pct: float, threshold_pct: float) -> str:
    return (
        f"🚨 <b>Portfolio stop-loss triggered</b>\n"
        f"Account lost {abs(loss_pct):.1f}% (threshold: {threshold_pct:.0f}%).\n"
        f"All subscriptions deactivated and positions closed."
    )


def format_model_portfolio_rebalance_completed(
    *,
    portfolio_name: str,
    from_version_no: int,
    to_version_no: int,
    added_count: int,
    removed_count: int,
    changed_count: int,
) -> str:
    return (
        f"<b>{portfolio_name} rebalance applied</b>\n"
        f"v{from_version_no} -> v{to_version_no}\n"
        f"Added: {added_count}, removed: {removed_count}, changed: {changed_count}."
    )
