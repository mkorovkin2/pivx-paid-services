# Phase 1 publication and future hosting examples

Phase 1 runs Core, the backend and MCP locally. Publish only aggregate static output to free GitHub Pages after a public destination is selected and reviewed. Use `operator --config config.toml export-site --output EMPTY_DIRECTORY`; the result has relative assets and stats.json suitable for a project subpath. It does not include a backend, wallet, ledger or secrets, and does not publish itself.

The included systemd/Caddy files are future examples for an existing independently owned host, not a tested deployment or an instruction to buy hosting. Adjust Python paths, service accounts and private local Core RPC credentials if Phase 2 is later authorized. No domain, certificate, cloud resource or PIVX-owned infrastructure was changed.

The pivx.ai deliverable is a proposal asking maintainers to link the public quickstart; editing their site is outside this project. Mainnet remains disabled and requires separate Phase 2 review.
