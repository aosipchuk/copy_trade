# New HyperLiquid Wallets Discovery Implementation Plan

> **For agentic workers:** execute task-by-task. Do not run local verification commands; this repository requires tests/builds on the server.

**Goal:** Detect newly funded HyperLiquid wallets before their first trades, qualify them by the balances of their upstream funding chain, expose a "Новые кошельки" selection, and let users auto-subscribe to qualified wallets for exactly 5 days. After 5 days, generated subscriptions must stop copying and close copied open positions.

**Architecture:** Add a dedicated new-wallet discovery pipeline beside the existing leaderboard and model-portfolio flows. The pipeline ingests incoming funding events, verifies that the target wallet has no prior perp fills, walks up to 3 funding-chain wallets, sums current balances, and qualifies the target when the chain total reaches the configured threshold. Qualified targets are inserted as `Trader` rows but remain separate from normal leaderboard discovery. A new parent strategy subscription creates ordinary `subscriptions` rows with `source_type = 'new_wallet'`, `expires_at = now + 5 days`, and standard copy settings. Execution guards stop expired subscriptions before close-position tasks run.

**Important data-source note:** HyperLiquid's public Info API can fetch non-funding ledger updates for a known user via `userNonFundingLedgerUpdates`, and those updates include deposits, transfers, and withdrawals. It is not a global "all new wallets" feed. Before production implementation, confirm or add a global funding-event source: HyperCore/Bridge2 indexer, explorer API, warehouse job, or another provider that emits `(target_wallet, source_wallet, amount, hash, time)`.

**Docs checked:** HyperLiquid Info endpoint docs document pagination limits and user-specific request bodies; the Perpetuals docs document `userNonFundingLedgerUpdates`; the Spot docs state `spotClearinghouseState` is the source of truth for balances under unified account or portfolio margin.

## Implementation Status (2026-07-20)

**Implemented in code:**

- Funding-event provider abstraction with HTTP/indexer production adapter and known-address ledger backfill adapter.
- HyperLiquid client extensions for non-funding ledger updates, spot balances, and auditable account-equity snapshots.
- New database models and Alembic migration for candidates, funding links, parent strategy subscriptions, child items, and child subscription expiry metadata.
- Funding-chain qualification service with three-step cumulative upstream balance checks, cycle/missing-source/balance-failure rejection, and qualified `Trader` upsert.
- Scheduler tasks for discovery, auto-attach, and expiry.
- Separate shadow-mode controls: `NEW_WALLET_DISCOVERY_ENABLED` gates the feature and `NEW_WALLET_AUTO_ATTACH_ENABLED` gates child subscription creation.
- New-wallet execution guard, expired-subscription exclusion, and first-trade empty-baseline signal detection fix.
- Leaderboard refresh no longer deactivates qualified/subscribed new-wallet trader rows before attach.
- Rejected/expired candidates are requeued when a later funding event arrives for the same target.
- Backend API for candidates, summary, strategy activation/detail/list/cancel, and admin rescan.
- Frontend API/types, `Новые` tab, candidate list/activation screen, and subscription detail screen.
- Focused tests added for ledger parsing, chain qualification, execution guard, first-trade baseline, expiry task, and API activation/cancel behavior.
- Data-source documentation and operational runbook.

**Blocked until explicit release/test approval:**

- Server-side execution of the new tests and frontend build. Project policy forbids local test/build verification, and production rules forbid copying uncommitted local changes to the server or changing the production server directly without explicit approval.
- Production rollout/shadow-mode validation with a real global funding-event provider.

**Server verification commands once a release/test path is approved:**

```bash
cd backend && uv run pytest tests/unit/test_hyperliquid_ledger_models.py tests/unit/test_new_wallet_chain.py tests/unit/test_subscription_execution_guards.py tests/unit/test_signal_detector_empty_baseline.py tests/unit/test_new_wallet_tasks.py tests/api/test_new_wallets.py -v
cd frontend && npm run build
```

---

## Phase 0: Product Rules And Data Source Validation

**Files:**
- Create: `docs/new_wallets_data_source.md`
- Create: `backend/app/services/hyperliquid/funding_events.py`
- Modify: `.env.example`
- Modify: `.env.prod.example`

- [ ] Define exact product semantics:
  - [ ] "New wallet" means no historical perp fills from `userFillsByTime` and no current open perp positions when first detected.
  - [ ] Funding-chain total excludes the new target wallet and includes only upstream funding wallets.
  - [ ] Chain depth is at most 3 upstream wallets: funder of target, funder of funder, funder of funder of funder.
  - [ ] The chain passes as soon as cumulative current balance is `>= NEW_WALLET_CHAIN_BALANCE_THRESHOLD_USD`.
  - [ ] If source wallet is missing, labeled exchange, unknown, duplicated in the chain, or balance calls fail after retries, candidate is held as `pending` or rejected with a reason.
  - [ ] Generated copy subscriptions last `NEW_WALLET_SUBSCRIPTION_TTL_DAYS`, default `5`.
- [ ] Create a `FundingEventProvider` interface returning normalized incoming funding events:
  - [ ] `target_address`
  - [ ] `source_address`
  - [ ] `amount_usdc`
  - [ ] `tx_hash`
  - [ ] `event_time`
  - [ ] `event_type`
  - [ ] `raw_event`
- [ ] Validate which provider can supply global events before the first trade:
  - [ ] Preferred production adapter: external HyperCore/Bridge2/indexer feed.
  - [ ] Test/backfill adapter: known-address `userNonFundingLedgerUpdates` fetcher.
  - [ ] Confirm event payload includes a usable source wallet for deposits and internal transfers; if not, resolve source wallet through tx hash/indexer.
- [ ] Add settings:
  - [ ] `NEW_WALLET_DISCOVERY_ENABLED=false`
  - [ ] `NEW_WALLET_AUTO_ATTACH_ENABLED=false`
  - [ ] `NEW_WALLET_CHAIN_BALANCE_THRESHOLD_USD=15000`
  - [ ] `NEW_WALLET_MAX_CHAIN_DEPTH=3`
  - [ ] `NEW_WALLET_SUBSCRIPTION_TTL_DAYS=5`
  - [ ] `NEW_WALLET_SCAN_INTERVAL_SECONDS=30`
  - [ ] `NEW_WALLET_DISCOVERY_LOOKBACK_HOURS=24`
  - [ ] `NEW_WALLET_MIN_INCOMING_AMOUNT_USD=100`
  - [ ] `NEW_WALLET_MAX_ACTIVE_PER_USER=20`
  - [ ] `NEW_WALLET_DEFAULT_MAX_PER_WALLET_USD=100`
- [ ] Document the selected provider, payload samples, rate limits, failure modes, and whether source-wallet labels are available.

## Phase 1: HyperLiquid Client Extensions

**Files:**
- Modify: `backend/app/services/hyperliquid/models.py`
- Modify: `backend/app/services/hyperliquid/info_client.py`
- Create: `backend/tests/unit/test_hyperliquid_ledger_models.py`

- [ ] Add Pydantic models for non-funding ledger updates:
  - [ ] `NonFundingLedgerUpdate`
  - [ ] `LedgerDelta`
  - [ ] Typed helpers for `deposit`, `withdraw`, `accountClassTransfer`, internal transfer, and unknown delta types.
- [ ] Add `HyperliquidInfoClient.get_non_funding_ledger_updates(address, start_time, end_time=None)`.
- [ ] Paginate by time because HyperLiquid docs state time-range responses are capped; stop on empty page or repeated timestamp/hash.
- [ ] Add `HyperliquidInfoClient.get_spot_balances(address)` using `spotClearinghouseState`.
- [ ] Add `get_account_equity_usd(address)` that returns a consistent balance snapshot:
  - [ ] Prefer existing `clearinghouseState.marginSummary.accountValue` for perp account value.
  - [ ] Also fetch spot USDC via `spotClearinghouseState` for unified/portfolio margin safety.
  - [ ] Persist `balance_source` in evidence JSON so future audits know which value was used.
- [ ] Reuse existing HL rate limiter and assign ledger/balance calls explicit weights.
- [ ] Unit-test payload parsing with representative ledger event fixtures and unknown delta fallback.

## Phase 2: Database Schema

**Files:**
- Create: `backend/app/models/new_wallet.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/subscription.py`
- Create: `backend/alembic/versions/<revision>_add_new_wallet_discovery.py`

- [ ] Create `new_wallet_candidates`:
  - [ ] `id`
  - [ ] `trader_id` nullable FK to `traders.id`
  - [ ] `hl_address` unique, normalized lowercase
  - [ ] `status`: `pending`, `qualified`, `rejected`, `subscribed`, `expired`, `disabled`
  - [ ] `detected_at`, `funded_at`, `qualified_at`, `last_checked_at`
  - [ ] `chain_depth`
  - [ ] `chain_total_balance_usd`
  - [ ] `threshold_usd_snapshot`
  - [ ] `reject_reason`
  - [ ] `first_seen_tx_hash`
  - [ ] `evidence_json`
- [ ] Create `new_wallet_funding_links`:
  - [ ] `id`
  - [ ] `candidate_id`
  - [ ] `depth` from 1 to 3
  - [ ] `wallet_address`
  - [ ] `funded_by_address`
  - [ ] `amount_usdc`
  - [ ] `event_time`
  - [ ] `tx_hash`
  - [ ] `balance_usd`
  - [ ] `balance_source`
  - [ ] `raw_event_json`
- [ ] Create `user_new_wallet_subscriptions` as the parent strategy subscription:
  - [ ] `id`
  - [ ] `user_id`
  - [ ] `status`: `active`, `paused`, `canceled`
  - [ ] `is_demo`
  - [ ] `total_allocation_usd`
  - [ ] `max_active_wallets`
  - [ ] `max_per_wallet_usd`
  - [ ] `copy_ratio_pct`, `stop_loss_pct`, `max_leverage`, `sizing_mode`, `allowed_coins`
  - [ ] `close_positions_on_expire`
  - [ ] `created_at`, `updated_at`, `canceled_at`
- [ ] Create `user_new_wallet_items`:
  - [ ] `id`
  - [ ] `user_new_wallet_subscription_id`
  - [ ] `candidate_id`
  - [ ] `subscription_id`
  - [ ] `trader_id`
  - [ ] `target_allocation_usd`
  - [ ] `status`: `active`, `expired`, `failed`, `removed`
  - [ ] `created_at`, `expires_at`, `ended_at`
  - [ ] `error_msg`
- [ ] Add `subscriptions.expires_at` and `subscriptions.ended_reason`.
- [ ] Update `subscriptions.source_type` check constraint to allow `new_wallet`.
- [ ] Add indexes:
  - [ ] candidate `status, detected_at`
  - [ ] candidate `hl_address`
  - [ ] links `candidate_id, depth`
  - [ ] parent `user_id, status, is_demo`
  - [ ] item `subscription_id`
  - [ ] item `expires_at` where `status = 'active'`
  - [ ] subscription `source_type, expires_at` where `is_active = true`
- [ ] Keep downgrade paths explicit for all constraints and indexes.

## Phase 3: Funding-Chain Qualification Service

**Files:**
- Create: `backend/app/services/new_wallets/discovery.py`
- Create: `backend/app/services/new_wallets/chain.py`
- Create: `backend/app/services/new_wallets/types.py`
- Modify: `backend/app/services/admin_trader_import.py` only if shared address normalization should move to a common helper.
- Create: `backend/tests/unit/test_new_wallet_chain.py`

- [ ] Implement `normalize_hl_address` in a shared module and reuse existing admin-import validation.
- [ ] Implement `is_wallet_new_for_copying(address)`:
  - [ ] Fetch up to one fill through `userFillsByTime`.
  - [ ] Fetch current positions.
  - [ ] Return false if fills exist or current positions exist.
- [ ] Implement `find_latest_incoming_funding(address, before_time=None)`:
  - [ ] Use the selected global funding provider when available.
  - [ ] Use `userNonFundingLedgerUpdates` for known-address backfill and chain traversal.
  - [ ] Sort by event time descending and choose the latest incoming transfer/deposit.
- [ ] Implement `build_funding_chain(target_address)`:
  - [ ] Start at target wallet.
  - [ ] For each depth from 1 to `NEW_WALLET_MAX_CHAIN_DEPTH`, find the latest incoming funding event for current wallet.
  - [ ] Extract upstream source wallet.
  - [ ] Fetch current upstream balance.
  - [ ] Add balance to cumulative total.
  - [ ] Stop and qualify when total reaches threshold.
  - [ ] Stop and reject when depth limit is reached without threshold.
  - [ ] Stop and reject on cycles or invalid source addresses.
  - [ ] Persist every inspected link for audit.
- [ ] Implement candidate statuses and reason codes:
  - [ ] `qualified`
  - [ ] `insufficient_chain_balance`
  - [ ] `already_trading`
  - [ ] `missing_funding_source`
  - [ ] `source_not_wallet`
  - [ ] `chain_cycle`
  - [ ] `balance_fetch_failed`
  - [ ] `provider_unavailable`
- [ ] Upsert a `Trader` row for qualified candidates:
  - [ ] `hl_address = target`
  - [ ] `display_name = NULL`
  - [ ] `is_active = true`
  - [ ] `has_perp_activity = NULL`
  - [ ] `last_seen_at = now`
  - [ ] Do not compute normal quality metrics until fills exist.
- [ ] Keep qualified new wallets out of normal `GET /traders` ranked results until `has_perp_activity` becomes true.
- [ ] Unit-test:
  - [ ] One-step pass with source balance `>= 15000`.
  - [ ] Three-step pass by cumulative balance.
  - [ ] Three-step fail below threshold.
  - [ ] Missing source fail.
  - [ ] Cycle detection.
  - [ ] Already-trading rejection.

## Phase 4: Discovery And Auto-Attach Background Tasks

**Files:**
- Create: `backend/app/tasks/new_wallets.py`
- Modify: `backend/app/core/scheduler.py`
- Create: `backend/tests/unit/test_new_wallet_tasks.py`

- [ ] Add Redis lock for discovery runs, similar to portfolio task locks.
- [ ] Add `discover_new_wallets_async()`:
  - [ ] Skip unless `NEW_WALLET_DISCOVERY_ENABLED`.
  - [ ] Pull funding events from provider since last cursor or configured lookback.
  - [ ] Dedupe by target address and tx hash.
  - [ ] Create or refresh candidate rows.
  - [ ] Run funding-chain qualification for bounded batch size.
  - [ ] Log counts for scanned, qualified, rejected, failed.
- [ ] Add `attach_qualified_new_wallets_async()`:
  - [ ] Find active `user_new_wallet_subscriptions`.
  - [ ] Find qualified candidates not already attached to each parent subscription.
  - [ ] Respect `max_active_wallets`.
  - [ ] Create child `subscriptions` with `source_type='new_wallet'`, `source_id=parent.id`, `expires_at=now+5 days`.
  - [ ] Create `user_new_wallet_items` with the same expiry.
  - [ ] For live users, require wallet and active approved agent.
  - [ ] Reuse one fetched `MarginSummary` per user to avoid repeated HL calls.
  - [ ] For demo users, create demo subscriptions with no wallet requirement.
- [ ] Add `expire_new_wallet_subscriptions_async()`:
  - [ ] Find active child items/subscriptions where `expires_at <= now`.
  - [ ] Mark subscription inactive first so no new signals execute.
  - [ ] Mark item `expired`.
  - [ ] Set `subscriptions.ended_reason='new_wallet_ttl_expired'`.
  - [ ] Close copied open positions for live subscriptions via `close_subscription_positions_async`.
  - [ ] Close demo positions via `close_demo_subscription_positions`.
  - [ ] Make the task idempotent so retries do not create duplicate close records.
- [ ] Register scheduler jobs:
  - [ ] discovery every `NEW_WALLET_SCAN_INTERVAL_SECONDS`
  - [ ] auto-attach every 30 seconds
  - [ ] expiry every 60 seconds

## Phase 5: Subscription Lifecycle And Copy Execution Guards

**Files:**
- Modify: `backend/app/services/subscription_service.py`
- Modify: `backend/app/services/portfolio/subscription_lifecycle.py`
- Modify: `backend/app/tasks/hl_tracker.py`
- Modify: `backend/app/services/copy_engine/executor.py`
- Modify: `backend/app/schemas/subscription.py`
- Create: `backend/tests/unit/test_subscription_execution_guards.py`
- Create: `backend/tests/unit/test_signal_detector_empty_baseline.py`

- [ ] Allow `create_subscription(..., source_type='new_wallet')` to copy new wallets before historical perp activity exists:
  - [ ] Require `Trader.is_active = true`.
  - [ ] Allow `Trader.has_perp_activity IS NULL`.
  - [ ] Still reject `Trader.has_perp_activity = false`.
  - [ ] Keep manual/model-portfolio behavior unchanged.
- [ ] Extend `subscription_execution_allowed_clause()`:
  - [ ] Manual remains allowed only when unmanaged.
  - [ ] Model portfolio remains allowed only when parent portfolio subscription status is executable.
  - [ ] New-wallet subscription is allowed only when:
    - [ ] `source_type = 'new_wallet'`
    - [ ] parent `user_new_wallet_subscriptions.status = 'active'`
    - [ ] `subscriptions.expires_at > now`
    - [ ] linked item status is `active`
- [ ] Update response schemas and frontend types to expose `expires_at` and `ended_reason`.
- [ ] Fix first-trade signal detection:
  - [ ] In `_poll_trader_positions_async`, distinguish `prev_raw is None` from `prev_positions == []`.
  - [ ] Only skip when no snapshot exists.
  - [ ] When the previous snapshot is an empty list and the current snapshot has a new position, detect an `OPEN` signal.
- [ ] Consider extending `_SNAPSHOT_TTL` or storing a durable baseline for new-wallet subscriptions so first trades are not missed after temporary downtime.
- [ ] Ensure `track_active_traders_async()` tracks new-wallet subscriptions even when `has_perp_activity` is still NULL.
- [ ] Ensure expired subscriptions are excluded from fan-out before close-position tasks start.
- [ ] Unit-test the empty-baseline case because it is central to copying the first trade.

## Phase 6: Backend API

**Files:**
- Create: `backend/app/api/new_wallets.py`
- Modify: `backend/app/api/router.py`
- Create: `backend/app/schemas/new_wallet.py`
- Create: `backend/app/services/new_wallets/activation.py`
- Create: `backend/tests/api/test_new_wallets.py`

- [ ] Add `GET /api/new-wallets/candidates`:
  - [ ] Query params: `status`, `limit`, `cursor`.
  - [ ] Return target wallet, status, detected time, qualified time, chain total, depth, threshold snapshot, reason, and compact funding-chain evidence.
  - [ ] Hide raw event JSON from normal users.
- [ ] Add `GET /api/new-wallets/summary`:
  - [ ] counts by status
  - [ ] active strategy subscription status for current user
  - [ ] current settings snapshot
- [ ] Add `POST /api/new-wallet-subscriptions`:
  - [ ] Create or reactivate the parent strategy subscription.
  - [ ] Validate live wallet/agent when `is_demo=false`.
  - [ ] Validate risk disclosure for live mode.
  - [ ] Return parent subscription plus active child items.
- [ ] Add `GET /api/new-wallet-subscriptions` and `GET /api/new-wallet-subscriptions/{id}`.
- [ ] Add `DELETE /api/new-wallet-subscriptions/{id}`:
  - [ ] Cancel parent.
  - [ ] Deactivate generated child subscriptions.
  - [ ] Optionally close positions immediately, based on request param and parent setting.
- [ ] Add admin-only endpoint `POST /api/admin/new-wallets/rescan` for manually resubmitting a target wallet during rollout.
- [ ] API-test:
  - [ ] Candidate list requires auth.
  - [ ] Live activation requires wallet and agent.
  - [ ] Demo activation works without wallet.
  - [ ] Activation creates child subscriptions for existing qualified candidates.
  - [ ] Expired children no longer execute.
  - [ ] Cancel deactivates generated subscriptions.

## Phase 7: Frontend

**Files:**
- Create: `frontend/src/api/newWallets.ts`
- Modify: `frontend/src/types/index.ts`
- Create: `frontend/src/pages/NewWalletsPage.tsx`
- Create: `frontend/src/pages/NewWalletSubscriptionDetailPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/TabBar.tsx`

- [ ] Add TypeScript types:
  - [ ] `NewWalletCandidate`
  - [ ] `NewWalletFundingLink`
  - [ ] `NewWalletSummary`
  - [ ] `UserNewWalletSubscription`
  - [ ] `UserNewWalletItem`
  - [ ] `NewWalletSubscriptionCreate`
- [ ] Add API client methods for candidate list, summary, activation, details, and cancel.
- [ ] Add a bottom-tab entry or in-app entry point named `Новые`.
- [ ] Build `NewWalletsPage`:
  - [ ] Header with strategy status and settings snapshot.
  - [ ] Qualified wallet list with chain total, depth, detected time, and copied/expired state.
  - [ ] Empty states for no candidates, discovery disabled, and data-source unavailable.
  - [ ] Activation modal with live/demo toggle, total allocation, max per wallet, max active wallets, copy ratio, stop-loss, max leverage, and fixed close-on-expiry behavior.
  - [ ] Clear warning that each generated wallet subscription expires after 5 days and copied positions are closed.
- [ ] Build detail page:
  - [ ] Active generated subscriptions.
  - [ ] Expiry countdown per wallet.
  - [ ] Realized/unrealized PnL where available.
  - [ ] Funding-chain evidence without overwhelming raw JSON.
- [ ] Keep UI dense and operational, consistent with current Telegram Mini App styling.
- [ ] Do not expose new-wallet candidates in normal `TradersPage` unless the user searches by exact address or the wallet later has confirmed perp activity.

## Phase 8: Observability, Controls, And Risk Limits

**Files:**
- Modify: `backend/app/core/logging.py` only if structured event names need centralization.
- Create: `docs/new_wallets_runbook.md`

- [ ] Add structured logs:
  - [ ] `new_wallet_event_ingested`
  - [ ] `new_wallet_candidate_qualified`
  - [ ] `new_wallet_candidate_rejected`
  - [ ] `new_wallet_user_attached`
  - [ ] `new_wallet_subscription_expired`
  - [ ] `new_wallet_close_positions_failed`
- [ ] Add Redis counters or DB aggregates for:
  - [ ] events scanned
  - [ ] candidates qualified/rejected
  - [ ] users attached
  - [ ] active child subscriptions
  - [ ] expiry failures
- [ ] Add hard safety limits:
  - [ ] max candidates processed per run
  - [ ] max active new-wallet subscriptions per user
  - [ ] max allocation per generated child
  - [ ] max chain requests per candidate
  - [ ] no duplicate active child per `(user_parent, candidate)`
- [ ] Add a kill switch:
  - [ ] `NEW_WALLET_DISCOVERY_ENABLED=false` stops discovery and auto-attach.
  - [ ] `NEW_WALLET_AUTO_ATTACH_ENABLED=false` stops only automatic child subscription creation.
  - [ ] Existing active child subscriptions continue until expiry unless an admin cancellation endpoint is used.
- [ ] Add runbook steps for provider outage, elevated false positives, HL 429s, and close-position failures.

## Phase 9: Server Verification And Rollout

**Files:**
- Add or modify focused tests under `backend/tests/unit/` and `backend/tests/api/`.

- [ ] Do not run local tests/builds.
- [ ] On the server, run focused backend unit tests:
  - [ ] funding-chain qualification
  - [ ] ledger parsing
  - [ ] subscription execution guards
  - [ ] first-trade empty-baseline detection
  - [ ] expiry task
- [ ] On the server, run API tests for new wallet endpoints.
- [ ] On the server, run frontend build after UI work.
- [ ] Roll out with discovery disabled:
  - [ ] deploy migrations
  - [ ] deploy code
  - [ ] verify app starts and existing subscriptions still execute
- [ ] Enable discovery in shadow mode:
  - [ ] ingest and qualify candidates
  - [ ] do not auto-attach users yet
  - [ ] inspect evidence for at least several real candidates
- [ ] Enable demo auto-attach first.
- [ ] Enable live auto-attach for admin/beta Telegram IDs.
- [ ] Enable live auto-attach broadly after close-position expiry behavior is verified.

## Acceptance Criteria

- [ ] A newly funded target wallet can be detected before any perp fill. Code path is implemented; production proof requires a real global funding-event provider.
- [x] The service walks up to 3 upstream wallets and qualifies only if cumulative upstream balance reaches the configured threshold.
- [x] Qualified targets appear in "Новые кошельки" with funding-chain evidence.
- [x] Users can activate the new-wallet strategy and receive generated child subscriptions automatically when auto-attach is enabled.
- [x] New-wallet subscriptions can copy the first opened position from an empty prior snapshot.
- [x] Each generated child subscription expires after 5 days by default.
- [x] Expired child subscriptions stop receiving new signals before close-position tasks execute.
- [x] Copied open positions from expired live subscriptions are always closed through the existing copy-engine close path.
- [x] Demo positions from expired demo subscriptions are always closed and realized in demo PnL.
- [ ] Existing manual and model-portfolio subscription behavior is unchanged by code inspection and targeted tests added; server regression execution is pending.

## Key Risks And Mitigations

- [ ] **No global funding feed in HyperLiquid Info API:** use a provider abstraction and complete a data-source spike before building auto-attach.
- [ ] **First trade can be missed by current snapshot logic:** fix `prev_raw is None` vs empty list before enabling this feature.
- [ ] **False positives from exchange or aggregator source wallets:** add source labels if available and reject/hold labeled exchange sources by default.
- [ ] **Rate limits from chain traversal:** cap scans per run, cache balances briefly, and reuse the existing HL rate limiter.
- [ ] **Subscription explosion:** enforce max active generated children per user and per-run attach limits.
- [ ] **Expired subscription race:** update execution guard and mark inactive before submitting close orders.
- [ ] **New wallets have no quality metrics:** keep them out of normal leaderboard lists until actual perp activity is confirmed.
