# New HyperLiquid Wallets Data Source

## Product Semantics

- A target wallet is considered new only when `userFillsByTime` returns no perp fills and `clearinghouseState` returns no open perp positions at detection time.
- The funding-chain balance total excludes the target wallet and includes only upstream funding wallets.
- The chain depth is capped by `NEW_WALLET_MAX_CHAIN_DEPTH`, default `3`.
- Qualification succeeds as soon as cumulative upstream current balance is greater than or equal to `NEW_WALLET_CHAIN_BALANCE_THRESHOLD_USD`, default `15000`.
- Qualification rejects candidates with missing source wallets, non-wallet sources, cycles, provider failures, or insufficient upstream balance.
- Generated child subscriptions expire after `NEW_WALLET_SUBSCRIPTION_TTL_DAYS`, default `5`, and copied positions are closed on expiry.

## Provider Contract

Production discovery requires a global funding-event provider. HyperLiquid Info API is user-scoped and cannot emit all new wallet deposits.

The configured HTTP provider is read from:

```dotenv
NEW_WALLET_DISCOVERY_ENABLED=false
NEW_WALLET_AUTO_ATTACH_ENABLED=false
NEW_WALLET_FUNDING_EVENTS_URL=
NEW_WALLET_FUNDING_EVENTS_API_KEY=
```

Global scan request:

```http
GET {NEW_WALLET_FUNDING_EVENTS_URL}?since_ms=1721457600000&limit=100&cursor=opaque
Authorization: Bearer <optional key>
```

Targeted chain request:

```http
GET {NEW_WALLET_FUNDING_EVENTS_URL}?target_address=0x...&before_ms=1721457600000&limit=1
Authorization: Bearer <optional key>
```

Accepted response shapes:

```json
{
  "events": [
    {
      "target_address": "0x...",
      "source_address": "0x...",
      "amount_usdc": "500.0",
      "tx_hash": "0x...",
      "event_time": 1721457600000,
      "event_type": "deposit"
    }
  ],
  "next_cursor": "opaque"
}
```

or a bare JSON array of the same event objects.

## HypurrScan Feed Adapter

For initial shadow rollout, `NEW_WALLET_FUNDING_EVENTS_URL` may point to:

```dotenv
NEW_WALLET_FUNDING_EVENTS_URL=https://api.hypurrscan.io/transfers
```

The adapter accepts successful HypurrScan `spotSend`, `sendAsset`, and `usdSend`
USDC transfers and normalizes them as `source_address -> target_address` funding
events. Failed transactions, non-USDC transfers, system transfers, and bridge
validator vote events are ignored. Chain traversal for already-known addresses
still uses `userNonFundingLedgerUpdates`.

## Ledger Adapter

`LedgerFundingEventProvider` uses `userNonFundingLedgerUpdates` for known-address backfill and chain traversal. It intentionally raises provider-unavailable for global scans because public Info API has no global feed.

If a ledger event lacks a usable `source_address`, the candidate is rejected with `missing_funding_source` unless an external provider resolves source by tx hash.

## Balance Snapshot

`HyperliquidInfoClient.get_account_equity_usd()` fetches both:

- `clearinghouseState.marginSummary.accountValue`
- `spotClearinghouseState` USDC total

The larger value is used as the upstream wallet balance. Evidence stores both raw values and `balance_source`.

## Failure Modes

- Provider unavailable: discovery logs `new_wallet_provider_unavailable`; existing child subscriptions continue until expiry.
- Provider 5xx/timeout: discovery logs `new_wallet_event_fetch_failed`; cursor is not advanced.
- HL 429/balance failures: candidate rejects with `balance_fetch_failed`; rate limiter still applies to Info API calls.
- Exchange/aggregator source wallets: provider should include labels when available. Until labels exist, non-wallet or missing source values are rejected.
