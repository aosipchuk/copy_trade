import pytest

from app.services.portfolio.explanations import (
    assert_no_forbidden_wording,
    explain_allocation_from_facts,
    rebalance_rationale,
)


def _facts() -> dict[str, object]:
    return {
        "allocation": {
            "id": 11,
            "trader_id": 22,
            "target_weight_pct": 17.5,
            "copy_ratio_pct": 100.0,
            "max_leverage": 8.0,
            "stop_loss_pct": 20.0,
        },
        "trader": {
            "id": 22,
            "address": "0xabc",
            "display_name": "Balanced Trader",
        },
        "portfolio": {
            "risk_profile": "balanced",
            "methodology_version": "balanced-mvp-v1",
        },
        "portfolio_score": 82.25,
        "source_metrics": {
            "active_trading_days": 91,
            "max_drawdown_pct": 12.5,
            "avg_leverage": 3.4,
            "trade_count": 44,
        },
        "available_fact_keys": [
            "allocation.target_weight_pct",
            "portfolio_score",
            "source_metrics.active_trading_days",
            "source_metrics.max_drawdown_pct",
            "source_metrics.avg_leverage",
            "source_metrics.trade_count",
        ],
    }


def test_template_explanation_uses_only_available_source_facts() -> None:
    facts = _facts()

    text, used_keys, generated_by = explain_allocation_from_facts(facts)

    assert generated_by == "template"
    assert "Balanced Trader" in text
    assert "82.25" in text
    assert "12.50%" in text
    assert_no_forbidden_wording(text)
    available = set(facts["available_fact_keys"])  # type: ignore[arg-type]
    assert set(used_keys) <= available


def test_template_explanation_falls_back_for_forbidden_source_wording() -> None:
    facts = _facts()
    facts["portfolio"] = {"risk_profile": "guaranteed"}

    text, used_keys, generated_by = explain_allocation_from_facts(facts)

    assert generated_by == "fallback"
    assert "guaranteed" not in text.lower()
    assert_no_forbidden_wording(text)
    available = set(facts["available_fact_keys"])  # type: ignore[arg-type]
    assert set(used_keys) <= available


def test_sparse_metrics_explanation_is_limited_to_allocation_facts() -> None:
    facts = {
        "allocation": {"target_weight_pct": 10.0},
        "trader": {"address": "0xsparse", "display_name": None},
        "portfolio": {"risk_profile": "balanced"},
        "source_metrics": {},
        "available_fact_keys": ["allocation.target_weight_pct"],
    }

    text, used_keys, generated_by = explain_allocation_from_facts(facts)

    assert generated_by == "template"
    assert "Detailed trader metrics are limited" in text
    assert used_keys == ["allocation.target_weight_pct"]
    assert_no_forbidden_wording(text)


def test_forbidden_wording_detector_rejects_promises() -> None:
    with pytest.raises(ValueError):
        assert_no_forbidden_wording("This is a guaranteed result.")


def test_rebalance_rationale_is_action_specific() -> None:
    text = rebalance_rationale(
        "add_trader",
        source_facts={
            "target_allocation": {
                "allocation_id": 1,
                "target_weight_pct": 18.0,
            }
        },
    )

    assert text == (
        "The current published version includes this trader at 18.000% "
        "target weight."
    )
    assert_no_forbidden_wording(text)
