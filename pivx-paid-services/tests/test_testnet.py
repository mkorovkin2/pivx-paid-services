"""Network rejection and Core receiver contracts, based on checked-out Core RPC."""
from dataclasses import replace
from decimal import Decimal
import json
from unittest.mock import patch

from paid_services.app import Application
from paid_services.crypto import shield_address_bytes, shield_address_valid
from paid_services.network import GENESIS
from paid_services.payments import CoreReceiver, PaymentWatcher
from paid_services.simulation import simulation_address
from paid_services.sources import Chain, Sources
from .support import Harness


class TestnetTests(Harness):
    def core_config(self):
        return replace(self.config, demo=False, receiver="core", rpc_mode="core")

    def row(self, invoice, **changes):
        return {"txid": "a" * 64, "outindex": 0, "amount": Decimal(invoice["amount_to_send_piv"]),
                "memo": invoice["memo"].encode().hex(), "blockheight": self.chain.height,
                "confirmations": 1, "change": False, **changes}

    def receiver(self, rows):
        self.chain.call = lambda method, params: rows
        return CoreReceiver(self.core_config(), self.chain)

    def test_config_disallows_mainnet_and_live_snapshot(self):
        for config in (replace(self.config, network="mainnet"), replace(self.config, demo=False),
                       replace(self.core_config(), rpc_url="https://example.org"),
                       replace(self.core_config(), rpc_mode="gateway")):
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    config.validate()
        self.core_config().validate()

    def test_testnet_address_decodes_and_mainnet_is_rejected(self):
        self.assertTrue(shield_address_valid(simulation_address()))
        self.assertEqual(shield_address_bytes(simulation_address()), bytes(43))
        self.assertFalse(shield_address_valid("ps1u87kylcmn28yclnx2uy0psnvuhs2xn608ukm6n2nshrpg2nzyu3n62ls8j77m9cgp40dx40evej"))
        # The upstream Core RPC help includes this independently encoded test address.
        self.assertTrue(shield_address_valid("ptestsapling1h0w73csah2aq0a32h42kr7tq4htlt5wfn4ejxfnm56f6ehjvek7k4e244g6v8v3pgylmz5ea8jh"))

    def test_mainnet_and_old_testnet_genesis_are_rejected(self):
        chain = Chain(self.core_config())
        with patch.object(chain, "call", return_value={"chain": "main", "initial_block_download": False}):
            self.error("wrong_network", chain.require_network)
        with patch.object(chain, "call", side_effect=[{"chain": "test", "initial_block_download": False}, "a" * 64]):
            self.error("wrong_network", chain.require_network)

    def test_syncing_or_unknown_sync_state_is_rejected(self):
        chain = Chain(self.core_config())
        for info in ({"chain": "test", "initial_block_download": True}, {"chain": "test"},
                     {"chain": "test", "initial_block_downloading": "false"},
                     {"chain": "test", "initial_block_downloading": True, "initial_block_download": False}):
            with patch.object(chain, "call", side_effect=[info, GENESIS]):
                self.error("node_syncing", chain.require_network)
        with patch.object(chain, "call", side_effect=[{"chain": "test", "initial_block_downloading": False}, GENESIS]):
            self.assertEqual(chain.require_network()["genesis"], GENESIS)

    def test_core_rpc_preserves_atomic_amount_precision(self):
        chain = Chain(self.core_config())
        raw = b'{"result":[{"amount":0.00000001}],"error":null}'
        with patch("paid_services.sources.fetch", return_value=raw):
            value = chain.call("listreceivedbyshieldaddress", [self.config.address, 1])
        self.assertEqual(value[0]["amount"], Decimal("0.00000001"))
        self.assertIs(type(value[0]["amount"]), Decimal)

    def test_core_payment_fulfills_and_repeated_output_is_idempotent(self):
        invoice = self.invoice()
        self.chain.advance()
        receiver = self.receiver([self.row(invoice)])
        watcher = PaymentWatcher(self.config, self.db, self.chain, snapshot=receiver, clock=self.clock)
        watcher.tick()
        paid = self.app.check_invoice(self.token, invoice["invoice_id"])
        self.assertEqual(paid["status"], "paid")
        watcher.tick()
        self.assertEqual(len(self.db.rows("SELECT * FROM receipts")), 1)
        self.assertEqual(self.app.call_service(paid["access_token"], "analytics", "core-result")["service_id"], "analytics")

    def test_core_uses_address_and_confirmed_only_receiving_rpc(self):
        invoice = self.invoice()
        receiver = self.receiver([self.row(invoice)])
        with patch.object(self.chain, "call", return_value=[self.row(invoice)]) as call:
            receiver.read()
        call.assert_called_once_with("listreceivedbyshieldaddress", [self.config.address, 1])

    def test_core_padded_hex_memo_and_different_output_indices(self):
        invoice = self.invoice()
        receiver = self.receiver([self.row(invoice, memo=invoice["memo"].encode().hex().ljust(1024, "0")),
                                  self.row(invoice, outindex=1)])
        height, notes = receiver.read()
        self.assertEqual(len(notes), 2)
        self.assertNotEqual(notes[0]["nullifier"], notes[1]["nullifier"])
        self.assertEqual(notes[0]["memo"], invoice["memo"])

    def test_core_wrong_memo_and_change_are_not_payments(self):
        invoice = self.invoice()
        rows = [self.row(invoice, change=True), self.row(invoice, memo=b"wrong memo".hex()),
                self.row(invoice, memo="ff" * 512)]
        self.assertEqual(self.receiver(rows).read()[1], [])

    def test_core_invalid_receipts_do_not_enter_ledger(self):
        invoice = self.invoice()
        for change in ({"amount": Decimal("0.000000001")}, {"amount": 0.1}, {"amount": Decimal("NaN")},
                       {"amount": -1}, {"confirmations": 0}, {"confirmations": True},
                       {"outindex": -1}, {"txid": "claimed-txid"}, {"blockheight": 0},
                       {"memo": "ab cd"}, {"memo": "0"}, {"memo": "00" * 513}):
            with self.subTest(change=change):
                self.error("wallet_invalid", self.receiver([self.row(invoice, **change)]).read)
        self.assertEqual(self.db.rows("SELECT * FROM receipts"), [])

    def test_core_duplicate_output_is_rejected(self):
        invoice = self.invoice()
        row = self.row(invoice)
        self.error("wallet_invalid", self.receiver([row, row]).read)

    def test_core_tip_change_retries_before_fulfillment(self):
        invoice = self.invoice()
        receiver = self.receiver([self.row(invoice)])
        with patch.object(self.chain, "tip", side_effect=[100, 101]):
            self.error("chain_changed", receiver.read)

    def test_snapshot_does_not_accept_another_recipient_in_same_wallet(self):
        invoice = self.invoice()
        self.pay(invoice, amount=1)
        self.notes.append({**self.notes[0], "nullifier": "b" * 64,
                           "note": {"value": invoice["amount_sat"], "recipient": [1] * 43}})
        self.tick()
        self.assertEqual(self.app.check_invoice(self.token, invoice["invoice_id"])["received_sat"], 1)

    def test_reincluded_receipt_quarantines_previously_paid_invoice(self):
        invoice = self.invoice()
        self.pay(invoice)
        self.chain.advance()
        self.notes[0]["height"] = self.chain.height
        self.tick()
        self.error("account_review", self.app.check_invoice, self.token, invoice["invoice_id"])
        self.assertEqual(self.db.rows("SELECT valid FROM receipts")[0]["valid"], 0)

    def test_fixed_testnet_prices_do_not_need_coingecko(self):
        sources = Sources(self.config, self.chain, self.db, self.clock)
        with patch("paid_services.sources.fetch", side_effect=AssertionError("No network expected")):
            self.assertEqual(sources.price()["piv_usd"], "1")
            self.assertIn("no cash value", sources.price()["source"])

    def test_all_payment_responses_identify_testnet(self):
        invoice = self.invoice()
        for response in (self.app.services(), self.app.quote(self.token, "analytics"), invoice, self.app.stats()):
            self.assertEqual(response["network"], "testnet6")
            self.assertEqual(response["payment_asset"], "tPIV")

    def test_database_binding_includes_receiver(self):
        changed = replace(self.config, receiver="core")
        with self.assertRaises(ValueError):
            Application(changed, self.db, self.watcher, self.sources, self.key, self.clock)

    def test_mainnet_analytics_source_is_separate_from_payment_chain(self):
        config = replace(self.config, analytics_rpc_url="https://public.example.org/mainnet")
        sources = Sources(config, self.chain, self.db, self.clock)
        replies = [{"chain": "main", "blocks": 100, "initial_block_download": False},
                   {"connections": 3}, [], 43200, {"total": 2}]
        with patch.object(sources.analytics_chain, "call", side_effect=replies):
            self.assertEqual(sources.analytics()["network"]["chain"], "main")
        self.assertIs(sources.chain, self.chain)
        self.assertEqual(sources.analytics_chain.config.receiver, "analytics")
        with self.assertRaises(ValueError):
            sources.analytics_chain.call("shieldsendmany", [])
