# Model Portfolio Staged Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release the new portfolio catalog safely, with new presets available for demo while live activation remains explicitly gated until each preset is reviewed.

**Architecture:** Add a database-backed `live_enabled` gate to `model_portfolios`; backend activation and billing enforce it independently of the UI. Balanced remains live-enabled for backward compatibility, while Starter, Conservative, and Aggressive launch demo-first and are promoted separately after review.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, PostgreSQL, pytest, React 18, TypeScript.

---

## File structure

- Create `backend/alembic/versions/v8w9x0y1z2a3_add_model_portfolio_live_gate.py`: persist the live gate and preserve Balanced behavior.
- Modify `backend/app/models/portfolio.py`: map `ModelPortfolio.live_enabled`.
- Modify `backend/app/schemas/portfolio.py`: expose the gate to clients.
- Modify `backend/scripts/seed_model_portfolios.py`: seed Balanced as live and new presets as demo-first.
- Modify `backend/app/services/portfolio/activation.py`: reject direct live activation for gated portfolios.
- Modify `backend/app/services/portfolio/billing.py`: prevent checkout and report live availability accurately.
- Modify `backend/app/api/portfolio_billing.py`: return HTTP 409 for disabled-live checkout.
- Modify `frontend/src/types/index.ts`: type the API field.
- Modify `frontend/src/pages/PortfolioDetailPage.tsx`: explain demo-only availability and suppress live CTAs.
- Modify `backend/tests/unit/test_portfolio_models.py`: cover model, schema, and seed defaults.
- Modify `backend/tests/api/test_portfolio_subscriptions.py`: cover activation enforcement.
- Modify `backend/tests/api/test_portfolio_billing.py`: cover billing enforcement.

### Task 1: Persist the live gate

**Files:**
- Create: `backend/alembic/versions/v8w9x0y1z2a3_add_model_portfolio_live_gate.py`
- Modify: `backend/app/models/portfolio.py`
- Modify: `backend/app/schemas/portfolio.py`
- Test: `backend/tests/unit/test_portfolio_models.py`

- [ ] **Step 1: Add the failing schema assertion**

Add `live_enabled=True` to the existing `SimpleNamespace` in `test_portfolio_schema_serializes_orm_like_objects`, then assert:

```python
assert response.live_enabled is True
```

- [ ] **Step 2: Confirm the test fails on the server**

Run: `cd backend && uv run pytest tests/unit/test_portfolio_models.py -v`

Expected: FAIL because `ModelPortfolioResponse` has no `live_enabled` field.

- [ ] **Step 3: Add the migration**

```python
"""add model portfolio live gate

Revision ID: v8w9x0y1z2a3
Revises: u7v8w9x0y1z2
Create Date: 2026-08-30 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v8w9x0y1z2a3"
down_revision: str | None = "u7v8w9x0y1z2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_portfolios",
        sa.Column(
            "live_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        "UPDATE model_portfolios SET live_enabled = true WHERE slug = 'balanced'"
    )


def downgrade() -> None:
    op.drop_column("model_portfolios", "live_enabled")
```

- [ ] **Step 4: Map and serialize the field**

In `ModelPortfolio` add:

```python
live_enabled: Mapped[bool] = mapped_column(
    Boolean,
    default=False,
    server_default="false",
    nullable=False,
)
```

In `ModelPortfolioResponse` add:

```python
live_enabled: bool
```

- [ ] **Step 5: Run the focused test on the server**

Run: `cd backend && uv run pytest tests/unit/test_portfolio_models.py -v`

Expected: PASS.

### Task 2: Seed demo-first launch defaults

**Files:**
- Modify: `backend/scripts/seed_model_portfolios.py`
- Test: `backend/tests/unit/test_portfolio_models.py`

- [ ] **Step 1: Add the failing catalog test**

```python
def test_new_portfolios_launch_demo_first() -> None:
    by_slug = {portfolio["slug"]: portfolio for portfolio in MODEL_PORTFOLIOS}

    assert by_slug["balanced"]["live_enabled"] is True
    assert by_slug["starter"]["live_enabled"] is False
    assert by_slug["conservative"]["live_enabled"] is False
    assert by_slug["aggressive"]["live_enabled"] is False
```

- [ ] **Step 2: Confirm the test fails on the server**

Run: `cd backend && uv run pytest tests/unit/test_portfolio_models.py -v`

Expected: FAIL with `KeyError: 'live_enabled'`.

- [ ] **Step 3: Add the explicit value to each existing seed dictionary**

```python
STARTER_PORTFOLIO["live_enabled"] = False
CONSERVATIVE_PORTFOLIO["live_enabled"] = False
BALANCED_PORTFOLIO["live_enabled"] = True
AGGRESSIVE_PORTFOLIO["live_enabled"] = False
```

In the actual file, put each value directly in its corresponding dictionary.

- [ ] **Step 4: Run the seed tests on the server**

Run: `cd backend && uv run pytest tests/unit/test_portfolio_models.py -v`

Expected: PASS.

### Task 3: Enforce the gate in activation and billing

**Files:**
- Modify: `backend/app/services/portfolio/activation.py`
- Modify: `backend/app/services/portfolio/billing.py`
- Modify: `backend/app/api/portfolio_billing.py`
- Test: `backend/tests/api/test_portfolio_subscriptions.py`
- Test: `backend/tests/api/test_portfolio_billing.py`

- [ ] **Step 1: Add a disabled-live activation test**

```python
async def test_live_activation_rejects_demo_only_portfolio(
    self, client, db_session
) -> None:
    seed = await _seed_published_portfolio(db_session)
    await db_session.execute(
        update(ModelPortfolio)
        .where(ModelPortfolio.id == seed.portfolio_id)
        .values(live_enabled=False)
    )
    await db_session.commit()
    headers, _ = await _auth_user(client, user_id=91020)

    response = await client.post(
        "/api/portfolio-subscriptions",
        headers=headers,
        json=_live_activation_body(seed),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Live activation is not enabled for this portfolio. Try demo mode."
    )
```

- [ ] **Step 2: Add a disabled-live billing test**

```python
async def test_billing_status_disables_live_for_demo_only_portfolio(
    self, client, db_session
) -> None:
    seed = await _seed_published_portfolio(db_session)
    portfolio = await db_session.get(ModelPortfolio, seed.portfolio_id)
    assert portfolio is not None
    portfolio.live_enabled = False
    await db_session.commit()
    headers, _ = await _auth_user(client, user_id=93020)

    response = await client.get(
        "/api/portfolio-subscriptions/billing/status",
        headers=headers,
        params={
            "portfolio_id": seed.portfolio_id,
            "active_version_id": seed.version_id,
        },
    )

    assert response.status_code == 200
    assert response.json()["can_activate_live"] is False
    assert response.json()["message"] == (
        "This portfolio is currently available in demo mode only."
    )


async def test_checkout_rejects_demo_only_portfolio(
    self, client, db_session
) -> None:
    seed = await _seed_published_portfolio(db_session)
    portfolio = await db_session.get(ModelPortfolio, seed.portfolio_id)
    assert portfolio is not None
    portfolio.live_enabled = False
    await db_session.commit()
    headers, _ = await _auth_user(client, user_id=93021)

    response = await client.post(
        "/api/portfolio-subscriptions/billing/checkout",
        headers=headers,
        json=_checkout_body(seed),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "This portfolio is currently available in demo mode only."
    )
```

- [ ] **Step 3: Confirm both tests fail on the server**

Run: `cd backend && uv run pytest tests/api/test_portfolio_subscriptions.py tests/api/test_portfolio_billing.py -v`

Expected: FAIL because the services do not inspect `live_enabled`.

Before making the tests pass, set `live_enabled=True` in the existing published-
portfolio fixtures used by live activation and billing tests. Those fixtures model
the legacy Balanced behavior and must stay live-enabled.

- [ ] **Step 4: Block direct live activation**

Inside `if not data.is_demo:` and before conflict, billing, or wallet checks, add:

```python
if not portfolio.live_enabled:
    raise ValueError(
        "Live activation is not enabled for this portfolio. Try demo mode."
    )
```

- [ ] **Step 5: Make billing status fail closed**

Retain the portfolio returned by `_load_published_portfolio` and calculate:

```python
portfolio, _ = await _load_published_portfolio(
    db,
    portfolio_id,
    active_version_id,
)
live_enabled = bool(portfolio.live_enabled)
paid = beta_override or is_paid_billing_status(
    subscription.status if subscription else None
)
can_rebalance = paid and (
    beta_override
    or subscription is None
    or subscription.status not in REBALANCE_BLOCKING_STATUSES
)

if not live_enabled:
    message = "This portfolio is currently available in demo mode only."
```

Return `can_activate_live=live_enabled and paid`. Keep `paid` truthful for any
pre-existing billing record, and keep rebalance eligibility independent so turning
off new activation does not strand an existing live position.

- [ ] **Step 6: Refuse checkout with HTTP 409**

Add this exception in `billing.py`:

```python
class PortfolioLiveDisabledError(ValueError):
    pass
```

After loading the checkout portfolio, enforce:

```python
if not portfolio.live_enabled:
    raise PortfolioLiveDisabledError(
        "This portfolio is currently available in demo mode only."
    )
```

Catch it in `portfolio_billing.py`:

```python
except PortfolioLiveDisabledError as exc:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(exc),
    ) from exc
```

Add `PortfolioLiveDisabledError` to the existing import list from
`app.services.portfolio.billing` in the API module.

- [ ] **Step 7: Run the focused API tests on the server**

Run: `cd backend && uv run pytest tests/api/test_portfolio_subscriptions.py tests/api/test_portfolio_billing.py -v`

Expected: PASS.

### Task 4: Present demo-only state in the frontend

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/pages/PortfolioDetailPage.tsx`

- [ ] **Step 1: Type the response field**

Add to `ModelPortfolioListItem`:

```typescript
live_enabled: boolean
```

- [ ] **Step 2: Show a demo-first notice**

```tsx
{!portfolio.live_enabled && (
  <div
    className="rounded-xl px-4 py-3 text-sm text-tg-text"
    style={{ background: 'var(--tg-theme-secondary-bg-color)' }}
  >
    Demo-first launch: live activation will become available after review and an
    initial track record.
  </div>
)}
```

- [ ] **Step 3: Render billing and live activation only when enabled**

```tsx
{portfolio.live_enabled && (
  <>
    <BillingPanel
      portfolio={portfolio}
      billingStatus={billingStatus}
      busy={billingBusy}
      error={billingError}
      notice={billingNotice}
      onCheckout={handleCreateCheckout}
    />
    <LiveActivationPanel
      portfolio={portfolio}
      portfolioSubscription={livePortfolioSubscription}
      billingStatus={billingStatus}
      walletStatus={walletStatus}
      busy={liveActivationBusy}
      error={liveActivationError}
      notice={liveActivationNotice}
      onActivate={handleActivateLive}
      onCancel={handleCancelLive}
      onWalletSetup={() => navigate('/wallet')}
    />
  </>
)}
```

- [ ] **Step 4: Build the frontend on the server**

Run: `cd frontend && npm ci && npm run build`

Expected: TypeScript and Vite build complete successfully.

### Task 5: Verify, release, and publish demo versions

**Files:**
- No additional code files.

- [ ] **Step 1: Run server verification**

```bash
make lint
make typecheck
(cd backend && uv run pytest tests/unit/test_portfolio_models.py tests/unit/test_portfolio_builder.py tests/unit/test_portfolio_backtest.py -v)
(cd backend && uv run pytest tests/api/test_portfolio_subscriptions.py tests/api/test_portfolio_billing.py tests/api/test_portfolios.py -v)
(cd frontend && npm ci && npm run build)
```

Expected: all commands pass. API tests must use the isolated `copytrade_test` database on server port `5433`, never the production database.

- [ ] **Step 2: Commit and push through the normal release path**

```bash
git add backend frontend docs/superpowers/plans
git commit -m "feat: add staged model portfolio rollout"
git push origin main
```

Expected: `origin/main` contains the reviewed commit and no environment or secret files.

- [ ] **Step 3: Pull and deploy on the server**

```bash
git pull --ff-only origin main
make prod-check-target
make deploy
```

Expected: Alembic reaches `v8w9x0y1z2a3`, services restart through Docker Compose, and the healthcheck succeeds. Do not patch production files or services directly.

- [ ] **Step 4: Seed and preview strict drafts in the deployed backend**

Open a shell in the immutable production backend image:

```bash
APP_ENV_FILE=.env.prod docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml exec backend bash
```

Then run inside the container:

```bash
uv run python -m scripts.seed_model_portfolios
uv run python -m scripts.seed_model_portfolios --check
for slug in starter conservative aggressive; do
  uv run python -m scripts.build_model_portfolio_draft --portfolio-slug "$slug" --period allTime --dry-run
done
```

Expected: each preview has a valid trader count and `weight_sum=100.0`. Do not use `--internal-alpha-relaxed` for public versions.

- [ ] **Step 5: Create version 1 and profile-sized backtests**

```bash
for slug in starter conservative aggressive; do
  uv run python -m scripts.build_model_portfolio_draft --portfolio-slug "$slug" --period allTime
done

uv run python -m scripts.run_model_portfolio_backtest --portfolio-slug starter --version-no 1 --period-days 180 --initial-equity-usd 500 --initial-equity-usd 1000 --initial-equity-usd 5000
uv run python -m scripts.run_model_portfolio_backtest --portfolio-slug conservative --version-no 1 --period-days 180 --initial-equity-usd 1000 --initial-equity-usd 5000 --initial-equity-usd 10000
uv run python -m scripts.run_model_portfolio_backtest --portfolio-slug aggressive --version-no 1 --period-days 180 --initial-equity-usd 2000 --initial-equity-usd 5000 --initial-equity-usd 10000
```

Expected: each draft reports `version_no=1`; every backtest is saved and prints its `data_source`.

- [ ] **Step 6: Apply publication gates**

Publish only if the draft was built in strict mode, weights total `100.000%`, all profile caps pass, backtest limitations are visible, and the reviewer accepts the stored allocation facts. Treat `aggregate_metric_proxy` as limited evidence, not a daily-return backtest.

- [ ] **Step 7: Publish demo-first versions**

```bash
uv run python -m scripts.publish_model_portfolio_version --portfolio-slug starter --version-no 1 --approval-note "Approved for demo-first launch after strict review"
uv run python -m scripts.publish_model_portfolio_version --portfolio-slug conservative --version-no 1 --approval-note "Approved for demo-first launch after strict review"
uv run python -m scripts.publish_model_portfolio_version --portfolio-slug aggressive --version-no 1 --approval-note "Approved for demo-first launch after strict review"
```

Expected: the new portfolios become usable in demo; live activation and checkout remain blocked by `live_enabled=false`.

### Task 6: Promote portfolios one at a time

**Files:**
- Modify: `backend/scripts/seed_model_portfolios.py`
- Test: `backend/tests/unit/test_portfolio_models.py`

- [ ] **Step 1: Observe demo behavior**

Review at least two weekly reports for Starter and Conservative, and four for Aggressive. Do not promote while there are unresolved `failed_risk_check`, minimum-order, missed-fill, slippage, or wallet-execution issues.

- [ ] **Step 2: Change one reviewed seed gate**

For Starter, change only its existing value:

```python
"live_enabled": True,
```

- [ ] **Step 3: Update the exact seed assertion**

```python
assert by_slug["starter"]["live_enabled"] is True
```

- [ ] **Step 4: Verify, commit, push, deploy, and reseed**

```bash
(cd backend && uv run pytest tests/unit/test_portfolio_models.py tests/api/test_portfolio_subscriptions.py tests/api/test_portfolio_billing.py -v)
git add backend/scripts/seed_model_portfolios.py backend/tests/unit/test_portfolio_models.py
git commit -m "feat: enable Starter portfolio live activation"
git push origin main
git pull --ff-only origin main
make deploy-backend
APP_ENV_FILE=.env.prod docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml exec -T backend uv run python -m scripts.seed_model_portfolios
```

Expected: only Starter changes to live-enabled. Repeat with separate reviewed commits for Conservative and, last, Aggressive.

---

## Separate follow-up: backtest data quality

`trader_stats.daily_returns_pct_by_day` exists, but analytics currently writes `None`, so portfolio backtests normally use `aggregate_metric_proxy`. Do not derive percentage returns from realized PnL without trustworthy historical equity denominators. Plan a separate data-source feature to ingest daily account-value history, populate daily returns, snapshot them into allocations, and then validate `daily_snapshot` backtests.
