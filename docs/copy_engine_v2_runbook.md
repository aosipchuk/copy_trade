# Copy Engine v2 operator runbook

## Safety state

`COPY_ENGINE_V2_LIVE_ENABLED=false` is the required default and the required
state during migration. The feature flag stops new live intents; it does not
resolve an order whose exchange status is already unknown. Never reactivate v1
execution for a subscription that has entered v2.

Migration pauses every existing live subscription and managed parent, pauses
active managed children, ends legacy demo sessions without deleting history,
and marks v1 signals as non-dispatchable. There is no bulk or automatic resume.

## Pause and block reasons

- `engine_v2_reconciliation_required`: migrated subscription; user must inspect
  the account, select a dedicated copy account, run preflight and resume.
- `preflight_required`: newly created live subscription.
- `settings_changed_preflight_required`: risk settings changed while live.
- `unknown_open_order`, `unknown_exchange_fill`, `unknown_position`,
  `unexplained_position_delta`: external or ambiguous activity. Keep the account
  blocked; do not send a corrective order.
- `opposing_subscription_targets`: two strategies request opposite exposure in
  one market. Resolve the subscriptions before clearing the block.
- `exchange_state_unavailable`: temporary API uncertainty. Retry; do not call it
  drift until exchange facts prove drift.

## Manual remediation and resume

1. Keep live execution disabled while investigating migration or drift.
2. Compare every default DEX and HIP-3 position and open order with the UI/API.
3. Resolve pending/unknown legacy and v2 intents from exchange order status and
   fills. Never assume a timeout means no order was placed.
4. Close positions and cancel orders manually until the selected copy account is
   flat.
5. Select only the master or a server-verified owned subaccount and acknowledge
   the dedicated-account rule.
6. Run `POST /copy-execution/preflight`. Every check must pass.
7. Resume each manual subscription or managed parent explicitly. Existing leader
   positions are baseline-only and are not chased.

Clearing a block only returns the account to `paused`; it never activates a
subscription.

## Rollout

1. Record the deployed commit, current Alembic revision, active live counts,
   pending/unknown trades, and all account positions/open orders.
2. Create a database backup/restore point.
3. Confirm `.env.prod` contains `COPY_ENGINE_V2_LIVE_ENABLED=false`.
4. Run `make deploy-copy-engine-v2`. It builds images, stops the old backend and
   scheduler, migrates, and starts the new backend with live disabled.
5. Verify `/health`, `/health/copy-engine-v2`, migration pause SQL, logs and UI.
   No exchange order is expected.
6. Perform testnet smoke. Then enable live only as a separate reviewed config
   change and restart.
7. Resume one internal canary after flat preflight. Approve broader manual resume
   only after observing registry freshness, reconciliation lag, unknown intents,
   rejects and drift blocks.

## Rollback

After any v2 intent exists, do not use Alembic downgrade as an operational
rollback. Set the live flag false, pause v2 accounts/subscriptions and deploy a
known image through the normal release path. Never auto-resume v1 subscriptions.

## Testnet smoke

`backend/scripts/smoke_copy_engine_v2.py` refuses mainnet and requires
`--ack-testnet`. Use a disposable dedicated test master/subaccount and finish by
closing positions and cancelling orders. Keep only redacted `oid`/`cloid`
correlation in release notes; never store agent keys or signatures.
