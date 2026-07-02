import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.models.portfolio import (
    ModelPortfolio,
    ModelPortfolioAllocation,
    ModelPortfolioVersion,
    PortfolioBacktest,
    PortfolioReport,
)
from app.schemas.portfolio import (
    PortfolioAllocationExplanationResponse,
    PortfolioExplanationResponse,
    PortfolioReportAllocationNote,
    PortfolioReportSection,
    PortfolioWeeklyReportResponse,
)
from app.services.portfolio.access import redact_trader_identity_payload

JsonDict = dict[str, Any]

logger = get_logger(__name__)

PROMPT_VERSION = "model-portfolio-explanations-v1"
FORBIDDEN_WORDING = (
    "guarantee",
    "guaranteed",
    "risk-free",
    "without risk",
    "stable income",
    "safe profit",
    "без риска",
    "гарант",
    "безопас",
    "стабильный доход",
)
METRIC_KEYS = (
    "roi_pct",
    "max_drawdown_pct",
    "active_trading_days",
    "avg_leverage",
    "trade_count",
    "win_rate_pct",
    "profit_factor",
    "sharpe_ratio",
    "sortino_ratio",
    "avg_trades_per_day",
    "composite_score",
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _int(value: object) -> int | None:
    numeric = _float(value)
    return int(numeric) if numeric is not None else None


def _json_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json_dict(value: object) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _contains_forbidden_wording(text: str) -> bool:
    normalized = text.lower()
    return any(fragment in normalized for fragment in FORBIDDEN_WORDING)


def assert_no_forbidden_wording(text: str) -> None:
    if _contains_forbidden_wording(text):
        raise ValueError("Generated portfolio explanation contains forbidden wording.")


def _source_metrics(allocation: ModelPortfolioAllocation) -> JsonDict:
    score_snapshot = _json_mapping(allocation.score_snapshot)
    raw_metrics = _json_mapping(score_snapshot.get("source_metrics"))
    metrics: JsonDict = {}
    for key in METRIC_KEYS:
        if key in raw_metrics and raw_metrics[key] is not None:
            metrics[key] = raw_metrics[key]
    return metrics


def _portfolio_score(allocation: ModelPortfolioAllocation) -> float | None:
    return _float(_json_mapping(allocation.score_snapshot).get("portfolio_score"))


def _available_fact_keys(source_facts: Mapping[str, Any]) -> list[str]:
    keys = [
        "allocation.target_weight_pct",
        "allocation.copy_ratio_pct",
        "allocation.max_leverage",
        "allocation.stop_loss_pct",
    ]
    if source_facts.get("portfolio_score") is not None:
        keys.append("portfolio_score")
    metrics = _json_mapping(source_facts.get("source_metrics"))
    keys.extend(f"source_metrics.{key}" for key in sorted(metrics))
    constraints = _json_mapping(source_facts.get("constraint_snapshot"))
    keys.extend(f"constraint_snapshot.{key}" for key in sorted(constraints))
    return keys


def allocation_source_facts(
    allocation: ModelPortfolioAllocation,
    *,
    portfolio: ModelPortfolio | None = None,
    version: ModelPortfolioVersion | None = None,
) -> JsonDict:
    trader = allocation.trader
    facts: JsonDict = {
        "allocation": {
            "id": allocation.id,
            "trader_id": allocation.trader_id,
            "target_weight_pct": _float(allocation.target_weight_pct),
            "copy_ratio_pct": _float(allocation.copy_ratio_pct),
            "max_leverage": _float(allocation.max_leverage),
            "stop_loss_pct": _float(allocation.stop_loss_pct),
            "sizing_mode": allocation.sizing_mode,
            "max_per_coin_usd": _float(allocation.max_per_coin_usd),
            "allowed_coins": list(allocation.allowed_coins or []),
            "reason_code": allocation.reason_code,
        },
        "trader": {
            "id": allocation.trader_id,
            "address": trader.hl_address if trader is not None else None,
            "display_name": trader.display_name if trader is not None else None,
        },
        "portfolio_score": _portfolio_score(allocation),
        "source_metrics": _source_metrics(allocation),
        "constraint_snapshot": _json_dict(allocation.constraint_snapshot),
    }
    if portfolio is not None:
        facts["portfolio"] = {
            "id": portfolio.id,
            "slug": portfolio.slug,
            "name": portfolio.name,
            "risk_profile": portfolio.risk_profile,
            "methodology_version": portfolio.methodology_version,
        }
    if version is not None:
        facts["version"] = {
            "id": version.id,
            "version_no": version.version_no,
            "status": version.status,
            "valid_from": (
                version.valid_from.isoformat()
                if version.valid_from is not None
                else None
            ),
        }
    facts["available_fact_keys"] = _available_fact_keys(facts)
    return facts


def explain_allocation_from_facts(
    source_facts: Mapping[str, Any],
    *,
    generated_by: str = "template",
) -> tuple[str, list[str], str]:
    allocation = _json_mapping(source_facts.get("allocation"))
    source_metrics = _json_mapping(source_facts.get("source_metrics"))
    trader = _json_mapping(source_facts.get("trader"))
    portfolio = _json_mapping(source_facts.get("portfolio"))
    used_keys = ["allocation.target_weight_pct"]

    name = trader.get("display_name") or trader.get("address") or "This trader"
    weight = _float(allocation.get("target_weight_pct"))
    risk_profile = portfolio.get("risk_profile") or "model portfolio"
    if weight is None:
        opening = f"{name} is included by the {risk_profile} methodology."
        used_keys = []
    else:
        opening = (
            f"{name} is included at {weight:.3f}% target weight by the "
            f"{risk_profile} methodology."
        )

    details: list[str] = []
    score = _float(source_facts.get("portfolio_score"))
    if score is not None:
        details.append(f"Saved portfolio score: {score:.2f}.")
        used_keys.append("portfolio_score")

    active_days = _int(source_metrics.get("active_trading_days"))
    if active_days is not None:
        details.append(f"Active trading days in stored metrics: {active_days}.")
        used_keys.append("source_metrics.active_trading_days")

    drawdown = _float(source_metrics.get("max_drawdown_pct"))
    if drawdown is not None:
        details.append(f"Stored max drawdown metric: {drawdown:.2f}%.")
        used_keys.append("source_metrics.max_drawdown_pct")

    leverage = _float(source_metrics.get("avg_leverage"))
    if leverage is not None:
        details.append(f"Stored average leverage metric: {leverage:.2f}x.")
        used_keys.append("source_metrics.avg_leverage")

    trade_count = _int(source_metrics.get("trade_count"))
    if trade_count is not None:
        details.append(f"Stored trade count: {trade_count}.")
        used_keys.append("source_metrics.trade_count")

    if not details:
        details.append(
            "Detailed trader metrics are limited, so the explanation uses only "
            "stored allocation and selection facts."
        )

    text = " ".join([opening, *details])
    if _contains_forbidden_wording(text):
        text = (
            "This allocation is explained only from stored portfolio facts and "
            "does not include performance promises."
        )
        generated_by = "fallback"
        available_keys = _available_fact_keys(source_facts)
        used_keys = [key for key in used_keys if key in available_keys]

    return text, used_keys, generated_by


def rebalance_rationale(
    action: str,
    *,
    source_facts: Mapping[str, Any],
    changed_fields: Sequence[str] | None = None,
) -> str:
    changed = ", ".join(changed_fields or [])
    if action == "add_trader":
        allocation = _json_mapping(source_facts.get("target_allocation"))
        weight = _float(allocation.get("target_weight_pct"))
        if weight is None:
            return "The current published version adds this trader."
        return (
            "The current published version includes this trader at "
            f"{weight:.3f}% target weight."
        )
    if action == "remove_trader":
        return (
            "The current published version no longer includes this trader, so "
            "only the portfolio-owned subscription is removed."
        )
    if action == "change_weight":
        return "The published target weight changed for this portfolio allocation."
    if action == "change_risk_settings":
        return "The published portfolio settings changed" + (
            f": {changed}." if changed else "."
        )
    if action == "no_change":
        return "Generated subscriptions already match the current published version."
    if action == "blocked_by_user_conflict":
        return "A manual live subscription would create duplicate live exposure."
    if action == "blocked_by_payment":
        return "Live rebalance requires an active billing status."
    if action == "blocked_by_wallet":
        return "Live rebalance requires a ready wallet and approved agent."
    if action == "failed_risk_check":
        return "Portfolio risk validation did not allow the target rebalance."
    return "This rebalance item is explained from the stored diff facts."


def _allocation_explanation_response(
    allocation: ModelPortfolioAllocation,
    portfolio: ModelPortfolio,
    version: ModelPortfolioVersion,
) -> PortfolioAllocationExplanationResponse:
    facts = allocation_source_facts(allocation, portfolio=portfolio, version=version)
    explanation, used_keys, generated_by = explain_allocation_from_facts(facts)
    trader = allocation.trader
    return PortfolioAllocationExplanationResponse(
        allocation_id=allocation.id,
        trader_id=allocation.trader_id,
        trader_address=trader.hl_address if trader is not None else "",
        trader_display_name=trader.display_name if trader is not None else None,
        generated_by=generated_by,
        prompt_version=PROMPT_VERSION,
        explanation=explanation,
        source_facts=facts,
        used_source_fact_keys=used_keys,
    )


def _redact_allocation_explanation(
    response: PortfolioAllocationExplanationResponse,
) -> PortfolioAllocationExplanationResponse:
    source_facts = redact_trader_identity_payload(response.source_facts)
    return response.model_copy(
        update={
            "trader_id": None,
            "trader_address": None,
            "trader_display_name": None,
            "source_facts": source_facts if isinstance(source_facts, dict) else {},
        }
    )


def _target_weight_sum(allocations: Sequence[ModelPortfolioAllocation]) -> float:
    return round(
        sum(_float(allocation.target_weight_pct) or 0.0 for allocation in allocations),
        3,
    )


def _current_week_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = (now or _now()).replace(hour=0, minute=0, second=0, microsecond=0)
    period_start = current - timedelta(days=current.weekday())
    period_end = period_start + timedelta(days=7)
    return period_start, period_end


async def _load_current_published(
    db: AsyncSession, slug: str
) -> tuple[ModelPortfolio, ModelPortfolioVersion]:
    result = await db.execute(
        select(ModelPortfolio)
        .where(ModelPortfolio.slug == slug, ModelPortfolio.status == "active")
        .limit(1)
    )
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        raise LookupError("Model portfolio not found.")

    version_result = await db.execute(
        select(ModelPortfolioVersion)
        .options(
            selectinload(ModelPortfolioVersion.allocations).selectinload(
                ModelPortfolioAllocation.trader
            ),
            selectinload(ModelPortfolioVersion.backtests),
        )
        .where(
            ModelPortfolioVersion.portfolio_id == portfolio.id,
            ModelPortfolioVersion.status == "published",
            ModelPortfolioVersion.valid_to.is_(None),
        )
        .limit(1)
    )
    version = version_result.scalar_one_or_none()
    if version is None:
        raise LookupError("Published portfolio version not found.")
    return portfolio, version


def _sorted_allocations(
    allocations: Sequence[ModelPortfolioAllocation],
) -> list[ModelPortfolioAllocation]:
    return sorted(
        allocations,
        key=lambda item: (_float(item.target_weight_pct) or 0.0, item.id),
        reverse=True,
    )


def _sorted_backtests(
    backtests: Sequence[PortfolioBacktest],
) -> list[PortfolioBacktest]:
    return sorted(
        backtests,
        key=lambda item: (
            item.period_days,
            _float(item.initial_equity_usd) or 0.0,
            item.created_at,
            item.id,
        ),
        reverse=True,
    )


async def build_portfolio_explanations(
    db: AsyncSession,
    slug: str,
    *,
    include_trader_identities: bool = True,
) -> PortfolioExplanationResponse:
    portfolio, version = await _load_current_published(db, slug)
    allocations = _sorted_allocations(version.allocations)
    allocation_explanations = [
        _allocation_explanation_response(allocation, portfolio, version)
        for allocation in allocations
    ]
    if not include_trader_identities:
        allocation_explanations = [
            _redact_allocation_explanation(response)
            for response in allocation_explanations
        ]
    source_facts: JsonDict = {
        "portfolio": {
            "id": portfolio.id,
            "slug": portfolio.slug,
            "name": portfolio.name,
            "risk_profile": portfolio.risk_profile,
            "methodology_version": portfolio.methodology_version,
            "rebalance_cadence": portfolio.rebalance_cadence,
        },
        "version": {
            "id": version.id,
            "version_no": version.version_no,
            "status": version.status,
            "valid_from": (
                version.valid_from.isoformat()
                if version.valid_from is not None
                else None
            ),
        },
        "allocation_count": len(allocations),
        "target_weight_sum_pct": _target_weight_sum(allocations),
    }
    summary = (
        f"{portfolio.name} v{version.version_no} has {len(allocations)} "
        f"stored allocations with {_target_weight_sum(allocations):.3f}% total "
        "target weight. Explanations use saved scoring and constraint facts."
    )
    assert_no_forbidden_wording(summary)
    return PortfolioExplanationResponse(
        portfolio_id=portfolio.id,
        portfolio_slug=portfolio.slug,
        portfolio_name=portfolio.name,
        version_id=version.id,
        version_no=version.version_no,
        generated_at=_now(),
        generated_by="template",
        prompt_version=PROMPT_VERSION,
        trader_details_visible=include_trader_identities,
        summary=summary,
        source_facts=source_facts,
        allocations=allocation_explanations,
    )


def _backtest_source_facts(backtest: PortfolioBacktest | None) -> JsonDict | None:
    if backtest is None:
        return None
    return {
        "id": backtest.id,
        "period_days": backtest.period_days,
        "initial_equity_usd": _float(backtest.initial_equity_usd),
        "total_return_pct": _float(backtest.total_return_pct),
        "max_drawdown_pct": _float(backtest.max_drawdown_pct),
        "sharpe_ratio": _float(backtest.sharpe_ratio),
        "sortino_ratio": _float(backtest.sortino_ratio),
        "win_rate_pct": _float(backtest.win_rate_pct),
        "turnover_pct": _float(backtest.turnover_pct),
        "fees_usd": _float(backtest.fees_usd),
        "slippage_usd": _float(backtest.slippage_usd),
        "missed_trade_count": backtest.missed_trade_count,
        "assumptions_json": backtest.assumptions_json,
        "created_at": backtest.created_at.isoformat(),
    }


def _weekly_source_facts(
    portfolio: ModelPortfolio,
    version: ModelPortfolioVersion,
    period_start: datetime,
    period_end: datetime,
) -> JsonDict:
    allocations = _sorted_allocations(version.allocations)
    backtests = _sorted_backtests(version.backtests)
    latest_backtest = backtests[0] if backtests else None
    return {
        "portfolio": {
            "id": portfolio.id,
            "slug": portfolio.slug,
            "name": portfolio.name,
            "risk_profile": portfolio.risk_profile,
            "methodology_version": portfolio.methodology_version,
            "rebalance_cadence": portfolio.rebalance_cadence,
            "min_equity_usd": _float(portfolio.min_equity_usd),
            "monthly_price_usd": _float(portfolio.monthly_price_usd),
        },
        "version": {
            "id": version.id,
            "version_no": version.version_no,
            "status": version.status,
            "valid_from": (
                version.valid_from.isoformat()
                if version.valid_from is not None
                else None
            ),
            "approved_at": (
                version.approved_at.isoformat()
                if version.approved_at is not None
                else None
            ),
            "summary_json": version.summary_json,
        },
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "allocation_count": len(allocations),
        "target_weight_sum_pct": _target_weight_sum(allocations),
        "allocations": [
            allocation_source_facts(allocation, portfolio=portfolio, version=version)
            for allocation in allocations
        ],
        "latest_backtest": _backtest_source_facts(latest_backtest),
    }


def _template_weekly_report_json(source_facts: Mapping[str, Any]) -> JsonDict:
    portfolio = _json_mapping(source_facts.get("portfolio"))
    version = _json_mapping(source_facts.get("version"))
    latest_backtest = source_facts.get("latest_backtest")
    backtest = _json_mapping(latest_backtest) if latest_backtest is not None else {}
    assumptions = _json_mapping(backtest.get("assumptions_json"))
    allocation_count = int(source_facts.get("allocation_count") or 0)
    target_weight_sum = _float(source_facts.get("target_weight_sum_pct")) or 0.0
    name = str(portfolio.get("name") or "Model portfolio")
    version_no = int(version.get("version_no") or 0)

    summary = (
        f"{name} v{version_no} remains allocated across {allocation_count} "
        f"traders with {target_weight_sum:.3f}% total target weight."
    )
    sections = [
        {
            "title": "Composition",
            "body": (
                f"The current published version has {allocation_count} traders. "
                f"Target weights sum to {target_weight_sum:.3f}%."
            ),
        },
        {
            "title": "Methodology",
            "body": (
                "Selection uses saved deterministic scoring, portfolio constraints, "
                "and manual publication of versions."
            ),
        },
    ]
    if backtest:
        data_source = assumptions.get("data_source", "unknown")
        sections.append(
            {
                "title": "Backtest context",
                "body": (
                    f"Latest saved backtest covers {backtest.get('period_days')} days "
                    f"with {data_source} data and explicit fee/slippage assumptions."
                ),
            }
        )
    else:
        sections.append(
            {
                "title": "Backtest context",
                "body": "No saved backtest is attached to this published version.",
            }
        )
    sections.append(
        {
            "title": "Risk note",
            "body": (
                "This report explains stored portfolio facts and historical testing "
                "assumptions; it is not a forecast."
            ),
        }
    )

    allocation_notes: list[JsonDict] = []
    raw_allocations = source_facts.get("allocations")
    if isinstance(raw_allocations, Sequence) and not isinstance(
        raw_allocations, str | bytes
    ):
        for raw in raw_allocations:
            facts = _json_mapping(raw)
            text, _, _ = explain_allocation_from_facts(facts)
            allocation = _json_mapping(facts.get("allocation"))
            trader = _json_mapping(facts.get("trader"))
            allocation_notes.append(
                {
                    "allocation_id": int(allocation.get("id") or 0),
                    "trader_id": int(allocation.get("trader_id") or 0),
                    "trader_address": str(trader.get("address") or ""),
                    "trader_display_name": trader.get("display_name"),
                    "note": text,
                }
            )

    report_json: JsonDict = {
        "summary": summary,
        "sections": sections,
        "allocation_notes": allocation_notes,
    }
    assert_no_forbidden_wording(json.dumps(report_json, ensure_ascii=False))
    return report_json


async def _llm_summary(source_facts: Mapping[str, Any]) -> str | None:
    if settings.model_portfolio_explanations_provider != "openai_compatible":
        return None
    if not (
        settings.model_portfolio_llm_api_url
        and settings.model_portfolio_llm_api_key
        and settings.model_portfolio_llm_model
    ):
        return None

    payload = {
        "model": settings.model_portfolio_llm_model,
        "temperature": 0.2,
        "max_tokens": 220,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Write a concise model portfolio weekly summary using only "
                    "the provided JSON facts. Do not make forecasts or promises."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(source_facts, ensure_ascii=False, sort_keys=True),
            },
        ],
    }
    headers = {"Authorization": f"Bearer {settings.model_portfolio_llm_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.post(
                settings.model_portfolio_llm_api_url,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("portfolio_llm_summary_failed", error=str(exc))
        return None

    data = response.json()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        return None
    text = content.strip()
    if _contains_forbidden_wording(text):
        logger.warning("portfolio_llm_summary_forbidden_wording")
        return None
    return text


def _report_response(
    report: PortfolioReport,
    portfolio: ModelPortfolio,
    version: ModelPortfolioVersion,
    *,
    include_trader_identities: bool = True,
) -> PortfolioWeeklyReportResponse:
    report_json = report.report_json
    raw_sections = report_json.get("sections")
    raw_notes = report_json.get("allocation_notes")
    sections: list[PortfolioReportSection] = []
    if isinstance(raw_sections, Sequence) and not isinstance(raw_sections, str | bytes):
        sections = [
            PortfolioReportSection.model_validate(section)
            for section in raw_sections
            if isinstance(section, Mapping)
        ]

    notes: list[PortfolioReportAllocationNote] = []
    if isinstance(raw_notes, Sequence) and not isinstance(raw_notes, str | bytes):
        notes = [
            PortfolioReportAllocationNote.model_validate(note)
            for note in raw_notes
            if isinstance(note, Mapping)
        ]
    source_facts: JsonDict = report.source_facts
    response_report_json: JsonDict = report.report_json
    if not include_trader_identities:
        notes = [
            note.model_copy(
                update={
                    "trader_id": None,
                    "trader_address": None,
                    "trader_display_name": None,
                }
            )
            for note in notes
        ]
        redacted_source_facts = redact_trader_identity_payload(source_facts)
        redacted_report_json = redact_trader_identity_payload(response_report_json)
        source_facts = (
            redacted_source_facts if isinstance(redacted_source_facts, dict) else {}
        )
        response_report_json = (
            redacted_report_json if isinstance(redacted_report_json, dict) else {}
        )
    return PortfolioWeeklyReportResponse(
        id=report.id,
        portfolio_id=portfolio.id,
        portfolio_slug=portfolio.slug,
        portfolio_name=portfolio.name,
        portfolio_version_id=version.id,
        version_no=version.version_no,
        report_type="weekly",
        period_start=report.period_start,
        period_end=report.period_end,
        generated_by=report.generated_by,
        prompt_version=report.prompt_version,
        trader_details_visible=include_trader_identities,
        source_facts=source_facts,
        report_json=response_report_json,
        summary=str(report_json.get("summary") or ""),
        sections=sections,
        allocation_notes=notes,
        created_at=report.created_at,
    )


async def get_latest_weekly_report(
    db: AsyncSession,
    slug: str,
    *,
    include_trader_identities: bool = True,
) -> PortfolioWeeklyReportResponse | None:
    portfolio, version = await _load_current_published(db, slug)
    result = await db.execute(
        select(PortfolioReport)
        .where(
            PortfolioReport.portfolio_id == portfolio.id,
            PortfolioReport.portfolio_version_id == version.id,
            PortfolioReport.report_type == "weekly",
        )
        .order_by(
            PortfolioReport.period_start.desc(),
            PortfolioReport.created_at.desc(),
            PortfolioReport.id.desc(),
        )
        .limit(1)
    )
    report = result.scalar_one_or_none()
    if report is None:
        return None
    return _report_response(
        report,
        portfolio,
        version,
        include_trader_identities=include_trader_identities,
    )


async def generate_weekly_report(
    db: AsyncSession,
    slug: str,
    *,
    now: datetime | None = None,
) -> PortfolioWeeklyReportResponse:
    portfolio, version = await _load_current_published(db, slug)
    period_start, period_end = _current_week_window(now)
    existing_result = await db.execute(
        select(PortfolioReport)
        .where(
            PortfolioReport.portfolio_id == portfolio.id,
            PortfolioReport.portfolio_version_id == version.id,
            PortfolioReport.report_type == "weekly",
            PortfolioReport.period_start == period_start,
            PortfolioReport.period_end == period_end,
        )
        .limit(1)
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        return _report_response(existing, portfolio, version)

    source_facts = _weekly_source_facts(portfolio, version, period_start, period_end)
    report_json = _template_weekly_report_json(source_facts)
    generated_by = "template"
    llm_text = await _llm_summary(source_facts)
    if llm_text is not None:
        report_json["summary"] = llm_text
        generated_by = "openai_compatible"

    if _contains_forbidden_wording(json.dumps(report_json, ensure_ascii=False)):
        report_json = _template_weekly_report_json(source_facts)
        generated_by = "fallback"

    report = PortfolioReport(
        portfolio_id=portfolio.id,
        portfolio_version_id=version.id,
        report_type="weekly",
        period_start=period_start,
        period_end=period_end,
        generated_by=generated_by,
        prompt_version=PROMPT_VERSION,
        source_facts=source_facts,
        report_json=report_json,
    )
    db.add(report)
    await db.flush()
    return _report_response(report, portfolio, version)
