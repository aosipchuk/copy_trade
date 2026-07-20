# New Wallets Runbook

## Default Rollout

1. Deploy code and migrations with `NEW_WALLET_DISCOVERY_ENABLED=false` and `NEW_WALLET_AUTO_ATTACH_ENABLED=false`.
2. Configure and validate `NEW_WALLET_FUNDING_EVENTS_URL`.
3. Enable discovery for shadow observation.
4. Inspect `new_wallet_candidates.evidence_json` and `new_wallet_funding_links` for several real candidates.
5. Enable demo users first.
6. Enable live users after expiry closes copied positions successfully.

## Operational Controls

- Feature kill switch: `NEW_WALLET_DISCOVERY_ENABLED=false` stops discovery and new auto-attach.
- Auto-attach kill switch: `NEW_WALLET_AUTO_ATTACH_ENABLED=false` keeps discovery in shadow mode.
- Threshold: `NEW_WALLET_CHAIN_BALANCE_THRESHOLD_USD`.
- Chain depth: `NEW_WALLET_MAX_CHAIN_DEPTH`.
- Per-user cap: `NEW_WALLET_MAX_ACTIVE_PER_USER`.
- Per-run scan cap: `NEW_WALLET_MAX_CANDIDATES_PER_RUN`.
- Per-run attach cap: `NEW_WALLET_MAX_ATTACH_PER_RUN`.

## Logs

- `new_wallet_event_ingested`
- `new_wallet_candidate_qualified`
- `new_wallet_candidate_rejected`
- `new_wallet_user_attached`
- `new_wallet_subscription_expired`
- `new_wallet_close_positions_failed`

## Provider Outage

1. Confirm `new_wallet_provider_unavailable` or `new_wallet_event_fetch_failed` frequency.
2. Keep discovery disabled if provider payloads are incomplete or delayed.
3. Do not manually patch candidates in production DB. Use admin rescan after provider recovery.
4. Existing child subscriptions should be left to expire and close copied positions unless risk requires canceling parent subscriptions sooner.

## Elevated False Positives

1. Disable discovery.
2. Inspect rejected/qualified evidence JSON for shared source wallets.
3. Add source labels or provider-side exchange filtering.
4. Rescan affected candidates through `POST /api/admin/new-wallets/rescan`.

## HL 429s

1. Reduce `HL_RATE_PER_SEC`.
2. Reduce `NEW_WALLET_MAX_CANDIDATES_PER_RUN`.
3. Increase `NEW_WALLET_SCAN_INTERVAL_SECONDS`.
4. Confirm normal leaderboard and copy execution latency recovered.

## Close-Position Failures

1. Search `new_wallet_close_positions_failed`.
2. For demo failures, check `user_new_wallet_items.error_msg`.
3. For live failures, use the existing subscription close path after verifying wallet/agent status.
4. Do not reactivate expired child subscriptions; create a fresh parent strategy if needed.
