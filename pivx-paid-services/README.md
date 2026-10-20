# PIVX paid agent services — testnet6

Two public-data services with memo-bound invoices, a local Core receiver, scoped access, an HTTP API, five MCP service tools, a separate testnet Core payer tool, and aggregate statistics. Python 3.11+ standard library is sufficient for runtime. Node is used only for frontend behavior tests. No paid API, hosting account, mainnet funds or third-party Python dependency is needed.

**Current state:** integrated in this workspace and tested locally with explicit fixtures. Real testnet6 memo encryption/decryption, payment broadcasts, live feeds, listening HTTP/browser behavior and publication remain unverified. See [verification](docs/VERIFICATION.md) and [testnet feasibility](docs/TESTNET.md). No PIVX-owned infrastructure is used or changed.

## Run the zero-cost demonstration

From this directory, using Python 3.11+:

```sh
python3 -m paid_services demo --headless --data-dir ./data/my-simulation
```

Both purchases exercise quotes, invoices, receipt matching, confirmations, tokens, results and aggregate stats. The output states `simulation: true` and `real_transactions: 0`. Only chain receipts, prices and service inputs are simulated. Do not send funds to a simulation address; its checksummed encoding is a fixture, not a funded wallet. For the interactive demo on a machine that permits local listeners, omit `--headless` and open `http://127.0.0.1:8787`.

```sh
PYTHON_BIN=/opt/homebrew/bin/python3 NODE_BIN=/Users/michaelkorovkin/.local/bin/node sh scripts/verify.sh
```

Set those variables to your Python 3.11+ and Node executables. The script runs Python tests, JavaScript syntax/behavior tests and a fresh temporary headless demo. HTTP tests use actual request/response bytes over accepted socketpairs; they do not prove the TCP listening loop or a browser rendering.

## What is implemented

| Component | Behavior |
| --- | --- |
| Catalog and quotes | Analytics call, digest call or seven-day pass; integer atomic units; ten-minute expiry; default fixed testnet reference |
| Testnet identity | Testnet address prefix/checksum, Core `chain=test`, exact testnet6 genesis and synchronized-node check; mainnet is refused |
| Receiver | Local Core `listreceivedbyshieldaddress` supplies decrypted notes for the configured owned address, including spent receipts; exact memo, atomic amount, canonical block/time and confirmation checks |
| Access | One confirmation below the illustrative reference threshold, three at/above it; short-lived signed tokens; persistent call quotas/pass expiry and idempotent result retries |
| Exceptions | Partial top-ups, expired/late/excess credit, duplicate detection, restart recovery, reorg quarantine and refund reservations; the backend never sends funds |
| Analytics | Public chain/governance fields from Core RPC, with source provenance; receiver/payment chain is testnet6 |
| Digest | Source-linked official release-feed excerpts for PIVX, Monero, Zcash and agent payments; coverage/window declared; no invented synthesis |
| MCP | `list_services`, `get_quote`, `create_invoice`, `check_invoice`, `call_service` |
| Paying client | Separate `pay_testnet_invoice` MCP/CLI tool uses a dedicated local Core wallet, receiver allowlist, amount limit and durable send journal |
| UI and stats | Checkout, quickstart and aggregate dashboard; GitHub Pages-compatible static export; no payment identifiers in public JSON |
| Operations | Readiness, SQLite backup, stats/site export and latency report |

Default illustrative prices are **0.05 tPIV/query, 0.25 tPIV/digest, 2 tPIV/seven-day pass**. tPIV has no cash value. The historical configuration names `analytics_usd`, `digest_usd`, `week_usd` and `piv_usd` represent reference arithmetic only in fixed-testnet mode. Optional `coingecko_reference` mode fetches a free indicative mainnet PIV/USD price; it does not make tPIV valuable or enable mainnet payments. Demand/prices remain unvalidated.

The richer V-508/V-509 outputs are not assumed available. Current adapters use direct Core public-chain data and official release feeds; a release excerpt digest is narrower than a completed AI research product. Response contracts are in [service-schemas.json](docs/service-schemas.json).

## Real testnet setup and three-command purchase

First complete the dedicated Core sender/receiver setup and live checks in [TESTNET.md](docs/TESTNET.md). Copy `config.example.toml` to ignored `config.toml` and `wallet-config.example.toml` to ignored `wallet-config.toml`; replace placeholders with testnet addresses. Keep RPC credentials in the operator's environment or a private ignored file, never in committed config. Both node RPC URLs must be loopback. Start the service:

```sh
python3 -m paid_services serve --config config.toml
```

In a separate terminal, after the receiver is ready and the payer has free faucet tPIV in its shielded balance:

```sh
umask 077
python3 -m paid_services invoice --service analytics --request-id first-purchase > invoice.json
python3 -m paid_services.testnet_wallet --config wallet-config.toml pay --invoice-file invoice.json
python3 -m paid_services result --invoice-id INVOICE_ID_FROM_FILE --service analytics --request-id first-result
```

The three purchase commands follow `umask`. Preserve the invoice/session files and request IDs. The sender adds the exact plain UTF-8 memo; Core returns received memos as hex. Fees are additional, and the sender only broadcasts once per invoice ID. An uncertain send stops for private operator inspection instead of retrying a payment. These commands are prepared and fixture-tested; a real on-chain quickstart has not yet been demonstrated. Upstream Agent Kit is mainnet-only and is not a valid payer for this Phase 1 configuration.

## MCP configuration

Replace all executable/config paths with absolute paths for this checkout. The launchers work from any working directory; no package install is necessary.

```json
{
  "mcpServers": {
    "pivx-services": {
      "command": "/ABS/PATH/python3",
      "args": ["/ABS/PATH/V-511-paid-agent-services/pivx-services-mcp/run.py", "--url", "http://127.0.0.1:8787", "--session-file", "/PRIVATE/PATH/services-session.json"]
    },
    "pivx-testnet-wallet": {
      "command": "/ABS/PATH/python3",
      "args": ["/ABS/PATH/V-511-paid-agent-services/pivx-services-mcp/wallet.py", "--config", "/PRIVATE/PATH/wallet-config.toml", "serve"]
    }
  }
}
```

Expose the payer credentials only to the wallet process. An agent calls the service tools to obtain an invoice, passes that complete invoice object to `pay_testnet_invoice`, polls `check_invoice` every ten seconds, and uses `call_service`. The services MCP never spends; the payer MCP never grants service access.

## HTTP and operations

The authenticated API provides sessions, service catalog, quotes, invoices, service calls, account credit and refund/cancellation requests under `/v1/`. `GET /healthz` is liveness; `GET /readyz` requires fresh successful receiver reconciliation. `/stats.json` contains aggregate counts, gross confirmed tPIV volume and unique active client sessions, not unique wallets. No addresses, memos, txids or stable payer identifiers are exported.

```sh
python3 -m paid_services operator --config config.toml status
python3 -m paid_services operator --config config.toml backup --output /PRIVATE/BACKUP/services.sqlite3
python3 -m paid_services operator --config config.toml latency
python3 -m paid_services operator --config config.toml export-site --output ./test-output/pages
```

Static export requires an empty directory and includes only the dashboard, assets and aggregates. It uses relative URLs for GitHub Pages project paths; it does not publish anything. Latency reports explicitly label simulation and insufficient samples. The live requirement is at least 50 one-confirmation payments with p95 detection-to-fulfillment ≤120 seconds; fixture timing is not evidence for it.

See [runbook](docs/RUNBOOK.md), [decisions](docs/DECISIONS.md), [outreach drafts](docs/OUTREACH-DRAFTS.md) and [x402 mapping](docs/X402.md). Phase 1 excludes mainnet, paid hosting/APIs, buying PIV, PIVX-owned deployments and a full x402 facilitator. Mainnet requires a separately reviewed Phase 2 implementation and explicit sign-off.
