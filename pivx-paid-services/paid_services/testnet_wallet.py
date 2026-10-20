"""Dedicated Core testnet6 payer: a CLI and separate MCP tool.

No wallet import, unlock, faucet claim, mainnet or arbitrary RPC tool is exposed.
An uncertain send is journaled before RPC and never automatically repeated.
"""
import argparse
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import tomllib
from types import SimpleNamespace
from urllib.parse import urlsplit

from .config import safe_url
from .crypto import canonical, digest, shield_address_valid
from .errors import ServiceError, require
from .mcp import serve, tool
from .network import ASSET, NETWORK
from .sources import Chain


TOOLS = [tool("pay_testnet_invoice", "Pay one verified testnet6 service invoice from the dedicated shielded Core wallet. Only configured receiver addresses are permitted. Fees are additional. Repeating the identical invoice returns its journaled txid; an uncertain send requires local operator recovery.",
              {"invoice": {"type": "object"}}, ["invoice"], False)]


@dataclass
class WalletConfig:
    data_dir: Path
    address: str
    allowed_receivers: list
    rpc_url: str
    max_amount_tpiv: str = "10"
    network: str = NETWORK

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        raw = tomllib.loads(path.read_text())
        raw["data_dir"] = (path.parent / raw["data_dir"]).resolve()
        config = cls(**raw)
        config.validate()
        return config

    def validate(self):
        safe_url(self.rpc_url)
        if self.network != NETWORK or not shield_address_valid(self.address):
            raise ValueError("Payer requires a testnet6 shielded address")
        if urlsplit(self.rpc_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Payer RPC must be local Core, not a public endpoint")
        if not isinstance(self.allowed_receivers, list) or not self.allowed_receivers or not all(
            shield_address_valid(a) and a != self.address for a in self.allowed_receivers
        ):
            raise ValueError("Configure distinct testnet receiver addresses explicitly")
        limit = Decimal(self.max_amount_tpiv)
        if not limit.is_finite() or not 0 < limit <= 100:
            raise ValueError("Per-invoice tPIV limit must be positive and at most 100")


class PayerRPC(Chain):
    ALLOWED = Chain.ALLOWED | {"shieldsendmany"}

    def __init__(self, config):
        # Sapling proving can outlast the short read-only data-source timeout.
        super().__init__(SimpleNamespace(rpc_url=config.rpc_url, rpc_mode="core", receiver="payer", rpc_timeout_seconds=180),
                         auth_env_prefix="PIVX_PAYER_RPC")


class TestnetWallet:
    def __init__(self, config, rpc=None, clock=time.time):
        config.validate()
        self.config, self.rpc, self.clock = config, rpc or PayerRPC(config), clock
        config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(config.data_dir, 0o700)
        self.path = config.data_dir / "testnet-sends.sqlite3"
        with closing(self.connect()) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS sends (invoice_id TEXT PRIMARY KEY, binding TEXT NOT NULL, status TEXT NOT NULL, txid TEXT, started_at INTEGER NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            binding = canonical({"network": NETWORK, "address": config.address, "rpc_url": config.rpc_url})
            previous = connection.execute("SELECT value FROM settings WHERE key='wallet'").fetchone()
            if previous and previous[0] != binding:
                raise ValueError("Payer journal belongs to another wallet or node")
            connection.execute("INSERT OR IGNORE INTO settings VALUES ('wallet',?)", [binding])
        os.chmod(self.path, 0o600)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def invoke(self, name, arguments):
        require(name == "pay_testnet_invoice", 400, "unknown_tool", "Unknown wallet tool")
        require(isinstance(arguments, dict) and set(arguments) == {"invoice"},
                400, "invalid_arguments", "Supply only invoice")
        return self.pay(arguments["invoice"])

    def pay(self, invoice):
        require(isinstance(invoice, dict), 400, "invalid_invoice", "Invoice must be an object")
        identifier = invoice.get("invoice_id")
        require(isinstance(identifier, str) and re.fullmatch(r"[A-Z2-7]{16}", identifier) is not None,
                400, "invalid_invoice", "Invalid invoice ID")
        require(invoice.get("simulation") is False and invoice.get("network") == NETWORK and invoice.get("payment_asset") == ASSET,
                400, "wrong_network", "Only real testnet6 tPIV invoices are accepted")
        require(invoice.get("address") in self.config.allowed_receivers and invoice.get("memo") == "pivxai:inv:" + identifier,
                400, "invalid_invoice", "Receiver or invoice memo is not permitted")
        amount = invoice.get("amount_to_send_sat")
        require(type(amount) is int and 0 < amount <= Decimal(self.config.max_amount_tpiv) * 100_000_000,
                400, "amount_limit", "Invoice amount exceeds the configured testnet limit or is zero")
        decimal = format(Decimal(amount) / 100_000_000, ".8f")
        require(invoice.get("amount_to_send_piv") == decimal, 400, "invalid_invoice", "Invoice amount representations disagree")
        binding = digest(canonical([NETWORK, invoice["address"], invoice["memo"], amount]))
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT * FROM sends WHERE invoice_id=?", [identifier]).fetchone()
            if previous:
                connection.rollback()
                require(previous["binding"] == binding, 409, "invoice_changed", "Invoice changed since its first send")
                require(previous["status"] == "broadcast", 409, "send_uncertain", "Send outcome is uncertain. Inspect the dedicated wallet before any recovery; no resend was attempted.")
                return {"network": NETWORK, "payment_asset": ASSET, "txid": previous["txid"], "replayed": True}
            require(type(invoice.get("expires_at")) is int and invoice["expires_at"] > self.clock()
                    and invoice.get("status") in {"pending", "partially_paid"} and invoice.get("receiver_ready") is True,
                    400, "invoice_unavailable", "Invoice expired, is already paid, or receiver is unavailable")
            # Preflight before reserving the send. A wrong network never creates an intent.
            self.rpc.require_network()
            connection.execute("INSERT INTO sends VALUES (?,?,'submitting',NULL,?)", [identifier, binding, int(self.clock())])
            connection.commit()
        try:
            # Core AmountFromValue accepts an exact decimal string. The memo is
            # plain UTF-8 on send, hex on listreceivedbyshieldaddress output.
            txid = self.rpc.call("shieldsendmany", [self.config.address,
                                 [{"address": invoice["address"], "amount": decimal, "memo": invoice["memo"]}], 1])
            require(isinstance(txid, str) and re.fullmatch(r"[0-9a-f]{64}", txid) is not None,
                    503, "send_uncertain", "Core did not return a valid transaction ID")
        except Exception:
            raise ServiceError(503, "send_uncertain", "Send outcome is uncertain. Check the dedicated Core wallet; this invoice will not be sent again automatically.") from None
        with closing(self.connect()) as connection:
            connection.execute("UPDATE sends SET status='broadcast',txid=? WHERE invoice_id=?", [txid, identifier])
        return {"network": NETWORK, "payment_asset": ASSET, "txid": txid, "replayed": False,
                "notice": "Broadcast only; the service receiver must independently verify payment."}


def main():
    parser = argparse.ArgumentParser(description="Dedicated testnet6 Core payer; never mainnet")
    parser.add_argument("--config", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Expose the separate wallet MCP tool over stdio")
    pay = sub.add_parser("pay", help="Pay an invoice JSON file once")
    pay.add_argument("--invoice-file", required=True)
    args = parser.parse_args()
    try:
        wallet = TestnetWallet(WalletConfig.load(args.config))
        if args.command == "serve":
            serve(wallet, tools=TOOLS, name="pivx-testnet-core-wallet",
                  instructions="Pay only testnet6 invoices returned by the service. Never pay simulation invoices. An uncertain send needs local operator investigation; do not change invoice IDs or delete the journal to retry.")
        else:
            print(canonical(wallet.pay(json.loads(Path(args.invoice_file).read_text()))))
    except (ValueError, OSError, ServiceError) as error:
        output = {"error": {"code": error.code, "message": error.message}} if isinstance(error, ServiceError) else {"error": {"code": "configuration", "message": "Invalid payer configuration or local I/O"}}
        print(canonical(output), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
