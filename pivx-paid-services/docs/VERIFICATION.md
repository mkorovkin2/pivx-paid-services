# Verification report — integrated workspace

Latest requested run without payments: [LOCAL-CHECKS.md](LOCAL-CHECKS.md), completed 2026-10-17T18:10:36Z. All 91 Python and 9 JavaScript tests passed again, alongside a fresh two-service simulation and static/launcher checks. No network checks or real payments were attempted.

Verified on 2026-10-17 in `/Users/michaelkorovkin/Desktop/pivx-projects/V-511-paid-agent-services/pivx-paid-services`, using Python 3.14.7 and Node. The implementation is now in the requested workspace, rather than only in the earlier recovery directory. All six upstream clones remain clean. No wallet was created, faucet claim made, transaction sent, money spent, publication performed or message posted.

## Passing evidence

Final full command:

```sh
PYTHON_BIN=/opt/homebrew/bin/python3 NODE_BIN=/Users/michaelkorovkin/.local/bin/node sh scripts/verify.sh
```

Exit status: 0. Full output: [verification.log](verification.log).

| Check | Result |
| --- | --- |
| Python unit/integration/CLI/RPC-contract tests | 91 passed, ResourceWarnings treated as errors |
| JavaScript syntax | Passed |
| JavaScript behavior harness | 9 passed, 0 failed |
| Fresh temporary headless demo | Analytics call and seven-day digest pass both paid/fulfilled; `simulation=true`, `real_transactions=0` |
| Both checkout MCP launchers from a different working directory | Help commands passed with absolute Python paths |
| Python syntax, example TOML, JSON schema and local Markdown links | Passed |
| Static aggregate exporter after final footer copy change | Targeted artifact tests passed |

The Python suite covers exact/wrong memos; testnet address checks; mainnet/old-testnet rejection; current Core `initial_block_downloading` and explicit older sync aliases; exact Decimal parsing; receipt identity/amount/confirmation validation; change and wrong-recipient exclusion; partial/late/excess payments; credits/refunds/cancellations; duplicate detection; restart/sweep recovery; token scope/quota/replay; concurrent requests; reorg quarantine; privacy; free-source failures; HTTP parser/authentication/headers; MCP stdio; and CLI lost-response recovery.

A combined integration test exercises service MCP dispatch, actual HTTP framing over accepted socketpairs, the separate payer MCP dispatch, Core-shaped receiver receipts, confirmation/access/result handling and idempotent send/result retries. The Core RPC transport and note contents are explicit local fixtures; no live send or cryptographic decryption occurs.

Payer tests verify testnet-only invoices, allowed receiver addresses, amount limits, exact plain-text memo, separate credentials/proof timeout, durable pre-broadcast intent, replay without a second send and refusal to automatically retry uncertain sends. A 60-payment simulation validates report aggregation and explicitly cannot satisfy a live latency target.

The JavaScript harness verifies service composition, readiness recovery, shared session creation, interrupted invoice recovery, safe source text insertion, zero/unavailable statistics, GitHub Pages-relative fetching and fixed tPIV labels. This is behavior coverage, not rendered-browser verification.

## Reviewable artifacts

- [Headless simulation proof](../test-output/demo-proof.json): both service results and aggregates, without private invoice/session material.
- [Simulated static stats page](../test-output/pages-simulation/index.html) and [stats.json](../test-output/pages-simulation/stats.json): marked simulation, unpublished. Serve/export via HTTP to load the JSON; opening an HTML file directly may block fetches.
- [Simulated latency report](../test-output/latency-simulation.json): labelled simulation and below the real sample minimum.
- [Environment preflight](../test-output/preflight.json): checked at 2026-10-17T18:00:55Z. Reproduce with `python3 scripts/preflight.py --output test-output/preflight.json`.
- [Testnet feasibility and live procedure](TESTNET.md), [runbook](RUNBOOK.md), [decision record](DECISIONS.md), and [unsent outreach drafts](OUTREACH-DRAFTS.md).

Runtime data, keys, private configuration, invoice/session files and test-output artifacts are ignored for publication. The verification log contains only test names and explicit simulation results. The exported page contains aggregate JSON and public assets only.

## Current execution limits

| Check | Observed result | What remains unverified |
| --- | --- | --- |
| Core executables | Neither `pivxd` nor `pivx-cli` found | Core build/version, wallet creation and sync |
| Cargo build | Workspace-local cache avoids the read-only user-cache failure, then GitHub DNS fails fetching pinned librustpivx | Actual upstream compilation; the network error is not a source compilation failure |
| Public testnet RPC/faucet | Shell cannot resolve computingcache.com; web tool could not retrieve candidate RPC/faucet pages | Availability, actual chain identity, faucet funds and endpoint compatibility |
| Local server listener | Loopback bind returns PermissionError | Server accept/listen loop, browser layout and interactive demo |
| Core receiver/payer | Source interfaces inspected and fixture-tested | Actual Sapling memo encryption/decryption, funded shield-to-shield sends, receiver behavior and canonical chain observation |
| External data | Fixture-tested public gateway/feed adapters | Live source availability/freshness and richer V-508/V-509 integration |
| Live performance | Zero actual payments | ≥50-payment p95 ≤120s and beta median/success metrics |
| Publication/beta | Local export and drafts only | Public repo/Pages, maintainer proposal, demo recording, ≥5 external testers and ≥3 distinct clients |

These restrictions are properties of this sandbox, not evidence that public hosts are down. No sandbox escalation or paid replacement infrastructure was used. A real testnet purchase must be completed on a network-enabled machine with compatible Core before claiming the ticket's end-to-end acceptance criterion.

## Remaining product work

The current digest is a source-linked release excerpt service; the quality/demand of a richer privacy-coin/AI research digest is not established. Prices are illustrative. True unique-wallet metrics cannot be derived from shielded receiver receipts, so anonymous integration counts are named accurately. A voluntary tester roster is needed for the external-participant KPI.

No mainnet or PIVX-owned deployment is planned in Phase 1. The maintainer deliverable is a proposal to link a verified public quickstart. Mainnet/paid operations require the ticket's separate Phase 2 sign-off and code review.
