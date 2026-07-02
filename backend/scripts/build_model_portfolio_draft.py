import argparse
import asyncio

from app.core.database import get_db_session
from app.services.portfolio.publisher import build_draft_model_portfolio


async def main_async(
    portfolio_slug: str,
    period: str,
    created_by: int | None,
    internal_alpha_relaxed: bool,
) -> int:
    async with get_db_session() as db:
        result = await build_draft_model_portfolio(
            db,
            portfolio_slug=portfolio_slug,
            period=period,
            created_by=created_by,
            internal_alpha_relaxed=internal_alpha_relaxed,
        )
        version = result.version
        print(
            "draft portfolio version created: "
            f"id={version.id} portfolio_id={version.portfolio_id} "
            f"version_no={version.version_no} status={version.status}"
        )
        print(
            "optimizer summary: "
            f"trader_count={result.optimization.summary['trader_count']} "
            f"weight_sum={result.optimization.summary['target_weight_sum_pct']} "
            f"filtered_rejected={len(result.candidate_selection.rejected)} "
            f"optimizer_rejected={len(result.optimization.rejected)}"
        )
        if internal_alpha_relaxed:
            print(
                "builder mode: internal_alpha_relaxed "
                "(draft only; requires manual review before publish)"
            )
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a draft model portfolio.")
    parser.add_argument(
        "--portfolio-slug",
        default="balanced",
        help="Model portfolio slug to build.",
    )
    parser.add_argument(
        "--period",
        default="allTime",
        help="TraderStat period used for candidate metrics.",
    )
    parser.add_argument(
        "--created-by",
        type=int,
        default=None,
        help="Optional admin user id recorded as version.created_by.",
    )
    parser.add_argument(
        "--internal-alpha-relaxed",
        action="store_true",
        help=(
            "Relax sparse-data gates for an internal draft only. "
            "Default Balanced methodology remains strict."
        ),
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            main_async(
                args.portfolio_slug,
                args.period,
                args.created_by,
                args.internal_alpha_relaxed,
            )
        )
    )


if __name__ == "__main__":
    main()
