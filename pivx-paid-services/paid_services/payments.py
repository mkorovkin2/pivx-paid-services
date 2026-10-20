"""Memo reconciliation over a dedicated Merchant Kit receiving wallet.

The upstream wallet sync decrypts Sapling notes. This adapter reads its atomically
persisted SerializedNote records, never unlocks keys, and archives receipts before
the operator can sweep them. The file is a privileged local integration boundary.
"""
import hashlib
import json
from decimal import Decimal
from pathlib import Path
import re
import threading
import time

from .errors import ServiceError, require
from .ledger import reconcile_invoices
from .crypto import shield_address_bytes

MEMO = re.compile(r"pivxai:inv:([A-Z2-7]{16})\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")


class WalletSnapshot:
    def __init__(self, config, clock=time.time):
        self.config, self.clock = config, clock

    def read(self):
        try:
            path = Path(self.config.wallet_path)
            require(0 <= self.clock() - path.stat().st_mtime <= self.config.max_sync_age,
                    503, "wallet_stale", "Receiver wallet snapshot is stale")
            with path.open("rb") as handle:
                raw = handle.read(33_554_433)
            require(len(raw) <= 33_554_432, 503, "wallet_invalid", "Receiver wallet snapshot is too large")
            wallet = json.loads(raw)
            fingerprint = hashlib.sha256(wallet["extfvk"].encode()).hexdigest()
            require(fingerprint == self.config.wallet_fingerprint,
                    503, "wrong_wallet", "Receiver wallet fingerprint does not match configuration")
            height = wallet["last_block"]
            require(type(height) is int and height > 0 and isinstance(wallet["unspent_notes"], list),
                    503, "wallet_invalid", "Invalid receiver snapshot")
            notes, seen = [], set()
            for note in wallet["unspent_notes"]:
                memo = note.get("memo") or ""
                # Non-invoice notes (including change) never enter the accounting ledger.
                if not isinstance(memo, str) or not MEMO.fullmatch(memo):
                    continue
                recipient = note["note"].get("recipient")
                require(isinstance(recipient, list) and len(recipient) == 43
                        and all(type(v) is int and 0 <= v <= 255 for v in recipient),
                        503, "wallet_invalid", "Missing or invalid note recipient")
                if bytes(recipient) != shield_address_bytes(self.config.address):
                    continue
                amount, block, nullifier = note["note"]["value"], note["height"], note["nullifier"]
                require(type(amount) is int and 0 < amount <= 2**63 - 1 and type(block) is int and 0 < block <= height
                        and isinstance(nullifier, str) and HEX.fullmatch(nullifier) and nullifier not in seen,
                        503, "wallet_invalid", "Invalid or duplicate receiver note")
                seen.add(nullifier)
                notes.append({"memo": memo, "amount": amount, "height": block, "nullifier": nullifier})
            return height, notes
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise ServiceError(503, "wallet_unavailable", "Cannot read a valid receiver wallet snapshot") from None


class CoreReceiver:
    """Receiver-side decrypted notes from local Core, including spent receipts.

    Core's listreceivedbyshieldaddress waits for wallet synchronization and
    checks ownership of the requested address. No public txid claim is accepted.
    The ledger's historical nullifier column holds a hashed output identity for
    this adapter, which also works with a viewing-key-only Core wallet.
    """
    def __init__(self, config, chain):
        self.config, self.chain = config, chain

    def read(self):
        height = self.chain.tip()
        rows = self.chain.call("listreceivedbyshieldaddress", [self.config.address, 1])
        require(isinstance(rows, list) and len(rows) <= 100000,
                503, "wallet_invalid", "Invalid Core received-note list")
        notes, seen = [], set()
        for row in rows:
            require(isinstance(row, dict), 503, "wallet_invalid", "Invalid Core received note")
            if row.get("change") is True:
                continue
            memo_hex = row.get("memo")
            require(isinstance(memo_hex, str) and len(memo_hex) <= 1024 and len(memo_hex) % 2 == 0
                    and re.fullmatch(r"[0-9a-fA-F]*", memo_hex) is not None,
                    503, "wallet_invalid", "Invalid Core memo encoding")
            try:
                memo = bytes.fromhex(memo_hex).rstrip(b"\x00").decode("utf-8")
            except UnicodeError:
                continue  # Binary and non-invoice memos have no service meaning.
            if not MEMO.fullmatch(memo):
                continue
            txid, index = row.get("txid"), row.get("outindex")
            block, confs, value = row.get("blockheight"), row.get("confirmations"), row.get("amount")
            require(isinstance(txid, str) and HEX.fullmatch(txid) and type(index) is int and 0 <= index < 2**32
                    and type(block) is int and 0 < block <= height and type(confs) is int and confs == height - block + 1,
                    503, "wallet_invalid", "Invalid Core receipt identity or confirmation depth")
            require(type(value) in (Decimal, int), 503, "wallet_invalid", "Core amount must be an exact JSON number")
            amount = Decimal(value) * 100_000_000
            require(amount.is_finite() and 0 < amount <= 2**63 - 1 and amount == amount.to_integral_value(),
                    503, "wallet_invalid", "Core amount must be positive with at most eight decimal places")
            identity = hashlib.sha256(f"core-output:{txid}:{index}".encode()).hexdigest()
            require(identity not in seen, 503, "wallet_invalid", "Duplicate Core receipt")
            seen.add(identity)
            notes.append({"memo": memo, "amount": int(amount), "height": block, "nullifier": identity})
        require(self.chain.tip() == height, 503, "chain_changed", "Chain advanced during receiver read; retrying next tick")
        return height, notes


class PaymentWatcher:
    def __init__(self, config, db, chain, snapshot=None, clock=time.time):
        self.config, self.db, self.chain, self.clock = config, db, chain, clock
        self.snapshot = snapshot or WalletSnapshot(config, clock)
        self.lock = threading.Lock()

    def tick(self):
        with self.lock:
            try:
                self._tick()
            except Exception as error:
                code = error.code if isinstance(error, ServiceError) else "watcher_error"
                with self.db.transaction() as connection:
                    connection.execute("INSERT OR REPLACE INTO state VALUES ('watcher_error',?)", [code])
                raise

    def _tick(self):
        now = int(self.clock())
        self.chain.require_network()
        tip = self.chain.tip()
        tip_hash = self.chain.block_hash(tip)
        height, notes = self.snapshot.read()
        require(0 <= tip - height <= self.config.max_sync_lag, 503, "wallet_behind", "Receiver wallet is behind the chain")
        existing = self.db.rows("SELECT * FROM receipts")
        known = {row["nullifier"]: row for row in existing}
        previous = {row["key"]: row["value"] for row in self.db.rows("SELECT * FROM state WHERE key IN ('watcher_tip','watcher_tip_hash')")}
        previous_tip = int(previous.get("watcher_tip", "0"))
        reorg = previous_tip and (tip < previous_tip or self.chain.block_hash(previous_tip) != previous.get("watcher_tip_hash"))
        # An output re-included at a new height is a reorg, not a second payment.
        # Invalidate it and quarantine its owner before immutable-field checks.
        changed = {note["nullifier"] for note in notes if note["nullifier"] in known
                   and known[note["nullifier"]]["height"] != note["height"]}
        # Recheck the last processed chain anchor every tick. Its ancestry
        # commits to all earlier payment blocks; rescan old receipts only on
        # a changed anchor, keeping normal polling bounded after many sales.
        heights = {height, *(note["height"] for note in notes if note["nullifier"] not in known),
                   *(row["height"] for row in existing if row["valid"] and reorg)}
        hashes, headers = {}, {}
        for block in heights:
            if block > tip:
                hashes[block] = None
                continue
            block_hash = self.chain.block_hash(block)
            header = self.chain.header(block_hash)
            require(header["height"] == block and header["time"] <= now + 120,
                    503, "rpc_invalid", "Inconsistent block header")
            hashes[block], headers[block] = block_hash, header
        require(self.chain.block_hash(tip) == tip_hash, 503, "chain_changed", "Chain changed while reconciling; retrying next tick")
        with self.db.transaction() as connection:
            for row in existing:
                if row["valid"] and (row["nullifier"] in changed or row["height"] in hashes and hashes[row["height"]] != row["block_hash"]):
                    connection.execute("UPDATE receipts SET valid=0,confs=0 WHERE nullifier=?", [row["nullifier"]])
                elif row["valid"]:
                    connection.execute("UPDATE receipts SET confs=? WHERE nullifier=?", [tip - row["height"] + 1, row["nullifier"]])
            for note in notes:
                previous = connection.execute("SELECT * FROM receipts WHERE nullifier=?", [note["nullifier"]]).fetchone()
                if previous:
                    if not previous["valid"]:
                        continue
                    require(all(previous[key] == note[key] for key in ("memo", "amount", "height")),
                            503, "receipt_changed", "Immutable receiver receipt changed")
                    continue
                invoice_id = MEMO.fullmatch(note["memo"])[1]
                invoice = connection.execute("SELECT id FROM invoices WHERE id=?", [invoice_id]).fetchone()
                # Unknown invoice memos are never later reassigned to a new buyer.
                connection.execute(
                    "INSERT INTO receipts(nullifier,invoice_id,memo,amount,height,block_hash,block_time,first_seen,confs) VALUES (?,?,?,?,?,?,?,?,?)",
                    [note["nullifier"], invoice_id if invoice else None, note["memo"], note["amount"], note["height"], hashes[note["height"]],
                     headers[note["height"]]["time"], now, tip - note["height"] + 1],
                )
            reconcile_invoices(connection, now, headers[height]["time"], self.config)
            for key, value in {"watcher_success": str(now), "watcher_tip": str(tip), "watcher_tip_hash": tip_hash,
                               "watcher_settled_through": str(headers[height]["time"]), "watcher_error": ""}.items():
                connection.execute("INSERT OR REPLACE INTO state VALUES (?,?)", [key, value])

    def fresh(self):
        rows = {row["key"]: row["value"] for row in self.db.rows("SELECT * FROM state WHERE key LIKE 'watcher_%'")}
        return not rows.get("watcher_error", "") and 0 <= self.clock() - int(rows.get("watcher_success", "0")) <= self.config.max_sync_age

    def require_fresh(self):
        require(self.fresh(), 503, "receiver_unavailable", "Receiver sync is unavailable; purchases are paused")

    def run(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                # Persisted error codes are available via readiness and the operator CLI.
                # Never log raw wallet data, RPC credentials, memos or receipt identifiers.
                pass
            stop.wait(self.config.poll_seconds)
