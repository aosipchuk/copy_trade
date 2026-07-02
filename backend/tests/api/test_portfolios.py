"""Integration tests for read-only model portfolio API endpoints."""

import hashlib
import hmac
import itertools
import json
import time
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.portfolio import (
    ModelPortfolio,
    ModelPortfolioAllocation,
    ModelPortfolioVersion,
    PortfolioBacktest,
    UserPortfolioSubscription,
)
from app.models.trader import Trader
from app.models.user import User

pytestmark = pytest.mark.asyncio(loop_scope="session")

_counter: itertools.count = itertools.count(1)


def _make_init_data(user_id: int, username: str = "portfoliotest") -> str:
    bot_token = "123456:test"
    user_data = json.dumps(
        {"id": user_id, "username": username, "first_name": "Portfolio"}
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


async def _auth_header(client, user_id: int) -> dict[str, str]:
    response = await client.post(
        "/api/auth/telegram", json={"init_data": _make_init_data(user_id)}
    )
    assert response.status_code == 200, f"Auth failed: {response.text}"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _seed_published_portfolio(db_session) -> str:
    index = next(_counter)
    now = datetime(2026, 7, 2, 12, 0, 0)
    slug = f"balanced-api-{index}"
    portfolio = ModelPortfolio(
        slug=slug,
        name=f"Balanced API {index}",
        risk_profile="balanced",
        status="active",
        description="Balanced model portfolio.",
        methodology_version="balanced-mvp-v1",
        rebalance_cadence="weekly",
        min_equity_usd=Decimal("1000.00"),
        monthly_price_usd=Decimal("19.00"),
        trial_days=7,
    )
    db_session.add(portfolio)
    await db_session.flush()

    version = ModelPortfolioVersion(
        portfolio_id=portfolio.id,
        version_no=1,
        status="published",
        valid_from=now,
        approved_at=now,
        approval_note="Approved for read-only API test.",
        summary_json={
            "trader_count": 2,
            "target_weight_sum_pct": 100.0,
            "max_weight_pct": 60.0,
        },
    )
    db_session.add(version)
    await db_session.flush()

    traders: list[Trader] = []
    for offset in range(2):
        trader = Trader(
            hl_address=f"0xpf{index:04x}{offset:034x}",
            display_name=f"Portfolio Trader {index}-{offset}",
            is_active=True,
            has_perp_activity=True,
        )
        db_session.add(trader)
        traders.append(trader)
    await db_session.flush()
    version.summary_json = {
        **(version.summary_json or {}),
        "account_size_profiles": [
            {
                "tier": "sample",
                "allocations": [
                    {
                        "trader_id": traders[0].id,
                        "hl_address": traders[0].hl_address,
                    }
                ],
            }
        ],
    }

    db_session.add_all(
        [
            ModelPortfolioAllocation(
                version_id=version.id,
                trader_id=traders[0].id,
                target_weight_pct=Decimal("60.000"),
                copy_ratio_pct=Decimal("100.00"),
                max_leverage=Decimal("8.00"),
                stop_loss_pct=Decimal("20.00"),
                sizing_mode="fixed_ratio",
                reason_code="balanced_mvp_score",
                reason_text="Selected by deterministic Balanced MVP methodology.",
                score_snapshot={
                    "portfolio_score": 81.25,
                    "source_metrics": {
                        "roi_pct": 12.0,
                        "max_drawdown_pct": 8.5,
                        "active_trading_days": 90,
                    },
                },
                constraint_snapshot={"selection_rank": 1},
            ),
            ModelPortfolioAllocation(
                version_id=version.id,
                trader_id=traders[1].id,
                target_weight_pct=Decimal("40.000"),
                copy_ratio_pct=Decimal("100.00"),
                max_leverage=Decimal("8.00"),
                stop_loss_pct=Decimal("20.00"),
                sizing_mode="fixed_ratio",
                reason_code="balanced_mvp_score",
                reason_text="Selected by deterministic Balanced MVP methodology.",
                score_snapshot={
                    "portfolio_score": 78.5,
                    "source_metrics": {
                        "roi_pct": 6.0,
                        "max_drawdown_pct": 5.0,
                        "active_trading_days": 90,
                    },
                },
                constraint_snapshot={"selection_rank": 2},
            ),
            PortfolioBacktest(
                portfolio_version_id=version.id,
                period_days=180,
                initial_equity_usd=Decimal("10000.00"),
                total_return_pct=Decimal("8.1000"),
                max_drawdown_pct=Decimal("7.1000"),
                sharpe_ratio=Decimal("1.2000"),
                sortino_ratio=Decimal("1.5000"),
                win_rate_pct=Decimal("55.00"),
                turnover_pct=Decimal("350.0000"),
                fees_usd=Decimal("14.0000"),
                slippage_usd=Decimal("17.5000"),
                missed_trade_count=0,
                assumptions_json={
                    "data_source": "aggregate_metric_proxy",
                    "fees_bps": 4.0,
                    "slippage_bps": 5.0,
                    "uses_trade_level_fills": False,
                },
                equity_curve_json={"source": "aggregate_metric_proxy", "points": []},
            ),
        ]
    )
    await db_session.commit()
    return slug


async def _portfolio_version_ids(db_session, slug: str) -> tuple[int, int]:
    result = await db_session.execute(
        select(ModelPortfolio.id, ModelPortfolioVersion.id)
        .join(
            ModelPortfolioVersion,
            ModelPortfolioVersion.portfolio_id == ModelPortfolio.id,
        )
        .where(
            ModelPortfolio.slug == slug,
            ModelPortfolioVersion.status == "published",
            ModelPortfolioVersion.valid_to.is_(None),
        )
    )
    row = result.one()
    return int(row[0]), int(row[1])


async def _user_id_for_telegram(db_session, telegram_id: int) -> int:
    result = await db_session.execute(
        select(User.id).where(User.telegram_id == telegram_id)
    )
    return int(result.scalar_one())


def _contains_identity(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key
                in {
                    "trader_id",
                    "peer_trader_id",
                    "trader_address",
                    "trader_display_name",
                    "trader_name",
                    "hl_address",
                }
                and item is not None
            ):
                return True
            if (
                key == "trader"
                and isinstance(item, dict)
                and any(
                    item.get(identity_key) is not None
                    for identity_key in ("id", "address", "display_name")
                )
            ):
                return True
            if _contains_identity(item):
                return True
    if isinstance(value, list):
        return any(_contains_identity(item) for item in value)
    return False


class TestPortfoliosReadOnly:
    @pytest.mark.asyncio
    async def test_requires_auth(self, client) -> None:
        response = await client.get("/api/portfolios")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_returns_active_portfolio_with_version_and_backtest(
        self, client, db_session
    ) -> None:
        slug = await _seed_published_portfolio(db_session)
        headers = await _auth_header(client, user_id=90001)

        response = await client.get("/api/portfolios", headers=headers)

        assert response.status_code == 200
        items = response.json()
        portfolio = next(item for item in items if item["slug"] == slug)
        assert portfolio["current_version"]["version_no"] == 1
        assert portfolio["current_version"]["target_weight_sum_pct"] == 100.0
        assert portfolio["latest_backtest"]["assumptions_json"]["fees_bps"] == 4.0

    @pytest.mark.asyncio
    async def test_detail_redacts_trader_identities_before_payment(
        self, client, db_session
    ) -> None:
        slug = await _seed_published_portfolio(db_session)
        headers = await _auth_header(client, user_id=90002)

        response = await client.get(f"/api/portfolios/{slug}", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["slug"] == slug
        assert body["trader_details_visible"] is False
        allocations = body["current_version"]["allocations"]
        assert len(allocations) == 2
        assert round(sum(item["target_weight_pct"] for item in allocations), 3) == 100.0
        assert allocations[0]["trader_id"] is None
        assert allocations[0]["trader_address"] is None
        assert allocations[0]["trader_display_name"] is None
        assert allocations[0]["portfolio_score"] is not None
        assert not _contains_identity(body["current_version"]["summary_json"])
        assert (
            body["backtests"][0]["assumptions_json"]["uses_trade_level_fills"] is False
        )

    @pytest.mark.asyncio
    async def test_detail_returns_trader_identities_after_payment(
        self, client, db_session
    ) -> None:
        slug = await _seed_published_portfolio(db_session)
        telegram_id = 90012
        headers = await _auth_header(client, user_id=telegram_id)
        user_id = await _user_id_for_telegram(db_session, telegram_id)
        portfolio_id, version_id = await _portfolio_version_ids(db_session, slug)
        db_session.add(
            UserPortfolioSubscription(
                user_id=user_id,
                portfolio_id=portfolio_id,
                active_version_id=version_id,
                status="active",
                is_demo=False,
                auto_rebalance=False,
                total_allocation_usd=Decimal("1000.00"),
                close_removed_positions=False,
                billing_provider="stripe",
                billing_subscription_id="sub_paid_portfolio_detail",
            )
        )
        await db_session.commit()

        response = await client.get(f"/api/portfolios/{slug}", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["trader_details_visible"] is True
        allocation = body["current_version"]["allocations"][0]
        assert allocation["trader_id"] is not None
        assert allocation["trader_address"].startswith("0xpf")
        assert allocation["trader_display_name"].startswith("Portfolio Trader")

    @pytest.mark.asyncio
    async def test_backtests_endpoint_returns_current_published_backtests(
        self, client, db_session
    ) -> None:
        slug = await _seed_published_portfolio(db_session)
        headers = await _auth_header(client, user_id=90003)

        response = await client.get(
            f"/api/portfolios/{slug}/backtests", headers=headers
        )

        assert response.status_code == 200
        backtests = response.json()
        assert backtests[0]["period_days"] == 180
        assert backtests[0]["initial_equity_usd"] == 10000.0

    @pytest.mark.asyncio
    async def test_explanations_endpoint_returns_safe_source_fact_rationales(
        self, client, db_session
    ) -> None:
        slug = await _seed_published_portfolio(db_session)
        headers = await _auth_header(client, user_id=90004)

        response = await client.get(
            f"/api/portfolios/{slug}/explanations", headers=headers
        )

        assert response.status_code == 200
        body = response.json()
        forbidden = ("guarantee", "guaranteed", "risk-free", "безопас")
        assert not any(word in body["summary"].lower() for word in forbidden)
        assert body["generated_by"] == "template"
        assert len(body["allocations"]) == 2
        first = body["allocations"][0]
        assert body["trader_details_visible"] is False
        assert first["trader_id"] is None
        assert first["trader_address"] is None
        assert first["trader_display_name"] is None
        assert not _contains_identity(first["source_facts"])
        assert "source_facts" in first
        assert first["explanation"]
        available = set(first["source_facts"]["available_fact_keys"])
        assert set(first["used_source_fact_keys"]) <= available
        assert not any(word in first["explanation"].lower() for word in forbidden)

    @pytest.mark.asyncio
    async def test_weekly_report_generation_rejects_non_override_user(
        self, client, db_session
    ) -> None:
        slug = await _seed_published_portfolio(db_session)
        headers = await _auth_header(client, user_id=90005)

        before = await client.get(
            f"/api/portfolios/{slug}/weekly-report", headers=headers
        )
        assert before.status_code == 200
        assert before.json() is None

        created = await client.post(
            f"/api/portfolios/{slug}/weekly-report", headers=headers
        )

        assert created.status_code == 403

    @pytest.mark.asyncio
    async def test_weekly_report_generation_persists_source_facts_for_override_user(
        self, client, db_session, monkeypatch
    ) -> None:
        user_id = 90006
        monkeypatch.setattr(
            settings,
            "model_portfolio_beta_override_telegram_ids",
            [user_id],
        )
        slug = await _seed_published_portfolio(db_session)
        headers = await _auth_header(client, user_id=user_id)

        before = await client.get(
            f"/api/portfolios/{slug}/weekly-report", headers=headers
        )
        assert before.status_code == 200
        assert before.json() is None

        created = await client.post(
            f"/api/portfolios/{slug}/weekly-report", headers=headers
        )
        fetched = await client.get(
            f"/api/portfolios/{slug}/weekly-report", headers=headers
        )

        assert created.status_code == 200
        assert fetched.status_code == 200
        created_body = created.json()
        fetched_body = fetched.json()
        assert fetched_body["id"] == created_body["id"]
        assert created_body["generated_by"] == "template"
        assert created_body["source_facts"]["allocation_count"] == 2
        assert created_body["source_facts"]["target_weight_sum_pct"] == 100.0
        assert created_body["summary"]
        assert created_body["sections"]
        assert created_body["allocation_notes"]

        repeated = await client.post(
            f"/api/portfolios/{slug}/weekly-report", headers=headers
        )
        assert repeated.status_code == 200
        assert repeated.json()["id"] == created_body["id"]
