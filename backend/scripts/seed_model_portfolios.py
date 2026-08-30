import argparse
import asyncio
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core.database import get_db_session
from app.models.portfolio import ModelPortfolio

JsonDict = dict[str, Any]

STARTER_PORTFOLIO: JsonDict = {
    "slug": "starter",
    "name": "Starter",
    "risk_profile": "balanced",
    "status": "active",
    "live_enabled": False,
    "description": (
        "Copyability-first portfolio for smaller accounts. It favors liquid, "
        "lower-frequency traders to reduce tiny orders, missed fills, and slippage. "
        "Backtests and historical results do not guarantee future returns."
    ),
    "methodology_version": "starter-copyable-v1",
    "rebalance_cadence": "weekly",
    "min_equity_usd": Decimal("500.00"),
    "monthly_price_usd": Decimal("19.00"),
    "trial_days": 7,
}

CONSERVATIVE_PORTFOLIO: JsonDict = {
    "slug": "conservative",
    "name": "Conservative",
    "risk_profile": "conservative",
    "status": "active",
    "live_enabled": False,
    "description": (
        "Lower-risk portfolio focused on drawdown control, lower leverage, longer "
        "trading histories, and stronger diversification. Backtests and historical "
        "results do not guarantee future returns."
    ),
    "methodology_version": "conservative-v1",
    "rebalance_cadence": "weekly",
    "min_equity_usd": Decimal("1000.00"),
    "monthly_price_usd": Decimal("19.00"),
    "trial_days": 7,
}

BALANCED_PORTFOLIO: JsonDict = {
    "slug": "balanced",
    "name": "Balanced",
    "risk_profile": "balanced",
    "status": "active",
    "live_enabled": True,
    "description": (
        "Balanced model portfolio combining trader quality, risk controls, "
        "copyability, and diversification. Backtests and historical results do not "
        "guarantee future returns."
    ),
    "methodology_version": "balanced-mvp-v1",
    "rebalance_cadence": "weekly",
    "min_equity_usd": Decimal("1000.00"),
    "monthly_price_usd": Decimal("19.00"),
    "trial_days": 7,
}

AGGRESSIVE_PORTFOLIO: JsonDict = {
    "slug": "aggressive",
    "name": "Aggressive",
    "risk_profile": "aggressive",
    "status": "active",
    "live_enabled": False,
    "description": (
        "Higher-risk portfolio emphasizing return potential while retaining hard "
        "drawdown, leverage, and diversification limits. Backtests and historical "
        "results do not guarantee future returns."
    ),
    "methodology_version": "aggressive-v1",
    "rebalance_cadence": "weekly",
    "min_equity_usd": Decimal("2000.00"),
    "monthly_price_usd": Decimal("19.00"),
    "trial_days": 7,
}

MODEL_PORTFOLIOS: tuple[JsonDict, ...] = (
    STARTER_PORTFOLIO,
    CONSERVATIVE_PORTFOLIO,
    BALANCED_PORTFOLIO,
    AGGRESSIVE_PORTFOLIO,
)


async def _seed_portfolios(portfolios: tuple[JsonDict, ...]) -> list[int]:
    portfolio_ids: list[int] = []
    async with get_db_session() as db:
        for values in portfolios:
            result = await db.execute(
                select(ModelPortfolio).where(ModelPortfolio.slug == values["slug"])
            )
            portfolio = result.scalar_one_or_none()
            action = "updated"

            if portfolio is None:
                portfolio = ModelPortfolio(**values)
                db.add(portfolio)
                action = "created"
            else:
                for key, value in values.items():
                    setattr(portfolio, key, value)

            await db.flush()
            portfolio_ids.append(portfolio.id)
            print(
                f"model portfolio {action}: id={portfolio.id} slug={portfolio.slug}"
            )
    return portfolio_ids


async def seed_model_portfolios() -> list[int]:
    return await _seed_portfolios(MODEL_PORTFOLIOS)


async def _check_portfolios(portfolios: tuple[JsonDict, ...]) -> int:
    expected_slugs = [str(portfolio["slug"]) for portfolio in portfolios]
    async with get_db_session() as db:
        result = await db.execute(
            select(ModelPortfolio).where(ModelPortfolio.slug.in_(expected_slugs))
        )
        found = {portfolio.slug: portfolio for portfolio in result.scalars().all()}

    missing = [slug for slug in expected_slugs if slug not in found]
    if missing:
        print(f"model portfolios missing: {', '.join(missing)}")
        return 1

    for slug in expected_slugs:
        portfolio = found[slug]
        print(
            f"model portfolio found: id={portfolio.id} "
            f"slug={portfolio.slug} status={portfolio.status}"
        )
    return 0


async def check_model_portfolios() -> int:
    return await _check_portfolios(MODEL_PORTFOLIOS)


async def seed_balanced_portfolio() -> int:
    """Backward-compatible helper retained for existing operational imports."""
    portfolio_ids = await _seed_portfolios((BALANCED_PORTFOLIO,))
    return portfolio_ids[0]


async def check_balanced_portfolio() -> int:
    """Backward-compatible helper retained for existing operational imports."""
    return await _check_portfolios((BALANCED_PORTFOLIO,))


async def main_async(check: bool) -> int:
    if check:
        return await check_model_portfolios()
    await seed_model_portfolios()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed model portfolio templates.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that every model portfolio template exists without mutating data.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args.check)))


if __name__ == "__main__":
    main()
