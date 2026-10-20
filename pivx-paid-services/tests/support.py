import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from paid_services.app import Application
from paid_services.config import Config
from paid_services.crypto import ALPHABET, digest, polymod
from paid_services.db import Database
from paid_services.errors import ServiceError
from paid_services.payments import PaymentWatcher
from paid_services.network import SAPLING_HRP


def simulated_address():
    expanded = [ord(c) >> 5 for c in SAPLING_HRP] + [0] + [ord(c) & 31 for c in SAPLING_HRP]
    data = [0] * 69
    checksum = polymod(expanded + data + [0] * 6) ^ 1
    return SAPLING_HRP + "1" + "".join(ALPHABET[x] for x in data + [(checksum >> (5 * (5 - i))) & 31 for i in range(6)])


class Clock:
    def __init__(self):
        self.value = 1_800_000_000

    def __call__(self):
        return self.value


class FakeChain:
    def __init__(self, clock):
        self.clock, self.height = clock, 100
        self.variants = {}
        self.times = {100: clock()}

    def tip(self):
        return self.height

    def require_network(self):
        return {"network": "testnet6", "simulation": True}

    def block_hash(self, height):
        return digest(f"block:{height}:{self.variants.get(height, 0)}")

    def header(self, block_hash):
        height = next(h for h in self.times if self.block_hash(h) == block_hash)
        return {"height": height, "time": self.times[height], "hash": block_hash, "confirmations": self.height - height + 1}

    def advance(self, blocks=1):
        for _ in range(blocks):
            self.clock.value += 60
            self.height += 1
            self.times[self.height] = self.clock()


class FakeSources:
    def __init__(self, clock):
        self.clock, self.rate, self.fail = clock, "1", False

    def price(self):
        if self.fail:
            raise ServiceError(503, "fixture_unavailable", "Test source unavailable")
        return {"piv_usd": self.rate, "updated_at": self.clock(), "source": "local-test-fixture"}

    def service(self, name, params):
        if self.fail:
            raise ServiceError(503, "fixture_unavailable", "Test source unavailable")
        if name == "analytics" and params:
            raise ServiceError(400, "invalid_parameters", "Analytics accepts no parameters")
        return {"schema_version": "1.0", "generated_at": self.clock(), "fixture": True, "name": name, "parameters": params}


class Harness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v511-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.chain = FakeChain(self.clock)
        self.config = Config(data_dir=self.root / "data", address=simulated_address(), wallet_path=self.root / "wallet.json",
                             wallet_fingerprint=digest("simulated-extfvk"), rpc_url="http://127.0.0.1:1",
                             receiver="snapshot", rpc_mode="gateway", demo=True)
        self.config.validate()
        self.key = self.config.signing_key()
        self.db = Database(self.config.data_dir / "services.sqlite3")
        self.notes = []
        self.save_wallet()
        self.watcher = PaymentWatcher(self.config, self.db, self.chain, clock=self.clock)
        self.watcher.tick()
        self.sources = FakeSources(self.clock)
        self.app = Application(self.config, self.db, self.watcher, self.sources, self.key, self.clock)
        self.token = self.app.create_session()["session_token"]

    def save_wallet(self):
        # Mirrors WalletData/SerializedNote; it intentionally contains no real keys or chain receipts.
        wallet = {"version": 1, "extfvk": "simulated-extfvk", "last_block": self.chain.height,
                  "seed": "ENCRYPTED-TEST-PLACEHOLDER", "mnemonic": "ENCRYPTED-TEST-PLACEHOLDER", "unspent_notes": self.notes}
        self.config.wallet_path.write_text(json.dumps(wallet))
        os.utime(self.config.wallet_path, (self.clock(), self.clock()))

    def tick(self):
        self.save_wallet()
        self.watcher.tick()

    def advance(self, blocks=1):
        self.chain.advance(blocks)
        self.tick()

    def invoice(self, service="analytics", product="single", key=None, use_credit=False):
        quote = self.app.quote(self.token, service, product)
        return self.app.create_invoice(self.token, quote["quote_id"], key or quote["quote_id"], use_credit)

    def pay(self, invoice, amount=None, memo=None, nullifier=None):
        self.chain.advance()
        note = {"note": {"value": invoice["amount_to_send_sat"] if amount is None else amount, "recipient": [0] * 43},
                "height": self.chain.height, "memo": memo or invoice["memo"], "nullifier": nullifier or digest(str(len(self.notes))), "witness": ""}
        self.notes.append(note)
        self.tick()
        return self.app.check_invoice(self.token, invoice["invoice_id"])

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(ServiceError) as context:
            function(*args, **kwargs)
        self.assertEqual(context.exception.code, code)
        return context.exception
