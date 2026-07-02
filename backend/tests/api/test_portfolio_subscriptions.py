"""Integration tests for demo model portfolio activation endpoints."""

import hashlib
import hmac
import itertools
import json
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from sqlalchemy import select, update

from app.models.portfolio import (
    ModelPortfolio,
    ModelPortfolioAllocation,
    ModelPortfolioVersion,
    PortfolioRebalanceEvent,
    UserPortfolioItem,
    UserPortfolioSubscription,
)
from app.models.signal import Signal
from app.models.subscription import Subscription
from app.models.trader import Trader
from app.models.user import User, UserAgent
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import MarginSummary
from app.services.portfolio.subscription_lifecycle import (
    executable_subscription_targets_for_signal,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

_counter: itertools.count = itertools.count(1)
_FAKE_HL_ADDRESS = "0x" + "de" * 20
_RICH_MARGIN = MarginSummary(
    accountValue=Decimal("50000"),
    totalMarginUsed=Decimal("0"),
    totalRawUsd=Decimal("50000"),
)
_LOW_MARGIN = MarginSummary(
    accountValue=Decimal("100"),
    totalMarginUsed=Decimal("90"),
    totalRawUsd=Decimal("100"),
)


@pytest.fixture(autouse=True)
def _mock_hl_margin():
    with patch.object(
        HyperliquidInfoClient,
        "get_account_summary",
        AsyncMock(return_value=_RICH_MARGIN),
    ):
        yield


@dataclass(frozen=True)
class SeededPortfolio:
    portfolio_id: int
    version_id: int
    slug: str
    trader_ids: list[int]


@dataclass(frozen=True)
class RebalanceTarget:
    version_id: int
    added_trader_id: int
    removed_trader_id: int


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


def _serialized_identity_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


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


async def _publish_rebalance_target_version(
    db_session,
    seed: SeededPortfolio,
) -> RebalanceTarget:
    index = next(_counter)
    now = datetime(2026, 7, 2, 13, 0, 0)
    current_version = await db_session.get(ModelPortfolioVersion, seed.version_id)
    assert current_version is not None
    current_version.status = "retired"
    current_version.valid_to = now

    version = ModelPortfolioVersion(
        portfolio_id=seed.portfolio_id,
        version_no=2,
        status="published",
        valid_from=now,
        approved_at=now,
        approval_note="Approved rebalance target for API test.",
        summary_json={
            "trader_count": 3,
            "target_weight_sum_pct": 100.0,
        },
    )
    db_session.add(version)
    await db_session.flush()

    added_trader = Trader(
        hl_address=f"0xrb{index:04x}{0:034x}",
        display_name=f"Rebalance Added Trader {index}",
        is_active=True,
        has_perp_activity=True,
    )
    db_session.add(added_trader)
    await db_session.flush()

    allocations = [
        (seed.trader_ids[0], Decimal("60.000"), Decimal("80.00")),
        (seed.trader_ids[1], Decimal("20.000"), Decimal("100.00")),
        (added_trader.id, Decimal("20.000"), Decimal("100.00")),
    ]
    for offset, (trader_id, weight, copy_ratio) in enumerate(allocations):
        db_session.add(
            ModelPortfolioAllocation(
                version_id=version.id,
                trader_id=trader_id,
                target_weight_pct=weight,
                copy_ratio_pct=copy_ratio,
                max_leverage=Decimal("8.00"),
                stop_loss_pct=Decimal("20.00"),
                sizing_mode="fixed_ratio",
                reason_code="rebalance_test",
                reason_text="Selected by rebalance API test target version.",
                score_snapshot={"portfolio_score": 90 - offset},
                constraint_snapshot={"selection_rank": offset + 1},
            )
        )
    await db_session.commit()
    return RebalanceTarget(
        version_id=version.id,
        added_trader_id=added_trader.id,
        removed_trader_id=seed.trader_ids[2],
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


def _live_activation_body(
    seed: SeededPortfolio,
    total: float = 1000.0,
    *,
    risk_disclosure_accepted: bool = True,
) -> dict[str, object]:
    return {
        "portfolio_id": seed.portfolio_id,
        "active_version_id": seed.version_id,
        "is_demo": False,
        "auto_rebalance": False,
        "total_allocation_usd": total,
        "close_removed_positions": False,
        "risk_disclosure_accepted": risk_disclosure_accepted,
    }


async def _make_user_live_ready(db_session, user_id: int) -> None:
    await db_session.execute(
        update(User).where(User.id == user_id).values(hl_address=_FAKE_HL_ADDRESS)
    )
    db_session.add(
        UserAgent(
            user_id=user_id,
            agent_address="0x" + "ef" * 20,
            agent_key_enc=b"encrypted-test-key",
            approved_at=datetime(2026, 7, 2, 12, 30, 0),
            is_active=True,
        )
    )
    await db_session.commit()


async def _seed_paid_holder(
    db_session,
    seed: SeededPortfolio,
    user_id: int,
    *,
    status: str = "active",
) -> UserPortfolioSubscription:
    subscription = UserPortfolioSubscription(
        user_id=user_id,
        portfolio_id=seed.portfolio_id,
        active_version_id=seed.version_id,
        status=status,
        is_demo=False,
        auto_rebalance=False,
        total_allocation_usd=Decimal("1000.00"),
        close_removed_positions=False,
        billing_provider="stripe",
        billing_customer_id=f"cus_live_{user_id}",
        billing_subscription_id=f"sub_live_{user_id}",
    )
    db_session.add(subscription)
    await db_session.commit()
    return subscription


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
        assert body["trader_details_visible"] is False
        assert len(body["items"]) == 3
        assert sum(item["target_allocation_usd"] for item in body["items"]) == 1000.0
        assert [item["target_allocation_usd"] for item in body["items"]] == [
            500.0,
            300.0,
            200.0,
        ]

        for item in body["items"]:
            subscription = item["subscription"]
            assert item["trader_id"] is None
            assert item["trader_address"] is None
            assert item["trader_display_name"] is None
            assert subscription["trader_id"] is None
            assert subscription["trader_address"] is None
            assert subscription["trader_name"] is None
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

    async def test_execution_targets_keep_manual_and_portfolio_demo_separate(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91005)
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
        generated_result = await db_session.execute(
            select(Subscription.id).where(
                Subscription.source_id == portfolio_subscription_id,
                Subscription.source_type == "model_portfolio",
                Subscription.trader_id == seed.trader_ids[0],
                Subscription.is_demo.is_(True),
            )
        )
        generated_subscription_id = generated_result.scalar_one()
        signal = Signal(
            trader_id=seed.trader_ids[0],
            signal_type="OPEN",
            coin="BTC",
            side="long",
            size=Decimal("0.01"),
            entry_price=Decimal("50000.00"),
            leverage=Decimal("3.00"),
        )
        db_session.add(signal)
        await db_session.commit()

        active_targets = await executable_subscription_targets_for_signal(
            db_session, signal.id
        )

        assert {
            target.subscription_id for target in active_targets if target.is_demo
        } == {manual.id, generated_subscription_id}

        portfolio_subscription = await db_session.get(
            UserPortfolioSubscription, portfolio_subscription_id
        )
        assert portfolio_subscription is not None
        portfolio_subscription.status = "paused"
        await db_session.commit()

        paused_targets = await executable_subscription_targets_for_signal(
            db_session, signal.id
        )

        assert {
            target.subscription_id for target in paused_targets if target.is_demo
        } == {manual.id}


class TestPortfolioRebalance:
    async def test_preview_rebalance_shows_diff_for_pending_manual_apply(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, _ = await _auth_user(client, user_id=91201)
        activation = await client.post(
            "/api/portfolio-subscriptions",
            json=_activation_body(seed),
            headers=headers,
        )
        assert activation.status_code == 201
        portfolio_subscription_id = activation.json()["id"]
        target = await _publish_rebalance_target_version(db_session, seed)

        response = await client.post(
            f"/api/portfolio-subscriptions/{portfolio_subscription_id}"
            "/preview-rebalance",
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending"
        assert body["can_apply"] is True
        assert body["auto_rebalance"] is False
        assert body["from_version_id"] == seed.version_id
        assert body["to_version_id"] == target.version_id
        actions = {item["action"] for item in body["diff"]}
        assert {
            "add_trader",
            "remove_trader",
            "change_weight",
            "change_risk_settings",
        } <= actions
        assert not _contains_identity(body["diff"])
        diff_text = _serialized_identity_text(body["diff"])
        assert "0xpf" not in diff_text
        assert "Portfolio Trader" not in diff_text

    async def test_apply_rebalance_is_idempotent_and_manual_subscription_untouched(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91202)
        manual = Subscription(
            user_id=user_id,
            trader_id=seed.trader_ids[2],
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
        await _publish_rebalance_target_version(db_session, seed)

        first = await client.post(
            f"/api/portfolio-subscriptions/{portfolio_subscription_id}"
            "/apply-rebalance",
            headers=headers,
        )
        second = await client.post(
            f"/api/portfolio-subscriptions/{portfolio_subscription_id}"
            "/apply-rebalance",
            headers=headers,
        )

        assert first.status_code == 200
        assert second.status_code == 200
        first_body = first.json()
        second_body = second.json()
        assert first_body["event"]["status"] == "completed"
        assert second_body["event"]["id"] == first_body["event"]["id"]
        assert second_body["event"]["status"] == "completed"
        assert first_body["portfolio_subscription"]["active_version_no"] == 2
        assert first_body["portfolio_subscription"]["trader_details_visible"] is False
        assert not _contains_identity(first_body["diff"])
        assert not _contains_identity(first_body["event"]["diff_json"])
        event_text = _serialized_identity_text(first_body["event"]["diff_json"])
        assert "0xpf" not in event_text
        assert "Portfolio Trader" not in event_text

        subscriptions_result = await db_session.execute(
            select(Subscription).where(
                Subscription.source_id == portfolio_subscription_id,
                Subscription.source_type == "model_portfolio",
            )
        )
        generated = list(subscriptions_result.scalars().all())
        assert len(generated) == 4
        active_generated = [
            subscription for subscription in generated if subscription.is_active
        ]
        assert len(active_generated) == 3
        removed_generated = [
            subscription
            for subscription in generated
            if subscription.trader_id == seed.trader_ids[2]
        ]
        assert len(removed_generated) == 1
        assert removed_generated[0].is_active is False

        manual_result = await db_session.execute(
            select(Subscription).where(Subscription.id == manual.id)
        )
        manual_after_apply = manual_result.scalar_one()
        assert manual_after_apply.is_active is True
        assert manual_after_apply.source_type == "manual"
        assert manual_after_apply.managed_by_portfolio is False

        event_result = await db_session.execute(
            select(PortfolioRebalanceEvent).where(
                PortfolioRebalanceEvent.user_portfolio_subscription_id
                == portfolio_subscription_id
            )
        )
        assert len(list(event_result.scalars().all())) == 1

        history = await client.get(
            f"/api/portfolio-subscriptions/{portfolio_subscription_id}"
            "/rebalance-history",
            headers=headers,
        )
        assert history.status_code == 200
        history_body = history.json()
        assert len(history_body) == 1
        assert not _contains_identity(history_body[0]["diff_json"])

    async def test_live_past_due_rebalance_is_skipped(self, client, db_session) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91203)
        paid_holder = await _seed_paid_holder(db_session, seed, user_id)
        await _make_user_live_ready(db_session, user_id)
        activation = await client.post(
            "/api/portfolio-subscriptions",
            json=_live_activation_body(seed),
            headers=headers,
        )
        assert activation.status_code == 201
        paid_holder.status = "past_due"
        await db_session.commit()
        await _publish_rebalance_target_version(db_session, seed)

        response = await client.post(
            f"/api/portfolio-subscriptions/{paid_holder.id}/apply-rebalance",
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "blocked"
        assert body["can_apply"] is False
        assert body["event"]["status"] == "skipped"
        assert "past_due" in body["event"]["error_msg"]
        assert body["portfolio_subscription"]["active_version_no"] == 1
        assert any(item["action"] == "blocked_by_payment" for item in body["diff"])

    async def test_update_rebalance_preferences(self, client, db_session) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, _ = await _auth_user(client, user_id=91204)
        activation = await client.post(
            "/api/portfolio-subscriptions",
            json=_activation_body(seed),
            headers=headers,
        )
        assert activation.status_code == 201
        portfolio_subscription_id = activation.json()["id"]

        response = await client.patch(
            f"/api/portfolio-subscriptions/{portfolio_subscription_id}",
            json={"auto_rebalance": True, "close_removed_positions": True},
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["auto_rebalance"] is True
        assert body["close_removed_positions"] is True


class TestLivePortfolioActivation:
    async def test_live_activation_requires_payment(self, client, db_session) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91101)
        await _make_user_live_ready(db_session, user_id)

        response = await client.post(
            "/api/portfolio-subscriptions",
            json=_live_activation_body(seed),
            headers=headers,
        )

        assert response.status_code == 402
        assert "Payment is required" in response.json()["detail"]

    async def test_live_activation_requires_risk_disclosure(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91102)
        await _seed_paid_holder(db_session, seed, user_id)

        response = await client.post(
            "/api/portfolio-subscriptions",
            json=_live_activation_body(seed, risk_disclosure_accepted=False),
            headers=headers,
        )

        assert response.status_code == 400
        assert "Risk disclosure" in response.json()["detail"]

    async def test_live_activation_requires_wallet_address(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91103)
        await _seed_paid_holder(db_session, seed, user_id)

        response = await client.post(
            "/api/portfolio-subscriptions",
            json=_live_activation_body(seed),
            headers=headers,
        )

        assert response.status_code == 400
        assert "HL wallet address required" in response.json()["detail"]

    async def test_live_activation_requires_active_agent(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91104)
        await _seed_paid_holder(db_session, seed, user_id)
        await db_session.execute(
            update(User).where(User.id == user_id).values(hl_address=_FAKE_HL_ADDRESS)
        )
        await db_session.commit()

        response = await client.post(
            "/api/portfolio-subscriptions",
            json=_live_activation_body(seed),
            headers=headers,
        )

        assert response.status_code == 400
        assert "Active Hyperliquid agent required" in response.json()["detail"]

    async def test_live_activation_blocks_insufficient_margin(
        self, client, db_session, monkeypatch
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91105)
        paid_holder = await _seed_paid_holder(db_session, seed, user_id)
        await _make_user_live_ready(db_session, user_id)
        monkeypatch.setattr(
            HyperliquidInfoClient,
            "get_account_summary",
            AsyncMock(return_value=_LOW_MARGIN),
        )

        response = await client.post(
            "/api/portfolio-subscriptions",
            json=_live_activation_body(seed),
            headers=headers,
        )

        assert response.status_code == 400
        assert "Insufficient free margin" in response.json()["detail"]

        items_result = await db_session.execute(
            select(UserPortfolioItem).where(
                UserPortfolioItem.user_portfolio_subscription_id == paid_holder.id
            )
        )
        assert list(items_result.scalars().all()) == []

    async def test_live_activation_creates_portfolio_owned_subscriptions(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91106)
        paid_holder = await _seed_paid_holder(db_session, seed, user_id)
        await _make_user_live_ready(db_session, user_id)

        response = await client.post(
            "/api/portfolio-subscriptions",
            json=_live_activation_body(seed),
            headers=headers,
        )

        assert response.status_code == 201
        body = response.json()
        assert body["created"] is True
        assert body["id"] == paid_holder.id
        assert body["is_demo"] is False
        assert body["status"] == "active"
        assert len(body["items"]) == 3

        for item in body["items"]:
            subscription = item["subscription"]
            assert subscription["is_demo"] is False
            assert subscription["source_type"] == "model_portfolio"
            assert subscription["source_id"] == body["id"]
            assert subscription["source_version_id"] == seed.version_id
            assert subscription["managed_by_portfolio"] is True

        second = await client.post(
            "/api/portfolio-subscriptions",
            json=_live_activation_body(seed),
            headers=headers,
        )

        assert second.status_code == 200
        assert second.json()["created"] is False
        assert second.json()["id"] == body["id"]

        subs_result = await db_session.execute(
            select(Subscription).where(
                Subscription.source_id == body["id"],
                Subscription.source_type == "model_portfolio",
                Subscription.is_demo.is_(False),
            )
        )
        generated_subscriptions = list(subs_result.scalars().all())
        assert len(generated_subscriptions) == 3

    async def test_live_activation_blocks_manual_conflict(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91107)
        await _seed_paid_holder(db_session, seed, user_id)
        await _make_user_live_ready(db_session, user_id)
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
            json=_live_activation_body(seed),
            headers=headers,
        )

        assert response.status_code == 400
        assert "manual subscription conflicts" in response.json()["detail"]

    async def test_cancel_live_portfolio_disables_only_generated_subscriptions(
        self, client, db_session
    ) -> None:
        seed = await _seed_published_portfolio(db_session)
        headers, user_id = await _auth_user(client, user_id=91108)
        paid_holder = await _seed_paid_holder(db_session, seed, user_id)
        await _make_user_live_ready(db_session, user_id)
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
            json=_live_activation_body(seed),
            headers=headers,
        )
        assert activation.status_code == 201

        response = await client.delete(
            f"/api/portfolio-subscriptions/{paid_holder.id}",
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "canceled"
        assert body["canceled_at"] is not None
        assert all(item["status"] == "removed" for item in body["items"])
        assert all(item["subscription"]["is_active"] is False for item in body["items"])

        manual_after_cancel = await db_session.get(Subscription, manual.id)
        assert manual_after_cancel is not None
        assert manual_after_cancel.is_active is True
        assert manual_after_cancel.source_type == "manual"
