# Public outreach drafts — unsent

These are reviewable drafts, not messages that have been posted. Replace placeholders only after a real testnet purchase and public repository are available. No Discord, private repository, internal dashboard or PIVX deployment access is required.

## Maintainer proposal

Title: Testnet6 memo-paid agent services: public demo and proposed llms.txt link

We're prototyping two public-data services that agents can buy with shielded testnet6 tPIV: PIVX chain/governance analytics and a source-linked privacy-coin/agent digest. Invoices bind through encrypted memo `pivxai:inv:<id>`; the dedicated Core receiver verifies memo, amount and confirmations before granting service access.

The service MCP exposes list_services, get_quote, create_invoice, check_invoice and call_service. A separate testnet Core wallet MCP tool supplies the paying-client fallback while Agent Kit's broader wallet/network testnet port remains open.

Repository: [INSERT PUBLIC REPOSITORY]. Real testnet demo/evidence: [INSERT VERIFIED DEMO]. Aggregate-only stats: [INSERT PAGES URL]. The service runs locally, uses public data and free testnet funds, and does not request deployment on PIVX infrastructure.

Would you consider linking the verified quickstart from pivx.ai's llms.txt? Feedback on the public RPC compatibility and a future explicit-network Agent Kit interface would also help. Stats expose counts and volume, without addresses, memos or txids.

## External tester invitation

We're looking for five builders to try an agent buying a PIVX service on testnet6. Use free faucet tPIV, request an invoice through MCP, pay with its encrypted memo, and receive analytics or a source-linked digest automatically.

Quickstart: [INSERT VERIFIED QUICKSTART]. Demo: [INSERT VERIFIED DEMO]. Feedback: [INSERT PUBLIC ISSUES URL]. No mainnet PIV, purchases, paid API account or private team access is needed.

Please report your MCP client/version, whether the flow completed, approximate timing and any errors. Keep wallet keys, private invoice details and transaction evidence out of public issues. We will report aggregate results and tester participation without claiming anonymous session counts identify unique wallets.

## Short X post

Can an agent buy a PIVX service without a manual payment check? We're testing that on testnet6: shielded tPIV, an encrypted invoice memo, and a result returned through MCP. Looking for five builders to try it with free faucet funds. [INSERT VERIFIED QUICKSTART]

## Public repository issue template

- Client name/version and operating system:
- Service/product requested:
- Completed quote → invoice → testnet payment → result:
- Approximate elapsed time and retry behavior:
- Safe error code or reproducible steps:
- Optional improvement suggestion:

Do not include seeds, spending/viewing keys, session/access tokens, invoice memos, wallet addresses or txids in a public issue. Provide private payment evidence only through a separately agreed operator support process.
