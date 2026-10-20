# PIVX services MCP

This checkout launcher exposes `list_services`, `get_quote`, `create_invoice`, `check_invoice`, and `call_service` over MCP stdio. The implementation lives in the sibling `pivx-paid-services/paid_services/mcp.py` so the HTTP and MCP contracts share one tested version. Keep both directories together until public repository packaging is chosen.

Run `python3 run.py --url http://127.0.0.1:8787 --session-file /PRIVATE/PATH/service-session.json` with Python 3.11+. An absolute `run.py` path works from any working directory. It never pays an invoice itself. Configure the separate `paid_services.testnet_wallet` server to pay through dedicated local Core testnet6; upstream Agent Kit remains mainnet-only.

See the sibling backend README and `docs/TESTNET.md` for configuration and the complete flow. No paid account or external Python dependency is needed.
