# Phase 1 testnet operations

## Setup and health

Use dedicated Core testnet6 receiver and payer nodes with separate data directories and RPC credentials. Follow TESTNET.md and verify the pinned genesis before generating addresses or claiming free faucet tPIV. The service's Core account needs read-only chain methods plus `listreceivedbyshieldaddress`. The payer is separate and only exposes a validated invoice payment. Neither application imports, exports or unlocks wallet keys. Back up real wallets privately under operator control.

Run `python3 -m paid_services serve --config config.toml`. `/healthz` is process liveness. `/readyz` means a fresh complete receiver tick succeeded against the configured testnet6 chain. A successful quote additionally checks service source availability. No health endpoint proves the on-chain payment milestone by itself.

Use `operator --config config.toml status` for safe error codes, `backup --output NEW_PRIVATE_FILE` for an owner-only SQLite online backup, and `export-stats --output FILE` for aggregate JSON. Back up the signing key and configuration separately. Do not replace/delete a ledger, signing key or payer send journal to bypass a recovery error.

## Invoice reconciliation

The local private ledger binds exact memo, amount, recipient, canonical block and confirmations. Wrong/malformed memos grant no access. Unknown invoice IDs remain unowned receipts; do not automatically reassign them. A complete timely payment may finish confirming after its deadline. Partial top-ups retain the original deadline. Expired partial, late and excess amounts become account credit after three confirmations; credit reservations are atomic. Failed service sources do not consume quota.

Core's received-note RPC includes spent notes and is scoped to the configured owned address. The historical snapshot receiver is simulation-only. A stale, unsynced, wrong-network or failed receiver pauses new purchases and service consumption. On failure, inspect the safe watcher code, node sync/genesis, RPC access and Core receiver ownership. Local note metadata and journal/database contents are private.

## Reorgs and retries

A changed chain anchor rechecks receipt inclusion. Orphaned or re-included outputs quarantine the affected invoice and owning account; existing access and dependent credit cannot silently reactivate. Preserve evidence and reconcile the customer's actual obligation privately. There is no automatic unfreeze command.

Service invoice/result retries use the same request IDs. The separate payer records intent before broadcast. Identical successful retries return its stored txid; an intent without a recorded txid raises `send_uncertain`. Inspect the payer wallet's transaction history and receiver evidence before recovery. Never change invoice IDs or delete the send journal simply to get another send through. No automated recovery command is supplied until real Core behavior is verified.

## Testnet refunds and future sweeps

Refund requests reserve confirmed private account credit and never broadcast from the service. They may be cancelled before a send. An unused paid invoice may be cancelled for confirmed credit; delivered results cannot be cancelled.

For a testnet refund, independently check the request, testnet destination, account credit, payer/receiver state and fee. Send through the separately operated Core wallet once, then record it with `operator --config config.toml record-refund --refund-id ID --txid VERIFIED_TXID --fee-sat FEE`. The refund amount sent is gross reserved credit minus its fee. A recorded txid is an operator assertion, not automated chain verification; monitor confirmation separately. Dust can remain credit. Do not use mainnet funds.

Automated sweeps are outside Phase 1. For a future Phase 2 runbook, back up receipts/wallet/ledger, reconcile all customer obligations, preserve refund/credit reserves and sweep only settled surplus with separately controlled spending keys. Mainnet requires a separate code/configuration review and Michael's sign-off; this application rejects it.

## Stats, evidence and beta

`operator --config config.toml export-site --output EMPTY_DIRECTORY` prepares a GitHub Pages-compatible static dashboard and aggregate JSON; no hosting is provisioned and nothing is published. Export after reconciliation and refresh the files as beta activity changes. No address, memo, txid or wallet/customer identifier belongs in public evidence.

`operator --config config.toml latency` reports one-confirmation sample counts and p50/p95 from first receiver observation to access/result. It states whether data is simulated and refuses a live success claim for simulations or fewer than 50 access samples. Keep broadcast timestamps separately; this report excludes pre-detection broadcast time. Real network tests must satisfy the ticket's ≥50-payment p95 ≤120-second target.

Before inviting testers, demonstrate one real agent purchase and validate actual source data. Then track ≥100 paid testnet calls, ≥5 external testers, ≥3 MCP clients, ≥98% success, median fulfillment ≤90 seconds, and at least one public maintainer response. Keep tester/client participation separate from anonymous session counts. These are future observed results, not local implementation claims.

The maintainer handoff requests a link from pivx.ai/llms.txt. It does not require access to or deployment on PIVX-owned infrastructure. Review the unsent drafts in OUTREACH-DRAFTS.md before publication.
