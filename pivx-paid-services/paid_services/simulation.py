"""Explicitly simulated, zero-cost receipts. Never enabled by live configuration."""
import json
import os
from pathlib import Path
import time

from .config import Config
from .crypto import ALPHABET, digest, polymod
from .errors import ServiceError, require
from .network import SAPLING_HRP


def simulation_address():
    hrp = [ord(c) >> 5 for c in SAPLING_HRP] + [0] + [ord(c) & 31 for c in SAPLING_HRP]
    data = [0] * 69
    check = polymod(hrp + data + [0] * 6) ^ 1
    return SAPLING_HRP + "1" + "".join(ALPHABET[v] for v in data + [(check >> (5 * (5 - i))) & 31 for i in range(6)])


class Simulation:
    def __init__(self, root, clock=time.time):
        self.root, self.clock = Path(root), clock
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "simulation-chain.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"height": 100, "times": {"100": int(clock())}, "notes": []}

    def config(self, port=8787):
        return Config(data_dir=self.root / "data", address=simulation_address(), wallet_path=self.root / "simulation-wallet.json",
                      wallet_fingerprint=digest("SIMULATION-ONLY"), rpc_url="http://127.0.0.1:1", port=port,
                      public_url=f"http://127.0.0.1:{port}", receiver="snapshot", demo=True)

    def require_network(self):
        return {"network": "testnet6", "simulation": True}

    def tip(self):
        return self.state["height"]

    def block_hash(self, height):
        require(str(height) in self.state["times"], 503, "simulation_height", "Unknown simulated block")
        return digest("simulated-block:" + str(height))

    def header(self, block_hash):
        height = next(int(h) for h in self.state["times"] if self.block_hash(int(h)) == block_hash)
        return {"height": height, "time": self.state["times"][str(height)], "hash": block_hash, "confirmations": self.tip() - height + 1}

    def advance(self, config, db=None, invoice=None):
        height = self.state["height"] + 1
        self.state["height"] = height
        self.state["times"][str(height)] = int(self.clock())
        candidates = [invoice] if invoice else db.rows("SELECT id AS invoice_id,amount-credit_used AS amount_to_send_sat FROM invoices WHERE status='pending' AND created_at<=?", [int(self.clock()) - 4]) if db else []
        for candidate in candidates:
            memo = "pivxai:inv:" + candidate["invoice_id"]
            if any(note["memo"] == memo for note in self.state["notes"]):
                continue
            self.state["notes"].append({"note": {"value": candidate["amount_to_send_sat"], "recipient": [0] * 43}, "memo": memo,
                                       "height": height, "nullifier": digest("simulated-receipt:" + memo), "witness": ""})
        self.path.write_text(json.dumps(self.state))
        wallet = {"version": 1, "extfvk": "SIMULATION-ONLY", "last_block": height, "unspent_notes": self.state["notes"],
                  "seed": "NO-REAL-KEYS", "mnemonic": "NO-REAL-KEYS"}
        temporary = config.wallet_path.with_suffix(".next")
        temporary.write_text(json.dumps(wallet))
        os.chmod(temporary, 0o600)
        temporary.replace(config.wallet_path)
        return height

    def price(self):
        return {"piv_usd": "1", "updated_at": int(self.clock()), "source": "LOCAL SIMULATION (not a market price)"}

    def service(self, name, params):
        require(isinstance(params, dict) and (not params if name == "analytics" else set(params) <= {"topic"}),
                400, "invalid_parameters", "Invalid service parameters")
        common = {"schema_version": "1.0", "generated_at": int(self.clock()), "simulation": True}
        if name == "analytics":
            next_superblock = (self.tip() // 144 + 1) * 144
            return {**common, "network": {"chain": "SIMULATED", "blocks": self.tip()}, "connections": 0,
                    "next_superblock_height": next_superblock, "blocks_until_superblock": next_superblock - self.tip(),
                    "masternodes": {"total": 0}, "proposals": [], "sources": [{"name": "Local test fixture"}]}
        require(name == "digest", 404, "unknown_service", "Unknown service")
        return {**common, "window": {"from": int(self.clock()) - 604800, "to": int(self.clock())},
                "method": "Local simulation; no collected news", "partial_coverage": False, "items": [],
                "coverage": [{"name": "Local test fixture", "status": "ok", "fetched_at": int(self.clock())}]}
