# Testnet6 feasibility and next live checks

## Decision

Use a dedicated local PIVX Core testnet6 receiver and a separate local Core payer for Phase 1. The receiver's read-only RPC feeds the invoice ledger. A small separate payer MCP/CLI tool provides automation without pretending upstream Agent Kit already supports testnet. All application changes are outside the six upstream clones; no unbuildable Rust patch was offered as working code.

This is a source-backed implementation decision, not a completed network proof. The current sandbox cannot resolve the public hosts or bind a local listening socket; neither `pivxd` nor `pivx-cli` is installed. Core was not built, wallets were not generated, faucet funds were not claimed and no transaction was sent. There is no request for money or existing wallet keys.

## Source findings in this checkout

| Component | Verified source evidence | Implication |
| --- | --- | --- |
| Core at `bd8a672d1d959db7cc6157e40c89dc1829d87abc` | `src/chainparams.cpp` defines CTestNetParams as testnet6; genesis below, `chain=test`, `ptestsapling` shield HRP, transparent prefix 139 and BIP44 coin type 1 | This checkout contains testnet6 parameters; the old question about release v5.6.1 is not resolved by assuming release compatibility |
| Core RPC | `src/chainparamsbase.cpp` uses data subdirectory `testnet6` and default RPC port 51475 | Use explicit dedicated data directories and loopback-only RPC |
| Core receiver | `src/wallet/rpcwallet.cpp:listreceivedbyshieldaddress` waits for wallet sync, checks address ownership and returns amount, hex memo, output index, confirmations and blockheight | Receiver-side decryption supplies evidence beyond a payer's txid claim; the API includes spent notes through `GetFilteredNotes(..., false, false)` |
| Core sender | `shieldsendmany` takes a source address and recipient objects; `sapling_operation.cpp:GetMemoFromString` takes plain UTF-8 and pads to 512 bytes | Send the invoice memo as text; decode the received hex representation before matching |
| Core sync status | `src/rpc/blockchain.cpp` emits `initial_block_downloading` | The adapter checks this actual field and accepts explicit older aliases; missing or contradictory flags fail |
| Agent Kit at `a031c6cab36fe03a606167fbf1db55dacc9f800a` | Hardcoded mainnet RPC/explorers; Wallet Kit v0.2.4 dependency | Endpoint-only patch would not establish testnet correctness |
| Wallet Kit at `f59aab7a333079c5bc8161e4da1dc2d6d1539bc8` | Mainnet coin type/prefix, MAIN_NETWORK key encodings/decryption, embedded mainnet checkpoints, MainNetwork transaction builder | A real Agent Kit testnet port must cover all these surfaces and storage separation |
| librustpivx at `0cbf7b895d1644a0152bb5f523eb88078a10ce3a` | TestNetwork and testnet HRPs exist | Useful primitives, but insufficient interoperability proof for the wallet stack |
| Merchant Kit at `6affdab329449167029fce16aac732f21a4fe560` | Wallet Kit v0.2.2; shield matcher binds receiving address and ignores memos | Not used as the live Phase 1 matcher; the recovered snapshot adapter remains simulation-only |
| MyPIVXWallet at `2d38cd2b16b93ccaabd0aa17c4dd0aff831e1576` | Testnet HRP/coin type are configured, but test chain config still has old endpoints and a starting shield height 1,503,537 | A hosted testnet6 wallet may differ; do not assume this clone is ready for the current chain |

Pinned testnet6 genesis:

```text
00000f254358fe046377c224341c794b7faf942a6311a7ece773d3c378239c54
```

The backend and payer require the same genesis and `chain=test` before accepting incoming payments or initiating a send. A previous testnet, regtest and mainnet are rejected. Payment addresses must have a valid testnet Bech32 checksum. Encoding checks do not prove a synthetic address has a valid curve point; only real node-generated addresses belong in live configuration.

## Local checks and environment evidence

A normal Cargo invocation could not write to the sandbox's read-only user Cargo cache. Retrying with workspace-local `CARGO_HOME` and `CARGO_TARGET_DIR` passed that filesystem boundary but failed resolving github.com while fetching pinned librustpivx. The failure does not establish that the upstream source fails to compile. Exact dependency versions remain preserved in upstream lockfiles.

A direct request to `https://computingcache.com/pivx/rpc/testnet/getblockchaininfo` failed DNS resolution. A web-tool attempt could not retrieve that endpoint or the faucet either. A loopback bind probe returned `PermissionError: Operation not permitted`. These are this execution environment's limits, not evidence that the public services are down.

## Prepared live procedure

1. On a machine where networking/listeners are allowed, build this pinned Core checkout or obtain and verify a public testnet6-compatible binary. Record the actual executable revision/version. Follow upstream build instructions and Sapling parameter verification; do not silently substitute an old release.
2. Start two dedicated Core instances with `-testnet`, distinct absolute data directories, loopback RPC and different P2P/RPC ports. Suggested receiver ports are P2P 51474/RPC 51475; payer ports are P2P 51476/RPC 51477. Keep RPC credentials in owner-only private configuration. Do not reuse an existing mainnet wallet/data directory.
3. For each instance, use `pivx-cli -testnet -datadir=ABSOLUTE_PATH getblockchaininfo` and `getblockhash 0`; confirm synced state and the pinned genesis. Wait for actual sync, then create separate `getnewshieldaddress` addresses. A view-only receiver is supported by the receiving RPC source but remains unproven at runtime.
4. Get a payer transparent address with `getnewaddress`, claim free testnet funds from the candidate faucet, wait for confirmation, then shield part of that balance using Core `shieldsendmany "from_transparent" [{"address":"PAYER_SHIELD_ADDRESS","amount":...}]`. Record private evidence and fees. Faucet availability, limits and live network identity must be verified when doing this; no purchase or mainnet substitution is allowed.
5. Fill the two example TOML files with the real testnet addresses and local RPC URLs. Set service RPC credentials separately from payer credentials. Keep the payer's receiver allowlist narrow. Start the service and verify `/readyz` semantically after reconciliation.
6. Perform the three-command purchase in the README or connect both MCP servers. Confirm one shield-to-shield payment, exact decrypted invoice memo, exact amount, canonical block/hash, confirmations, scoped token and service response. Save private evidence with versions, network identity, timestamps and source responses; do not publish wallet material.
7. Test retries, late/partial/excess/duplicate payments, restart recovery and delayed confirmations against the real receiver. Run at least 50 small one-confirmation payments and record detection/access/result timing. Use the `latency` operator report as an aggregate; also preserve send timestamps for broadcast-to-result measurements.
8. Record a complete agent purchase only after the real flow passes. Publish aggregate statistics and invite external testers after reviewing the prepared artifacts.

The service's optional public mainnet analytics source is read-only public chain data. Its URL and returned `network.chain=main` never change the payment network. Empty `analytics_rpc_url` selects local testnet analytics explicitly. A source outage fails the quote/service; it never fabricates a governance response.

## Remaining acceptance gates

| Gate | State |
| --- | --- |
| Source feasibility and Core fallback implementation | Complete locally |
| Fixture-tested invoice, receiver, payer, HTTP and MCP contracts | Complete locally; see VERIFICATION.md |
| Real shielded memo payment and automatic agent fulfillment | Pending networking, Core binary and real testnet execution |
| 50-payment p95 ≤120s | Pending real measurements |
| Browser/listener and recorded agent demo | Pending an environment that permits them |
| Public repository, GitHub Pages and maintainer proposal | Prepared locally; destination/review pending |
| 100 testnet calls, five external testers, three MCP clients, ≥98% success and median ≤90s | Pending public beta; cannot be established by local fixtures |
