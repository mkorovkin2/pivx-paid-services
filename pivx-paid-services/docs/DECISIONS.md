# V-511 implementation decisions

## Product and costs

Implement the ticket's chain/governance analytics and privacy-coin/agent digest contracts. The digest currently provides official release-feed excerpts, not a validated research synthesis. V-508/V-509 integration can enrich the same versioned contracts. Default illustrative prices are 0.05/0.25/2 tPIV; demand and prices are unvalidated. Fixed-testnet reference arithmetic avoids a price-API dependency. Optional CoinGecko is only a free indicative reference. Phase 1 spends $0 and uses public data plus dedicated local testnet nodes.

## Payment path

Use local Core testnet6 for receiving and a separate dedicated Core payer through the new testnet-only MCP tool. Pin the genesis and chain identifier, verify testnet address encoding, and isolate data/signing material. Upstream Agent Kit needs a wider wallet/network port than endpoint configuration, and cannot be built in this sandbox because dependencies cannot be fetched. No upstream patch is presented as proven.

Reuse the recovered Python invoice/token/MCP code after reviewing it against this ticket. Its previous mainnet-only validation and instructions were replaced. Merchant Kit's address-only matcher does not implement encrypted memo invoice binding. Its recovered SerializedNote adapter is retained only for simulation, with recipient bytes checked against the configured address. The live adapter uses Core's own decrypted, address-scoped received-note RPC.

Each Core receipt is keyed by a SHA256 of txid and shielded output index, stored in the historical `nullifier` ledger column. This is a stable output identity, not a claim that a txid alone proves payment. Core's receiver view supplies memo, amount and inclusion evidence; wallet ownership is enforced by that RPC. Confirmation depth is checked against the current canonical chain. Spent received notes remain discoverable with Core, and archived receipts survive restarts.

The separate payer validates the complete testnet invoice, allows only configured receiver addresses, limits each amount and persists a send intent before calling `shieldsendmany`. Identical successful retries return the journaled txid. An uncertain result stops for operator inspection and is never automatically rebroadcast. The service backend has no spend or wallet-unlock method. Neither tool creates, imports or unlocks a real wallet in this environment.

## Accounting and access

Invoices use an exact random 16-character base32 ID in `pivxai:inv:ID` and expire with the ten-minute quote. Full payment included before expiry may finish confirming afterward. Late/partial-expired/excess amounts become private credit after three confirmations. One confirmation grants small-call access; three are required at/above the illustrative 1-reference-unit threshold. Integer atomic units, durable reservations and idempotent request IDs prevent duplicate fulfillment or credit use.

Signed HS256 access tokens expire in at most 15 minutes. Persisted call counts and pass expiry enforce scope and quota independently of refreshes. Single-call access lasts 24 hours from recognition; passes last seven days. Failed source requests consume no quota. Changed chain ancestry or a re-included receipt quarantines affected invoices/accounts, including credit-funded entitlements. Previously delivered data cannot be recalled.

## Public data and privacy

The optional mainnet analytics gateway is a separate read-only data source. Payment verification and sends always use pinned local testnet6. Source timestamps, method provenance and digest coverage are visible; unavailable sources fail explicitly. Current analytics does not duplicate V-508's proposal summarization.

Stats contain calls, gross confirmed tPIV volume, payment counts and unique active anonymous integration sessions in UTC. No addresses, memos, txids or stable customer IDs are exported. Shielded receipts cannot establish unique sender wallets; external tester counts need a voluntary beta roster, not a hash presented as wallet identity.

## Publication and operations

GitHub Pages receives only exported static assets and aggregate JSON. Backend, MCP and nodes run locally in Phase 1. No PIVX-owned deployment, paid hosting, mainnet or private team access is required. The pivx.ai deliverable is a proposal for maintainers to link the service; editing their site is outside scope. Outreach drafts remain unsent pending review. Public repository layout can be selected when a destination is provided; the sibling MCP launcher currently shares the tested backend module.

Full x402, fiat, swaps, real-value operations and production hosting are deferred. The runbook separates current testnet operations from future Phase 2 considerations.
