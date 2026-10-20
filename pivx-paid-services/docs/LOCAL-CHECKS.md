# Latest verification without payments

Fresh run: 2026-10-17T18:10:32.648246+00:00 through 2026-10-17T18:10:36.952158+00:00. Exit status: 0.

- 91 Python tests passed, with ResourceWarnings treated as errors. Coverage includes invoice accounting, mocked Core receiver/payer, HTTP over socketpairs, MCP stdio, retries, tokens, restart recovery and aggregate exports.
- 9 JavaScript tests passed, 0 failed; JavaScript syntax check passed.
- Fresh analytics-call and seven-day-digest simulations both completed and returned results. The simulation states real_transactions=0.
- Python syntax, both example TOML files, package TOML, JSON schema, local Markdown links and both MCP checkout launchers from a separate working directory passed.
- No network checks, real payments, wallet creation, faucet claims, publication or spending occurred.

Evidence: [full verification log](../test-output/local-checks-20261017T181032648246Z/verify.log), [simulation proof](../test-output/local-checks-20261017T181032648246Z/simulation-proof.json), and [machine-readable summary](../test-output/latest-local-checks.json).

These results verify local code and explicit fixture/simulation behavior. Real chain decryption/payments, listening HTTP/browser behavior and live latency are outside this run.
