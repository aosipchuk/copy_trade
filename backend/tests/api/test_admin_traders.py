"""Integration tests for admin trader import endpoints."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.core.config import settings
from app.models.trader import Trader, TraderStat
from app.services.analytics.metrics import QualityMetrics

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _make_init_data(user_id: int, username: str = "admintest") -> str:
    bot_token = "123456:test"
    user_data = json.dumps(
        {"id": user_id, "username": username, "first_name": "Admin"}
    )
    fields = {"user": user_data, "auth_date": str(int(time.time())), "query_id": "test"}
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items())
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(fields)


async def _auth_header(client, telegram_id: int) -> dict[str, str]:
    response = await client.post(
        "/api/auth/telegram", json={"init_data": _make_init_data(telegram_id)}
    )
    assert response.status_code == 200, f"Auth failed: {response.text}"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _quality_metrics() -> QualityMetrics:
    return QualityMetrics(
        win_rate_pct=62.0,
        max_drawdown_usd=120.0,
        max_drawdown_pct=12.0,
        trade_count=24,
        avg_trade_duration_hrs=3.5,
        first_trade_at=None,
        sharpe_ratio=1.4,
        sortino_ratio=2.1,
        profit_factor=1.8,
        avg_pnl_per_trade=42.0,
        max_losing_streak=3,
        profitable_days_pct=58.0,
        avg_trades_per_day=2.4,
        daily_pnl_std_dev=25.0,
        long_ratio_pct=54.0,
        avg_position_size_usd=2500.0,
        fees_paid_usd=18.0,
        calmar_ratio=1.6,
        max_drawdown_duration_days=8.0,
        active_trading_days=18,
        avg_leverage=4.0,
        daily_pnl_by_day={"2026-07-01": 10.0, "2026-07-02": 24.0},
        daily_returns_pct_by_day=None,
        composite_score=None,
        has_perp_activity=True,
        perp_period_stats={
            "day": (12.0, 1000.0),
            "week": (80.0, 8000.0),
            "month": (240.0, 24000.0),
            "allTime": (500.0, 50000.0),
        },
    )


class TestAdminTraderImport:
    async def test_non_admin_gets_403(self, client) -> None:
        headers = await _auth_header(client, telegram_id=81001)

        response = await client.post(
            "/api/admin/traders/import",
            json={"hl_address": "0x" + "11" * 20},
            headers=headers,
        )

        assert response.status_code == 403

    async def test_admin_invalid_address_gets_400(self, client, monkeypatch) -> None:
        telegram_id = 81002
        monkeypatch.setattr(settings, "admin_telegram_ids", [telegram_id])
        headers = await _auth_header(client, telegram_id=telegram_id)

        response = await client.post(
            "/api/admin/traders/import",
            json={"hl_address": "not-an-address"},
            headers=headers,
        )

        assert response.status_code == 400

    async def test_admin_import_creates_trader_and_stats(
        self, client, db_session, monkeypatch
    ) -> None:
        telegram_id = 81003
        hl_address = "0x" + "22" * 20
        monkeypatch.setattr(settings, "admin_telegram_ids", [telegram_id])

        async def fake_compute(
            address: str, *, use_available_history: bool = False
        ) -> QualityMetrics:
            assert address == hl_address
            assert use_available_history is True
            return _quality_metrics()

        monkeypatch.setattr(
            "app.services.admin_trader_import.compute_trader_quality_metrics",
            fake_compute,
        )
        headers = await _auth_header(client, telegram_id=telegram_id)

        response = await client.post(
            "/api/admin/traders/import",
            json={"hl_address": hl_address},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "imported"
        assert payload["has_perp_activity"] is True
        assert payload["trader"]["hl_address"] == hl_address
        assert payload["trader"]["is_active"] is True

        trader = await db_session.get(Trader, payload["trader"]["id"])
        assert trader is not None
        assert trader.has_perp_activity is True

        stats = await db_session.get(TraderStat, (trader.id, "allTime"))
        assert stats is not None
        assert float(stats.pnl_usd) == 500.0
        assert float(stats.volume_usd) == 50000.0
