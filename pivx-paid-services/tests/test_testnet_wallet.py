from contextlib import closing
from dataclasses import replace
from io import StringIO
import json
from unittest.mock import Mock
from unittest.mock import patch

from paid_services.crypto import ALPHABET, polymod
from paid_services.mcp import serve
from paid_services.network import SAPLING_HRP
from paid_services.testnet_wallet import PayerRPC, TestnetWallet, WalletConfig, TOOLS
from .support import Harness


def sender_address():
    hrp = [ord(c) >> 5 for c in SAPLING_HRP] + [0] + [ord(c) & 31 for c in SAPLING_HRP]
    data = [1] * 68 + [0]
    checksum = polymod(hrp + data + [0] * 6) ^ 1
    return SAPLING_HRP + "1" + "".join(ALPHABET[v] for v in data + [(checksum >> (5 * (5 - i))) & 31 for i in range(6)])


class TestnetWalletTests(Harness):
    def setUp(self):
        super().setUp()
        self.wallet_config = WalletConfig(self.root / "payer", sender_address(), [self.config.address], "http://127.0.0.1:51475")
        self.rpc = Mock()
        self.rpc.call.return_value = "b" * 64
        self.wallet = TestnetWallet(self.wallet_config, self.rpc, self.clock)
        self.payment = {**self.invoice(), "simulation": False}

    def test_send_uses_shield_source_exact_amount_and_plain_memo(self):
        result = self.wallet.pay(self.payment)
        self.assertFalse(result["replayed"])
        self.rpc.require_network.assert_called_once()
        self.rpc.call.assert_called_once_with("shieldsendmany", [self.wallet_config.address,
            [{"address": self.payment["address"], "amount": self.payment["amount_to_send_piv"], "memo": self.payment["memo"]}], 1])

    def test_restart_retry_returns_original_without_another_send(self):
        original = self.wallet.pay(self.payment)
        self.clock.value += 1000
        restarted = TestnetWallet(self.wallet_config, self.rpc, self.clock)
        replay = restarted.pay(self.payment)
        self.assertTrue(replay["replayed"])
        self.assertEqual(original["txid"], replay["txid"])
        self.assertEqual(self.rpc.call.call_count, 1)

    def test_send_timeout_remains_uncertain_after_restart(self):
        self.rpc.call.side_effect = TimeoutError("response lost after a possible broadcast")
        self.error("send_uncertain", self.wallet.pay, self.payment)
        restarted = TestnetWallet(self.wallet_config, self.rpc, self.clock)
        self.error("send_uncertain", restarted.pay, self.payment)
        self.assertEqual(self.rpc.call.call_count, 1)

    def test_changed_amount_cannot_reuse_invoice(self):
        self.wallet.pay(self.payment)
        changed = {**self.payment, "amount_to_send_sat": 1, "amount_to_send_piv": "0.00000001"}
        self.error("invoice_changed", self.wallet.pay, changed)
        self.assertEqual(self.rpc.call.call_count, 1)

    def test_wrong_network_fails_before_intent_or_send(self):
        from paid_services.errors import ServiceError
        self.rpc.require_network.side_effect = ServiceError(503, "wrong_network", "mainnet")
        self.error("wrong_network", self.wallet.pay, self.payment)
        self.rpc.call.assert_not_called()
        with closing(self.wallet.connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sends").fetchone()[0], 0)

    def test_simulation_mainnet_missing_network_and_asset_are_refused(self):
        for change in ({"simulation": True}, {"simulation": None}, {"network": "mainnet"}, {"payment_asset": "PIV"}):
            self.error("wrong_network", self.wallet.pay, {**self.payment, **change})
        self.rpc.call.assert_not_called()

    def test_expired_unready_paid_or_over_limit_invoice_is_refused(self):
        for change, code in (({"expires_at": self.clock()}, "invoice_unavailable"),
                             ({"receiver_ready": False}, "invoice_unavailable"),
                             ({"status": "paid"}, "invoice_unavailable"),
                             ({"amount_to_send_sat": 10**11}, "amount_limit"),
                             ({"amount_to_send_sat": 0}, "amount_limit"),
                             ({"amount_to_send_piv": "999"}, "invalid_invoice"),
                             ({"address": sender_address()}, "invalid_invoice"),
                             ({"memo": "wrong"}, "invalid_invoice")):
            self.error(code, self.wallet.pay, {**self.payment, **change})
        self.rpc.call.assert_not_called()

    def test_concurrent_identical_payments_broadcast_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        self.wallet.pay(self.payment)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.wallet.pay(self.payment), range(8)))
        self.assertTrue(all(r["replayed"] for r in results))
        self.assertEqual(self.rpc.call.call_count, 1)

    def test_payer_journal_bound_to_wallet_and_owner_only(self):
        self.assertEqual(self.wallet.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            TestnetWallet(replace(self.wallet_config, rpc_url="http://127.0.0.1:51476"), self.rpc, self.clock)
        for change in ({"network": "mainnet"}, {"rpc_url": "https://example.org"},
                       {"allowed_receivers": []}, {"max_amount_tpiv": "101"}):
            with self.assertRaises(ValueError):
                replace(self.wallet_config, **change).validate()

    def test_wallet_mcp_discovery_and_send(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "pay_testnet_invoice", "arguments": {"invoice": self.payment}}},
        ]
        output = StringIO()
        serve(self.wallet, StringIO("\n".join(json.dumps(r) for r in requests)), output,
              tools=TOOLS, name="pivx-testnet-core-wallet")
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "pivx-testnet-core-wallet")
        self.assertEqual(responses[1]["result"]["tools"][0]["name"], "pay_testnet_invoice")
        self.assertFalse(responses[2]["result"]["isError"])
        self.assertEqual(self.rpc.call.call_count, 1)

    def test_payer_has_a_longer_proof_timeout_and_separate_credentials(self):
        rpc = PayerRPC(self.wallet_config)
        with patch.dict('os.environ', {'PIVX_RPC_USER': 'receiver', 'PIVX_RPC_PASSWORD': 'not-used',
                                     'PIVX_PAYER_RPC_USER': 'payer', 'PIVX_PAYER_RPC_PASSWORD': 'fixture-only'}), \
             patch('paid_services.sources.fetch', return_value=b'{"result":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","error":null}') as fetch:
            rpc.call('shieldsendmany', [])
        self.assertEqual(fetch.call_args.kwargs['timeout'], 180)
        import base64
        auth = fetch.call_args.args[2]['Authorization'].removeprefix('Basic ')
        self.assertEqual(base64.b64decode(auth), b'payer:fixture-only')
