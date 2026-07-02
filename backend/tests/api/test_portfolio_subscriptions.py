"""Integration tests for demo model portfolio activation endpoints."""

import hashlib
import hmac
import itertools
import json
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from app.models.portfolio import (
    ModelPortfolio,
    ModelPortfolioAllocation,
    ModelPortfolioVersion,
    UserPortfolioItem,
)
from app.models.subscription import Subscription
from app.models.trader import Trader

pytestmark = pytest.mark.asyncio(loop_scope="session")

_counter: itertools.count = itertools.count(1)


@dataclass(frozen=True)
class SeededPortfolio:
    portfolio_id: int
    version_id: int
    slug: str
    trader_ids: list[int]


def _make_init_data(user_id: int, username: str = "portfolioactivation") -> str:
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


async def _auth_user(client, user_id: int) -> tuple[dict[str, str], int]:
    response = await client.post(
        "/api/auth/telegram", json={"init_data": _make_init_data(user_id)}
    )
    assert response.status_code == 200, f"Auth failed: {response.text}"
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, int(body["user_id"])


async def _seed_published_portfolio(db_session) -> SeededPortfolio:
    index = next(_counter)
    now = datetime(2026, 7, 2, 12, 0, 0)
    slug = f"balanced-activation-{index}"
    portfolio = ModelPortfolio(
        slug=slug,
        name=f"Balanced Activation {index}",
        risk_profile="balanced",
        status="active",
        description="Balanced demo activation test portfolio.",
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
        approval_note="Approved for demo activation API test.",
        summary_json={
            "trader_count": 3,
            "target_weight_sum_pct": 100.0,
        },
    )
    db_session.add(version)
    await db_session.flush()

    traders: list[Trader] = []
    for offset in range(3):
        trader = Trader(
            hl_address=f"0xpa{index:04x}{offset:034x}",
            display_name=f"Activation Trader {index}-{offset}",
            is_active=True,
            has_perp_activity=True,
        )
        db_session.add(trader)
        traders.append(trader)
    await db_session.flush()

    weights = [Decimal("50.000"), Decimal("30.000"), Decimal("20.000")]
    for offset, trader in enumerate(traders):
        db_session.add(
            ModelPortfolioAllocation(
                version_id=version.id,
                trader_id=trader.id,
                target_weight_pct=weights[offset],
                copy_ratio_pct=Decimal("100.00"),
                max_leverage=Decimal("8.00"),
                stop_loss_pct=Decimal("20.00"),
                sizing_mode="fixed_ratio",
                reason_code="balanced_mvp_score",
                reason_text="Selected by deterministic Balanced MVP methodology.",
                score_snapshot={
                    "portfolio_score": 80 - offset,
                    "source_metrics": {"max_drawdown_pct": 8 + offset},
                },
                constraint_snapshot={"selection_rank": offset + 1},
            )
        )
    await db_session.commit()
    return SeededPortfolio(
        portfolio_id=portfolio.id,
        version_id=version.id,
        slug=slug,
        trader_ids=[trader.id for trader in traders],
    )


def _activation_body(seed: SeededPortfolio, total: float = 1000.0) -> dict[str, object]:
    return {
        "portfolio_id": seed.portfolio_id,
        "active_version_id": seed.version_id,
        "is_demo": True,
        "auto_rebalance": False,
        "total_allocation_usd": total,
        "close_removed_positions": False,
    }


class TestDemoPortfolioActivation:
    async def test_requires_auth(self, client, db_session) -> None:
        seed = await _seed_published_portfolio(db_session)

        response = await client.post(
            "/api/portfolio-subscriptions", json=_activation_body(seed)
        )

        assert response.status_code == 401

    async def test_demo_activation_creates_generated_subscriptions_and_items(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, _ = await _auth_user(client, user_id=91001)

        response = await client.post(
            "/api/portfolio-subscriptions",
            json=_activation_body(seed),
            headers=headers,
        )

        assert response.status_code == 201
        body = response.json()
        assert body["created"] is True
        assert body["is_demo"] is True
        assert body["status"] == "active"
        assert body["portfolio_slug"] == seed.slug
        assert len(body["items"]) == 3
        assert sum(item["target_allocation_usd"] for item in body["items"]) == 1000.0
        assert [item["target_allocation_usd"] for item in body["items"]] == [
            500.0,
            300.0,
            200.0,
        ]

        for item in body["items"]:
            subscription = item["subscription"]
            assert subscription["is_demo"] is True
            assert subscription["is_active"] is True
            assert subscription["source_type"] == "model_portfolio"
            assert subscription["source_id"] == body["id"]
            assert subscription["source_version_id"] == seed.version_id
            assert subscription["managed_by_portfolio"] is True

        subs_result = await db_session.execute(
            select(Subscription).where(Subscription.source_id == body["id"])
        )
        generated_subscriptions = list(subs_result.scalars().all())
        assert len(generated_subscriptions) == 3
        assert all(sub.managed_by_portfolio for sub in generated_subscriptions)

    async def test_demo_activation_is_idempotent_for_same_version(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, _ = await _auth_user(client, user_id=91002)
        request_body = _activation_body(seed)

        first = await client.post(
            "/api/portfolio-subscriptions", json=request_body, headers=headers
        )
        second = await client.post(
            "/api/portfolio-subscriptions", json=request_body, headers=headers
        )

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["created"] is False
        assert second.json()["id"] == first.json()["id"]

        subs_result = await db_session.execute(
            select(Subscription).where(
                Subscription.source_id == first.json()["id"],
                Subscription.source_type == "model_portfolio",
            )
        )
        assert len(list(subs_result.scalars().all())) == 3

    async def test_demo_activation_reports_manual_live_conflicts_without_blocking(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91003)
        manual = Subscription(
            user_id=user_id,
            trader_id=seed.trader_ids[0],
            max_allocation_usd=Decimal("100.00"),
            copy_ratio_pct=Decimal("100.00"),
            stop_loss_pct=Decimal("20.00"),
            max_leverage=Decimal("8.00"),
            is_active=True,
            is_demo=False,
            source_type="manual",
            managed_by_portfolio=False,
        )
        db_session.add(manual)
        await db_session.commit()

        response = await client.post(
            "/api/portfolio-subscriptions",
            json=_activation_body(seed),
            headers=headers,
        )

        assert response.status_code == 201
        body = response.json()
        assert len(body["conflicts"]) == 1
        assert body["conflicts"][0]["trader_id"] == seed.trader_ids[0]
        assert body["conflicts"][0]["subscription_id"] == manual.id
        assert len(body["items"]) == 3

    async def test_cancel_demo_portfolio_disables_only_portfolio_owned_subscriptions(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91004)
        manual = Subscription(
            user_id=user_id,
            trader_id=seed.trader_ids[0],
            max_allocation_usd=Decimal("100.00"),
            copy_ratio_pct=Decimal("100.00"),
            stop_loss_pct=Decimal("20.00"),
            max_leverage=Decimal("8.00"),
            is_active=True,
            is_demo=True,
            source_type="manual",
            managed_by_portfolio=False,
        )
        db_session.add(manual)
        await db_session.commit()

        activation = await client.post(
            "/api/portfolio-subscriptions",
            json=_activation_body(seed),
            headers=headers,
        )
        assert activation.status_code == 201
        portfolio_subscription_id = activation.json()["id"]

        cancel = await client.delete(
            f"/api/portfolio-subscriptions/{portfolio_subscription_id}",
            headers=headers,
        )

        assert cancel.status_code == 200
        body = cancel.json()
        assert body["status"] == "canceled"
        assert all(item["status"] == "removed" for item in body["items"])
        assert all(item["subscription"]["is_active"] is False for item in body["items"])

        manual_result = await db_session.execute(
            select(Subscription).where(Subscription.id == manual.id)
        )
        manual_after_cancel = manual_result.scalar_one()
        assert manual_after_cancel.is_active is True
        assert manual_after_cancel.source_type == "manual"

        items_result = await db_session.execute(
            select(UserPortfolioItem).where(
                UserPortfolioItem.user_portfolio_subscription_id
                == portfolio_subscription_id
            )
        )
        items = list(items_result.scalars().all())
        assert len(items) == 3
        assert all(item.status == "removed" for item in items)
