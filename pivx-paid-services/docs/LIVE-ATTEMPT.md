# Live testnet verification attempt

Attempted at 2026-10-17T18:07:53.797838+00:00 following the request to run the real testnet6 purchase verification.

Result: **blocked before wallet creation or payment**. Fresh preflight found no pivxd/pivx-cli, failed DNS for github.com and computingcache.com, and PermissionError on a loopback bind. The attempt to execute that read-only preflight outside the sandbox was rejected by the configured approval policy because sandbox_approval is false. No live payment test, wallet generation, faucet claim or transaction occurred. Spend remains $0.

Fresh evidence: [live-attempt-preflight.json](../test-output/live-attempt-preflight.json). The previous passing local tests remain fixture/simulation evidence; they were not substituted for this requested on-chain test.

The next real execution requires an environment where Core can be installed/run, public testnet hosts can be reached, and local RPC/service listeners can bind. The [prepared testnet procedure](TESTNET.md) and [purchase commands](../README.md) remain ready for that environment.
