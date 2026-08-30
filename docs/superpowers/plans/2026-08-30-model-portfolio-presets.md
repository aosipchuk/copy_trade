# Model Portfolio Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the model-portfolio catalog from Balanced-only to Conservative, Balanced, Aggressive, and copyability-first Starter presets.

**Architecture:** Keep risk limits in the existing deterministic builder, add profile-specific scoring weights, and use a slug-specific override only for the Starter objective. Seed templates remain idempotent; versions still follow the existing draft, backtest, manual approval, and publish workflow.

**Tech Stack:** Python 3.12, SQLAlchemy 2, Pydantic v2, pytest, React 18, TypeScript, Tailwind CSS.

---

## File structure

- Modify `backend/scripts/seed_model_portfolios.py`: define and idempotently seed the four product templates.
- Modify `backend/app/services/portfolio/types.py`: hold score weights, the Starter selection override, and profile lookup.
- Modify `backend/app/services/portfolio/candidates.py`: enforce the optional minimum copyable position size.
- Modify `backend/app/services/portfolio/advanced.py`: make anomaly thresholds follow the selected profile.
- Modify `backend/app/services/portfolio/scoring.py`: apply profile-specific scoring objectives and persist them in score snapshots.
- Modify `backend/app/services/portfolio/optimizer.py`: emit profile-specific rationale instead of Balanced-only text.
- Modify `backend/app/services/portfolio/publisher.py`: resolve a config by portfolio slug and risk profile for build and publish validation.
- Modify `backend/app/services/portfolio/activation.py`: enforce each template's minimum allocation server-side.
- Modify `frontend/src/pages/PortfoliosPage.tsx`: explain and deliberately order the presets.
- Modify `frontend/src/pages/PortfolioDetailPage.tsx`: show the portfolio description on the detail screen.
- Modify `backend/tests/unit/test_portfolio_models.py`: cover the seeded catalog.
- Modify `backend/tests/unit/test_portfolio_builder.py`: cover profile thresholds, Starter copyability, and differentiated scoring.
- Modify `backend/tests/api/test_portfolio_subscriptions.py`: cover minimum-allocation enforcement.

### Task 1: Seed the product catalog

**Files:**
- Modify: `backend/scripts/seed_model_portfolios.py`
- Test: `backend/tests/unit/test_portfolio_models.py`

- [ ] **Step 1: Write catalog expectations**

```python
def test_model_portfolio_seed_catalog_is_complete() -> None:
    assert [item["slug"] for item in MODEL_PORTFOLIOS] == [
        "starter",
        "conservative",
        "balanced",
        "aggressive",
    ]
    assert {item["risk_profile"] for item in MODEL_PORTFOLIOS} == {
        "conservative",
        "balanced",
        "aggressive",
    }
```

- [ ] **Step 2: Verify the test fails on the server**

Run: `cd backend && uv run pytest tests/unit/test_portfolio_models.py -v`

Expected: FAIL because `MODEL_PORTFOLIOS` does not exist.

- [ ] **Step 3: Define all templates and generalize the seed/check loop**

```python
MODEL_PORTFOLIOS = (
    STARTER_PORTFOLIO,
    CONSERVATIVE_PORTFOLIO,
    BALANCED_PORTFOLIO,
    AGGRESSIVE_PORTFOLIO,
)

async def seed_model_portfolios() -> list[int]:
    async with get_db_session() as db:
        ids: list[int] = []
        for values in MODEL_PORTFOLIOS:
            result = await db.execute(
                select(ModelPortfolio).where(ModelPortfolio.slug == values["slug"])
            )
            portfolio = result.scalar_one_or_none()
            if portfolio is None:
                portfolio = ModelPortfolio(**values)
                db.add(portfolio)
            else:
                for key, value in values.items():
                    setattr(portfolio, key, value)
            await db.flush()
            ids.append(portfolio.id)
        return ids
```

- [ ] **Step 4: Verify the seed tests on the server**

Run: `cd backend && uv run pytest tests/unit/test_portfolio_models.py -v`

Expected: PASS.

### Task 2: Differentiate selection and scoring

**Files:**
- Modify: `backend/app/services/portfolio/types.py`
- Modify: `backend/app/services/portfolio/candidates.py`
- Modify: `backend/app/services/portfolio/advanced.py`
- Modify: `backend/app/services/portfolio/scoring.py`
- Modify: `backend/app/services/portfolio/optimizer.py`
- Modify: `backend/app/services/portfolio/publisher.py`
- Modify: `backend/app/services/portfolio/activation.py`
- Test: `backend/tests/unit/test_portfolio_builder.py`
- Test: `backend/tests/api/test_portfolio_subscriptions.py`

- [ ] **Step 1: Add failing profile-behavior tests**

```python
def test_starter_rejects_hard_to_copy_small_positions() -> None:
    config = get_portfolio_config("starter", "balanced")
    result = apply_candidate_filters(
        [_raw_candidate(1, metrics=_metrics(avg_position_size_usd=499.0))],
        config,
    )
    assert result.rejected[0].reason_code == "position_size_too_small"

def test_aggressive_scoring_values_returns_more_than_conservative() -> None:
    candidate = _candidate(1, metrics=_metrics(roi_pct=50.0, max_drawdown_pct=18.0))
    conservative = score_candidate(candidate, RISK_PROFILE_CONFIGS["conservative"])
    aggressive = score_candidate(candidate, RISK_PROFILE_CONFIGS["aggressive"])
    assert aggressive.score_snapshot["score_weights"]["return_score"] > (
        conservative.score_snapshot["score_weights"]["return_score"]
    )
```

- [ ] **Step 2: Verify the tests fail on the server**

Run: `cd backend && uv run pytest tests/unit/test_portfolio_builder.py -v`

Expected: FAIL because portfolio-specific configuration and scoring do not exist.

- [ ] **Step 3: Add score weights and the Starter override**

```python
@dataclass(frozen=True)
class PortfolioScoreWeights:
    risk_adjusted_score: float
    consistency_score: float
    return_score: float
    copyability_score: float
    diversification_score: float
    behavior_stability_score: float

STARTER_PORTFOLIO_CONFIG = replace(
    RISK_PROFILE_CONFIGS["balanced"],
    selection_profile="starter",
    min_traders=5,
    max_traders=6,
    max_weight_pct=22.0,
    max_avg_trades_per_day=8.0,
    min_avg_position_size_usd=500.0,
    account_size_tiers=(
        ("starter", 500.0),
        ("standard", 1_000.0),
        ("larger", 5_000.0),
    ),
    score_weights=PortfolioScoreWeights(
        risk_adjusted_score=0.20,
        consistency_score=0.15,
        return_score=0.10,
        copyability_score=0.35,
        diversification_score=0.10,
        behavior_stability_score=0.10,
    ),
)

def get_portfolio_config(slug: str, risk_profile: str) -> RiskProfileConfig:
    if slug == "starter":
        if risk_profile != "balanced":
            raise ValueError("Starter portfolio must use balanced risk profile.")
        return STARTER_PORTFOLIO_CONFIG
    return get_risk_profile_config(risk_profile)
```

- [ ] **Step 4: Apply the config through filtering, anomaly detection, scoring, optimization, and publication**

```python
config = get_portfolio_config(portfolio.slug, portfolio.risk_profile)
scored_candidates = score_candidates(candidate_selection.eligible, config)
optimization = optimize_portfolio(scored_candidates, config)
```

The score snapshot stores the exact objective used for reproducibility:

```python
snapshot = {
    "methodology_version": config.methodology_version,
    "risk_profile": config.risk_profile,
    "selection_profile": config.selection_profile,
    "score_weights": config.score_weights.as_dict(),
    "portfolio_score": portfolio_score,
    "component_scores": components,
    "anomaly_detection": detect_candidate_anomalies(metrics, config),
}
```

- [ ] **Step 5: Enforce the advertised minimum allocation**

```python
minimum_equity = _decimal(portfolio.min_equity_usd)
if _total_allocation(data.total_allocation_usd) < minimum_equity:
    raise ValueError(
        "Portfolio allocation must be at least "
        f"${minimum_equity.quantize(Decimal('0.01'))}."
    )
```

- [ ] **Step 6: Verify builder and activation tests on the server**

Run: `cd backend && uv run pytest tests/unit/test_portfolio_builder.py tests/api/test_portfolio_subscriptions.py -v`

Expected: PASS.

### Task 3: Make the choices understandable in the UI

**Files:**
- Modify: `frontend/src/pages/PortfoliosPage.tsx`
- Modify: `frontend/src/pages/PortfolioDetailPage.tsx`

- [ ] **Step 1: Add stable product ordering and user-facing risk labels**

```typescript
const portfolioOrder: Record<string, number> = {
  starter: 0,
  conservative: 1,
  balanced: 2,
  aggressive: 3,
}

const riskLabels: Record<RiskProfile, string> = {
  conservative: 'Lower risk',
  balanced: 'Balanced',
  aggressive: 'High risk',
}
```

- [ ] **Step 2: Render concise descriptions on list and detail screens**

```tsx
{portfolio.description && (
  <p className="mb-3 text-xs leading-5 text-tg-hint">
    {portfolio.description}
  </p>
)}
```

- [ ] **Step 3: Verify the frontend on the server**

Run: `cd frontend && npm run build`

Expected: TypeScript and Vite build complete successfully.

### Task 4: Server verification and release preparation

**Files:**
- No code files.

- [ ] **Step 1: Run backend verification on the server**

Run: `cd backend && uv run pytest tests/unit/test_portfolio_models.py tests/unit/test_portfolio_builder.py -v`

Expected: PASS.

- [ ] **Step 2: Run static checks on the server**

Run: `make lint && make typecheck`

Expected: Ruff, Black, and strict mypy pass.

- [ ] **Step 3: Seed and review templates through the normal release path**

```bash
cd backend
uv run python -m scripts.seed_model_portfolios
uv run python -m scripts.seed_model_portfolios --check
for slug in starter conservative aggressive; do
  uv run python -m scripts.build_model_portfolio_draft --portfolio-slug "$slug" --period allTime --dry-run
done
```

Expected: all four templates are present, and each new preset can produce a valid 100% allocation preview. Draft creation, backtests, manual approval, and publication remain explicit release operations.

- [ ] **Step 4: Commit the focused change**

```bash
git add backend/scripts/seed_model_portfolios.py \
  backend/app/services/portfolio/types.py \
  backend/app/services/portfolio/candidates.py \
  backend/app/services/portfolio/advanced.py \
  backend/app/services/portfolio/scoring.py \
  backend/app/services/portfolio/optimizer.py \
  backend/app/services/portfolio/publisher.py \
  backend/app/services/portfolio/activation.py \
  backend/tests/unit/test_portfolio_models.py \
  backend/tests/unit/test_portfolio_builder.py \
  backend/tests/api/test_portfolio_subscriptions.py \
  frontend/src/pages/PortfoliosPage.tsx \
  frontend/src/pages/PortfolioDetailPage.tsx \
  docs/superpowers/plans/2026-08-30-model-portfolio-presets.md
git commit -m "feat: add model portfolio presets"
```
