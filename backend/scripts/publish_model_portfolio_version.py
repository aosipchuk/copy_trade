import argparse
import asyncio

from app.core.database import get_db_session
from app.services.portfolio.publisher import publish_model_portfolio_version


async def main_async(
    portfolio_slug: str,
    version_no: int,
    approved_by: int | None,
    approval_note: str | None,
    allow_internal_alpha_relaxed: bool,
) -> int:
    async with get_db_session() as db:
        version = await publish_model_portfolio_version(
            db,
            portfolio_slug=portfolio_slug,
            version_no=version_no,
            approved_by=approved_by,
            approval_note=approval_note,
            allow_internal_alpha_relaxed=allow_internal_alpha_relaxed,
        )
        print(
            "portfolio version published: "
            f"id={version.id} portfolio_id={version.portfolio_id} "
            f"version_no={version.version_no} status={version.status} "
            f"valid_from={version.valid_from}"
        )
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish a reviewed draft model portfolio version."
    )
    parser.add_argument(
        "--portfolio-slug",
        default="balanced",
        help="Model portfolio slug.",
    )
    parser.add_argument(
        "--version-no",
        type=int,
        required=True,
        help="Reviewed draft version number to publish.",
    )
    parser.add_argument(
        "--approved-by",
        type=int,
        default=None,
        help="Optional admin user id recorded as approved_by.",
    )
    parser.add_argument(
        "--approval-note",
        default=None,
        help="Optional approval note stored on the version.",
    )
    parser.add_argument(
        "--allow-internal-alpha-relaxed",
        action="store_true",
        help=(
            "Allow publishing an internal_alpha_relaxed draft after manual review. "
            "This is refused by default."
        ),
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            main_async(
                args.portfolio_slug,
                args.version_no,
                args.approved_by,
                args.approval_note,
                args.allow_internal_alpha_relaxed,
            )
        )
    )


if __name__ == "__main__":
    main()
