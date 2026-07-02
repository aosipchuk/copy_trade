"""Integration tests for model portfolio billing gate endpoints."""

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

from app.core.config import settings
from app.models.portfolio import (
    ModelPortfolio,
    ModelPortfolioAllocation,
    ModelPortfolioVersion,
    UserPortfolioItem,
    UserPortfolioSubscription,
)
from app.models.subscription import Subscription
from app.models.trader import Trader
from app.services.portfolio.billing import (
    BillingPaymentRequiredError,
    require_portfolio_rebalance_billing,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

_counter: itertools.count = itertools.count(1)


@dataclass(frozen=True)
class SeededPortfolio:
    portfolio_id: int
    version_id: int
    slug: str


def _make_init_data(user_id: int, username: str = "portfoliobilling") -> str:
    bot_token = "123456:test"
    user_data = json.dumps(
        {"id": user_id, "username": username, "first_name": "Billing"}
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
    portfolio = ModelPortfolio(
        slug=f"balanced-billing-{index}",
        name=f"Balanced Billing {index}",
        risk_profile="balanced",
        status="active",
        description="Balanced billing test portfolio.",
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
        approval_note="Approved for billing API test.",
        summary_json={"trader_count": 2, "target_weight_sum_pct": 100.0},
    )
    db_session.add(version)
    await db_session.flush()

    traders: list[Trader] = []
    for offset in range(2):
        trader = Trader(
            hl_address=f"0xpb{index:04x}{offset:034x}",
            display_name=f"Billing Trader {index}-{offset}",
            is_active=True,
            has_perp_activity=True,
        )
        db_session.add(trader)
        traders.append(trader)
    await db_session.flush()

    for trader, weight in zip(
        traders, [Decimal("60.000"), Decimal("40.000")], strict=True
    ):
        db_session.add(
            ModelPortfolioAllocation(
                version_id=version.id,
                trader_id=trader.id,
                target_weight_pct=weight,
                copy_ratio_pct=Decimal("100.00"),
                max_leverage=Decimal("8.00"),
                stop_loss_pct=Decimal("20.00"),
                sizing_mode="fixed_ratio",
                reason_code="balanced_mvp_score",
                reason_text="Selected by deterministic Balanced MVP methodology.",
                score_snapshot={
                    "portfolio_score": 80,
                    "source_metrics": {"max_drawdown_pct": 10},
                },
                constraint_snapshot={"selection_rank": 1},
            )
        )
    await db_session.commit()
    return SeededPortfolio(
        portfolio_id=portfolio.id,
        version_id=version.id,
        slug=portfolio.slug,
    )


def _checkout_body(seed: SeededPortfolio) -> dict[str, object]:
    return {
        "portfolio_id": seed.portfolio_id,
        "active_version_id": seed.version_id,
        "total_allocation_usd": 1000,
    }


def _live_activation_body(seed: SeededPortfolio) -> dict[str, object]:
    return {
        "portfolio_id": seed.portfolio_id,
        "active_version_id": seed.version_id,
        "is_demo": False,
        "auto_rebalance": False,
        "total_allocation_usd": 1000,
        "close_removed_positions": False,
        "risk_disclosure_accepted": True,
    }


def _stripe_signature(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    signed_payload = f"{ts}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


class TestPortfolioBillingGate:
    async def test_checkout_creates_live_billing_holder_without_generated_items(
        self, client, db_session, monkeypatch
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, _ = await _auth_user(client, user_id=92001)
        monkeypatch.setattr(settings, "stripe_api_key", "")
        monkeypatch.setattr(settings, "stripe_portfolio_price_id", "")

        response = await client.post(
            "/api/portfolio-subscriptions/billing/checkout",
            json=_checkout_body(seed),
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "stripe"
        assert body["provider_configured"] is False
        assert body["checkout_url"] is None
        assert body["portfolio_subscription"]["is_demo"] is False
        assert body["portfolio_subscription"]["status"] == "paused"
        assert body["portfolio_subscription"]["items"] == []
        assert body["billing_status"]["paid"] is False

        items_result = await db_session.execute(
            select(UserPortfolioItem).where(
                UserPortfolioItem.user_portfolio_subscription_id
                == body["portfolio_subscription"]["id"]
            )
        )
        assert list(items_result.scalars().all()) == []

    async def test_webhook_signature_updates_billing_status(
        self, client, db_session, monkeypatch
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, _ = await _auth_user(client, user_id=92002)
        monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")

        checkout = await client.post(
            "/api/portfolio-subscriptions/billing/checkout",
            json=_checkout_body(seed),
            headers=headers,
        )
        assert checkout.status_code == 200
        local_id = checkout.json()["portfolio_subscription"]["id"]
        payload = json.dumps(
            {
                "id": "evt_checkout_completed",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "client_reference_id": str(local_id),
                        "customer": "cus_test",
                        "subscription": "sub_test",
                        "metadata": {"user_portfolio_subscription_id": str(local_id)},
                    }
                },
            },
            separators=(",", ":"),
        ).encode()

        invalid = await client.post(
            "/api/portfolio-subscriptions/billing/webhook",
            content=payload,
            headers={"Stripe-Signature": "t=1,v1=bad"},
        )
        assert invalid.status_code == 400

        valid = await client.post(
            "/api/portfolio-subscriptions/billing/webhook",
            content=payload,
            headers={"Stripe-Signature": _stripe_signature(payload, "whsec_test")},
        )

        assert valid.status_code == 200
        assert valid.json()["updated_subscription_id"] == local_id
        result = await db_session.execute(
            select(UserPortfolioSubscription).where(
                UserPortfolioSubscription.id == local_id
            )
        )
        subscription = result.scalar_one()
        assert subscription.status == "active"
        assert subscription.billing_customer_id == "cus_test"
        assert subscription.billing_subscription_id == "sub_test"

    async def test_active_payment_passes_live_billing_gate(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=92003)
        db_session.add(
            UserPortfolioSubscription(
                user_id=user_id,
                portfolio_id=seed.portfolio_id,
                active_version_id=seed.version_id,
                status="active",
                is_demo=False,
                auto_rebalance=False,
                total_allocation_usd=Decimal("1000.00"),
                close_removed_positions=False,
                billing_provider="stripe",
                billing_customer_id="cus_paid",
                billing_subscription_id="sub_paid",
            )
        )
        await db_session.commit()

        response = await client.post(
            "/api/portfolio-subscriptions",
            json=_live_activation_body(seed),
            headers=headers,
        )

        assert response.status_code == 400
        assert "HL wallet address required" in response.json()["detail"]

    async def test_past_due_blocks_live_activation(self, client, db_session) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=92004)
        db_session.add(
            UserPortfolioSubscription(
                user_id=user_id,
                portfolio_id=seed.portfolio_id,
                active_version_id=seed.version_id,
                status="past_due",
                is_demo=False,
                auto_rebalance=False,
                total_allocation_usd=Decimal("1000.00"),
                close_removed_positions=False,
                billing_provider="stripe",
                billing_customer_id="cus_past_due",
                billing_subscription_id="sub_past_due",
            )
        )
        await db_session.commit()

        response = await client.post(
            "/api/portfolio-subscriptions",
            json=_live_activation_body(seed),
            headers=headers,
        )

        assert response.status_code == 402
        assert "past_due" in response.json()["detail"]

    async def test_canceled_keeps_history_but_blocks_rebalance(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        _, user_id = await _auth_user(client, user_id=92005)
        canceled = UserPortfolioSubscription(
            user_id=user_id,
            portfolio_id=seed.portfolio_id,
            active_version_id=seed.version_id,
            status="canceled",
            is_demo=False,
            auto_rebalance=True,
            total_allocation_usd=Decimal("1000.00"),
            close_removed_positions=False,
            billing_provider="stripe",
            billing_customer_id="cus_canceled",
            billing_subscription_id="sub_canceled",
        )
        db_session.add(canceled)
        await db_session.commit()

        with pytest.raises(BillingPaymentRequiredError, match="canceled"):
            await require_portfolio_rebalance_billing(db_session, canceled.id)

        result = await db_session.execute(
            select(UserPortfolioSubscription).where(
                UserPortfolioSubscription.id == canceled.id
            )
        )
        saved = result.scalar_one()
        assert saved.billing_subscription_id == "sub_canceled"
        assert saved.status == "canceled"

    async def test_deleted_webhook_deactivates_generated_live_subscriptions(
        self, client, db_session, monkeypatch
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        _, user_id = await _auth_user(client, user_id=92006)
        monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
        portfolio_subscription = UserPortfolioSubscription(
            user_id=user_id,
            portfolio_id=seed.portfolio_id,
            active_version_id=seed.version_id,
            status="active",
            is_demo=False,
            auto_rebalance=True,
            total_allocation_usd=Decimal("1000.00"),
            close_removed_positions=False,
            billing_provider="stripe",
            billing_customer_id="cus_cancel",
            billing_subscription_id="sub_cancel",
        )
        db_session.add(portfolio_subscription)
        await db_session.flush()
        portfolio_subscription_id = portfolio_subscription.id

        allocation_result = await db_session.execute(
            select(ModelPortfolioAllocation).where(
                ModelPortfolioAllocation.version_id == seed.version_id
            )
        )
        allocations = list(allocation_result.scalars().all())
        for allocation in allocations:
            subscription = Subscription(
                user_id=user_id,
                trader_id=allocation.trader_id,
                max_allocation_usd=Decimal("100.00"),
                copy_ratio_pct=Decimal("100.00"),
                stop_loss_pct=Decimal("20.00"),
                max_leverage=Decimal("8.00"),
                source_type="model_portfolio",
                source_id=portfolio_subscription_id,
                source_version_id=seed.version_id,
                managed_by_portfolio=True,
                is_active=True,
                is_demo=False,
            )
            db_session.add(subscription)
            await db_session.flush()
            db_session.add(
                UserPortfolioItem(
                    user_portfolio_subscription_id=portfolio_subscription_id,
                    subscription_id=subscription.id,
                    portfolio_version_id=seed.version_id,
                    allocation_id=allocation.id,
                    trader_id=allocation.trader_id,
                    target_allocation_usd=Decimal("100.00"),
                    target_weight_pct=allocation.target_weight_pct,
                    status="active",
                )
            )
        await db_session.commit()

        payload = json.dumps(
            {
                "id": "evt_subscription_deleted",
                "type": "customer.subscription.deleted",
                "data": {
                    "object": {
                        "id": "sub_cancel",
                        "customer": "cus_cancel",
                        "metadata": {
                            "user_portfolio_subscription_id": str(
                                portfolio_subscription_id
                            )
                        },
                    }
                },
            },
            separators=(",", ":"),
        ).encode()

        response = await client.post(
            "/api/portfolio-subscriptions/billing/webhook",
            content=payload,
            headers={"Stripe-Signature": _stripe_signature(payload, "whsec_test")},
        )

        assert response.status_code == 200
        db_session.expire_all()
        saved = await db_session.get(
            UserPortfolioSubscription, portfolio_subscription_id
        )
        assert saved is not None
        assert saved.status == "canceled"
        assert saved.canceled_at is not None

        generated_result = await db_session.execute(
            select(Subscription).where(
                Subscription.source_id == portfolio_subscription_id,
                Subscription.source_type == "model_portfolio",
            )
        )
        assert all(
            subscription.is_active is False
            for subscription in generated_result.scalars().all()
        )

        items_result = await db_session.execute(
            select(UserPortfolioItem).where(
                UserPortfolioItem.user_portfolio_subscription_id
                == portfolio_subscription_id
            )
        )
        assert all(item.status == "removed" for item in items_result.scalars().all())
