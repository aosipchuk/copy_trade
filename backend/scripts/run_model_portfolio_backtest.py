import argparse
import asyncio

from sqlalchemy import select

from app.core.database import get_db_session
from app.models.portfolio import ModelPortfolio, ModelPortfolioVersion
from app.services.portfolio.backtest import (
    BacktestAssumptions,
    run_model_portfolio_backtest,
)


async def _get_version_id(
    portfolio_slug: str,
    version_no: int | None,
) -> int:
    async with get_db_session() as db:
        stmt = (
            select(ModelPortfolioVersion.id)
            .join(
                ModelPortfolio, ModelPortfolio.id == ModelPortfolioVersion.portfolio_id
            )
            .where(ModelPortfolio.slug == portfolio_slug)
        )
        if version_no is None:
            stmt = stmt.where(
                ModelPortfolioVersion.status == "published",
                ModelPortfolioVersion.valid_to.is_(None),
            )
        else:
            stmt = stmt.where(ModelPortfolioVersion.version_no == version_no)

        result = await db.execute(stmt)
        resolved_version_id = result.scalar_one_or_none()
        if resolved_version_id is None:
            raise LookupError(
                "Portfolio version not found: "
                f"slug={portfolio_slug} version_no={version_no or 'current_published'}"
            )
        return int(resolved_version_id)


async def main_async(
    portfolio_slug: str,
    version_no: int | None,
    period_days: int,
    initial_equity_values: list[float],
    replace_existing: bool,
) -> int:
    version_id = await _get_version_id(portfolio_slug, version_no)
    async with get_db_session() as db:
        for initial_equity_usd in initial_equity_values:
            backtest = await run_model_portfolio_backtest(
                db,
                version_id=version_id,
                assumptions=BacktestAssumptions(
                    period_days=period_days,
                    initial_equity_usd=initial_equity_usd,
                ),
                replace_existing=replace_existing,
            )
            print(
                "portfolio backtest saved: "
                f"id={backtest.id} version_id={version_id} "
                f"period_days={backtest.period_days} "
                f"initial_equity_usd={backtest.initial_equity_usd} "
                f"total_return_pct={backtest.total_return_pct} "
                f"data_source={backtest.assumptions_json.get('data_source')}"
            )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run and save model portfolio backtests."
    )
    parser.add_argument(
        "--portfolio-slug",
        default="balanced",
        help="Model portfolio slug.",
    )
    parser.add_argument(
        "--version-no",
        type=int,
        default=None,
        help="Version number. Defaults to current published version.",
    )
    parser.add_argument(
        "--period-days",
        type=int,
        default=180,
        help="Backtest window length.",
    )
    parser.add_argument(
        "--initial-equity-usd",
        type=float,
        action="append",
        default=None,
        help=(
            "Initial equity to simulate. Repeat for multiple values. "
            "Defaults to 1000, 5000, and 10000."
        ),
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not replace existing backtests for the same version/window/equity.",
    )
    args = parser.parse_args()
    initial_equity_values = args.initial_equity_usd or [1000.0, 5000.0, 10000.0]
    raise SystemExit(
        asyncio.run(
            main_async(
                args.portfolio_slug,
                args.version_no,
                args.period_days,
                initial_equity_values,
                replace_existing=not args.keep_existing,
            )
        )
    )


if __name__ == "__main__":
    main()
