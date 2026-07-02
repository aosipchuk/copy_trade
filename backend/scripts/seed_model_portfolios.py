import argparse
import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.core.database import get_db_session
from app.models.portfolio import ModelPortfolio

BALANCED_PORTFOLIO = {
    "slug": "balanced",
    "name": "Balanced",
    "risk_profile": "balanced",
    "status": "active",
    "description": (
        "Balanced model portfolio for copy trading. The methodology uses trader "
        "quality metrics, risk filters, diversification limits, and manual approval. "
        "Backtests and historical results do not guarantee future returns."
    ),
    "methodology_version": "balanced-mvp-v1",
    "rebalance_cadence": "weekly",
    "min_equity_usd": Decimal("1000.00"),
    "monthly_price_usd": Decimal("19.00"),
    "trial_days": 7,
}


async def seed_balanced_portfolio() -> int:
    async with get_db_session() as db:
        result = await db.execute(
            select(ModelPortfolio).where(
                ModelPortfolio.slug == BALANCED_PORTFOLIO["slug"]
            )
        )
        portfolio = result.scalar_one_or_none()
        action = "updated"

        if portfolio is None:
            portfolio = ModelPortfolio(**BALANCED_PORTFOLIO)
            db.add(portfolio)
            action = "created"
        else:
            for key, value in BALANCED_PORTFOLIO.items():
                setattr(portfolio, key, value)

        await db.flush()
        print(f"balanced portfolio {action}: id={portfolio.id} slug={portfolio.slug}")
        return portfolio.id


async def check_balanced_portfolio() -> int:
    async with get_db_session() as db:
        result = await db.execute(
            select(ModelPortfolio).where(
                ModelPortfolio.slug == BALANCED_PORTFOLIO["slug"]
            )
        )
        portfolio = result.scalar_one_or_none()
        if portfolio is None:
            print("balanced portfolio missing")
            return 1

        print(f"balanced portfolio found: id={portfolio.id} status={portfolio.status}")
        return 0


async def main_async(check: bool) -> int:
    if check:
        return await check_balanced_portfolio()
    await seed_balanced_portfolio()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed model portfolio templates.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that the Balanced template exists without mutating data.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args.check)))


if __name__ == "__main__":
    main()
