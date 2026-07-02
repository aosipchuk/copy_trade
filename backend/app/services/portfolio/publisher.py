import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import (
    ModelPortfolio,
    ModelPortfolioAllocation,
    ModelPortfolioVersion,
)
from app.services.portfolio.candidates import load_portfolio_candidates
from app.services.portfolio.optimizer import optimize_portfolio
from app.services.portfolio.scoring import score_candidates
from app.services.portfolio.types import (
    CandidateSelectionResult,
    OptimizationResult,
    get_internal_alpha_relaxed_config,
    get_risk_profile_config,
)


@dataclass(frozen=True)
class PortfolioDraftBuildPreview:
    portfolio: ModelPortfolio
    candidate_selection: CandidateSelectionResult
    optimization: OptimizationResult


@dataclass(frozen=True)
class PortfolioDraftBuildResult:
    version: ModelPortfolioVersion
    candidate_selection: CandidateSelectionResult
    optimization: OptimizationResult


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _get_portfolio(db: AsyncSession, portfolio_slug: str) -> ModelPortfolio:
    result = await db.execute(
        select(ModelPortfolio).where(ModelPortfolio.slug == portfolio_slug)
    )
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        raise ValueError(f"Model portfolio not found: {portfolio_slug}")
    if portfolio.status == "retired":
        raise ValueError(f"Model portfolio is retired: {portfolio_slug}")
    return portfolio


async def preview_model_portfolio_draft(
    db: AsyncSession,
    portfolio_slug: str = "balanced",
    period: str = "allTime",
    internal_alpha_relaxed: bool = False,
) -> PortfolioDraftBuildPreview:
    portfolio = await _get_portfolio(db, portfolio_slug)
    config = get_risk_profile_config(portfolio.risk_profile)
    if internal_alpha_relaxed:
        config = get_internal_alpha_relaxed_config(config)
    candidate_selection = await load_portfolio_candidates(db, config, period=period)
    scored_candidates = score_candidates(candidate_selection.eligible)
    optimization = optimize_portfolio(scored_candidates, config)
    return PortfolioDraftBuildPreview(portfolio, candidate_selection, optimization)


async def _next_version_no(db: AsyncSession, portfolio_id: int) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(ModelPortfolioVersion.version_no), 0)).where(
            ModelPortfolioVersion.portfolio_id == portfolio_id
        )
    )
    return int(result.scalar_one()) + 1


def _facts_payload(preview: PortfolioDraftBuildPreview) -> dict[str, object]:
    allocations = []
    for allocation in preview.optimization.allocations:
        scored = allocation.scored_candidate
        candidate = scored.candidate
        allocations.append(
            {
                "trader_id": candidate.trader_id,
                "hl_address": candidate.hl_address,
                "portfolio_score": scored.portfolio_score,
                "target_weight_pct": allocation.target_weight_pct,
                "source_metrics": scored.score_snapshot["source_metrics"],
                "constraint_snapshot": allocation.constraint_snapshot,
            }
        )

    return {
        "portfolio_slug": preview.portfolio.slug,
        "risk_profile": preview.portfolio.risk_profile,
        "methodology_version": preview.portfolio.methodology_version,
        "allocations": allocations,
        "optimizer_summary": preview.optimization.summary,
    }


def _facts_hash(preview: PortfolioDraftBuildPreview) -> str:
    payload = json.dumps(_facts_payload(preview), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


async def build_draft_model_portfolio(
    db: AsyncSession,
    portfolio_slug: str = "balanced",
    period: str = "allTime",
    created_by: int | None = None,
    internal_alpha_relaxed: bool = False,
) -> PortfolioDraftBuildResult:
    selection_started_at = _naive_utc_now()
    preview = await preview_model_portfolio_draft(
        db,
        portfolio_slug,
        period=period,
        internal_alpha_relaxed=internal_alpha_relaxed,
    )
    selection_finished_at = _naive_utc_now()
    version_no = await _next_version_no(db, preview.portfolio.id)

    summary_json = {
        **preview.optimization.summary,
        "period": period,
        "eligible_candidate_count": len(preview.candidate_selection.eligible),
        "filtered_rejected_count": len(preview.candidate_selection.rejected),
        "optimizer_rejected_count": len(preview.optimization.rejected),
        "methodology_version": preview.portfolio.methodology_version,
        "builder_mode": (
            "internal_alpha_relaxed" if internal_alpha_relaxed else "strict"
        ),
    }
    version = ModelPortfolioVersion(
        portfolio_id=preview.portfolio.id,
        version_no=version_no,
        status="draft",
        created_by=created_by,
        selection_started_at=selection_started_at,
        selection_finished_at=selection_finished_at,
        facts_hash=_facts_hash(preview),
        summary_json=summary_json,
    )
    db.add(version)
    await db.flush()

    for allocation in preview.optimization.allocations:
        scored = allocation.scored_candidate
        db.add(
            ModelPortfolioAllocation(
                version_id=version.id,
                trader_id=scored.candidate.trader_id,
                target_weight_pct=_decimal(allocation.target_weight_pct),
                copy_ratio_pct=_decimal(allocation.copy_ratio_pct),
                max_leverage=_decimal(allocation.max_leverage),
                stop_loss_pct=_decimal(allocation.stop_loss_pct),
                sizing_mode=allocation.sizing_mode,
                max_per_coin_usd=(
                    _decimal(allocation.max_per_coin_usd)
                    if allocation.max_per_coin_usd is not None
                    else None
                ),
                allowed_coins=allocation.allowed_coins,
                reason_code=allocation.reason_code,
                reason_text=allocation.reason_text,
                score_snapshot=scored.score_snapshot,
                constraint_snapshot=allocation.constraint_snapshot,
            )
        )

    await db.flush()
    return PortfolioDraftBuildResult(
        version=version,
        candidate_selection=preview.candidate_selection,
        optimization=preview.optimization,
    )
