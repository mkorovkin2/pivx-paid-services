from concurrent.futures import ThreadPoolExecutor
import json
import os

from paid_services.app import Application
from paid_services.crypto import digest, shield_address_valid, verify_token
from paid_services.db import Database
from paid_services.payments import PaymentWatcher
from .support import Harness, simulated_address


class PurchaseTests(Harness):
    def test_quote_uses_integer_round_up(self):
        self.sources.rate = "0.3"
        quote = self.app.quote(self.token, "analytics")
        self.assertEqual(quote["amount_sat"], 16666667)
        self.assertEqual(quote["amount_piv"], "0.16666667")

    def test_quote_expiry(self):
        quote = self.app.quote(self.token, "analytics")
        self.clock.value = quote["expires_at"]
        self.tick()
        self.error("quote_expired", self.app.create_invoice, self.token, quote["quote_id"], "expired")

    def test_full_analytics_purchase_and_retry(self):
        invoice = self.invoice()
        self.assertNotIn("access_token", invoice)
        paid = self.pay(invoice)
        self.assertEqual(paid["status"], "paid")
        result = self.app.call_service(paid["access_token"], "analytics", "call-1")
        self.assertEqual(result["data"]["name"], "analytics")
        self.assertEqual(result, self.app.call_service(paid["access_token"], "analytics", "call-1"))
        self.error("quota_exhausted", self.app.call_service, paid["access_token"], "analytics", "call-2")
        self.assertEqual(self.app.stats()["totals"]["paid_calls"], 1)

    def test_week_pass_requires_three_confirmations(self):
        invoice = self.invoice("digest", "week")
        checking = self.pay(invoice)
        self.assertEqual(checking["status"], "confirming")
        self.assertNotIn("access_token", checking)
        self.advance(2)
        paid = self.app.check_invoice(self.token, invoice["invoice_id"])
        self.assertEqual(paid["status"], "paid")
        for key in ["a", "b", "c"]:
            self.app.call_service(paid["access_token"], "digest", key)
        self.clock.value = paid["access_until"]
        self.tick()
        self.assertNotIn("access_token", self.app.check_invoice(self.token, invoice["invoice_id"]))
        self.error("invalid_token", self.app.call_service, paid["access_token"], "digest", "expired")

    def test_confirmation_wait_does_not_expire_timely_payment(self):
        invoice = self.invoice("digest", "week")
        self.clock.value = invoice["expires_at"] - 61
        self.pay(invoice)
        self.advance(2)
        self.assertGreater(self.clock(), invoice["expires_at"])
        self.assertEqual(self.app.check_invoice(self.token, invoice["invoice_id"])["status"], "paid")

    def test_wrong_memo_does_not_fulfill(self):
        invoice = self.invoice()
        checked = self.pay(invoice, memo="pivxai:inv:AAAAAAAAAAAAAAAA")
        self.assertEqual(checked["status"], "pending")
        self.assertEqual(self.app.stats()["totals"]["payment_count"], 0)

    def test_memo_is_exact(self):
        invoice = self.invoice()
        for memo in [invoice["memo"] + " ", invoice["memo"].lower(), "hello", "inv:" + invoice["invoice_id"]]:
            self.assertEqual(self.pay(invoice, memo=memo)["received_sat"], 0)

    def test_partial_topup(self):
        invoice = self.invoice()
        partial = self.pay(invoice, amount=invoice["amount_sat"] - 1)
        self.assertEqual(partial["status"], "partially_paid")
        self.assertEqual(partial["amount_to_send_sat"], 1)
        self.assertEqual(self.pay(invoice, amount=1)["status"], "paid")

    def test_partial_expiry_becomes_credit(self):
        invoice = self.invoice()
        self.pay(invoice, amount=100)
        self.clock.value = invoice["expires_at"] + 1
        self.advance(3)
        checked = self.app.check_invoice(self.token, invoice["invoice_id"])
        self.assertEqual(checked["status"], "expired")
        self.assertEqual(self.app.account(self.token)["available_credit_sat"], 100)

    def test_late_payment_is_credit_only(self):
        invoice = self.invoice()
        self.clock.value = invoice["expires_at"] + 1
        checked = self.pay(invoice)
        self.assertEqual(checked["status"], "expired")
        self.advance(2)
        self.assertNotIn("access_token", self.app.check_invoice(self.token, invoice["invoice_id"]))
        self.assertEqual(self.app.account(self.token)["available_credit_sat"], invoice["amount_sat"])

    def test_overpayment_credit_and_credit_purchase(self):
        invoice = self.invoice()
        self.pay(invoice, amount=invoice["amount_sat"] * 2)
        self.assertEqual(self.app.account(self.token)["available_credit_sat"], 0)
        self.advance(2)
        self.assertEqual(self.app.account(self.token)["available_credit_sat"], invoice["amount_sat"])
        next_invoice = self.invoice(use_credit=True)
        self.assertEqual(next_invoice["status"], "paid")
        self.assertEqual(next_invoice["amount_to_send_sat"], 0)
        self.assertEqual(self.app.account(self.token)["available_credit_sat"], 0)

    def test_duplicate_ticks_count_receipt_once(self):
        invoice = self.invoice()
        self.pay(invoice)
        for _ in range(5):
            self.tick()
        self.assertEqual(len(self.db.rows("SELECT * FROM receipts")), 1)
        self.assertEqual(self.app.stats()["totals"]["payment_count"], 1)

    def test_receipts_survive_wallet_sweep_and_restart(self):
        paid = self.pay(self.invoice())
        result = self.app.call_service(paid["access_token"], "analytics", "once")
        self.notes.clear()
        self.tick()
        db = Database(self.config.data_dir / "services.sqlite3")
        watcher = PaymentWatcher(self.config, db, self.chain, clock=self.clock)
        watcher.tick()
        app = Application(self.config, db, watcher, self.sources, self.config.signing_key(), self.clock)
        self.assertEqual(app.call_service(paid["access_token"], "analytics", "once"), result)
        self.error("quota_exhausted", app.call_service, paid["access_token"], "analytics", "twice")

    def test_cross_session_isolation(self):
        invoice = self.invoice()
        other = self.app.create_session()["session_token"]
        self.error("invoice_not_found", self.app.check_invoice, other, invoice["invoice_id"])

    def test_cross_service_and_tampered_tokens(self):
        paid = self.pay(self.invoice())
        self.error("access_denied", self.app.call_service, paid["access_token"], "digest", "wrong")
        self.error("invalid_token", self.app.call_service, paid["access_token"][:-1] + "!", "analytics", "bad")

    def test_token_refresh_preserves_quota(self):
        paid = self.pay(self.invoice())
        self.app.call_service(paid["access_token"], "analytics", "once")
        self.clock.value += 901
        self.tick()
        refreshed = self.app.check_invoice(self.token, paid["invoice_id"])
        self.app.call_service(refreshed["access_token"], "analytics", "once")
        self.error("quota_exhausted", self.app.call_service, refreshed["access_token"], "analytics", "twice")

    def test_failing_service_does_not_consume_quota(self):
        paid = self.pay(self.invoice())
        self.sources.fail = True
        self.error("fixture_unavailable", self.app.call_service, paid["access_token"], "analytics", "retry")
        self.sources.fail = False
        self.app.call_service(paid["access_token"], "analytics", "retry")

    def test_concurrent_retries_charge_once(self):
        paid = self.pay(self.invoice())
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.app.call_service(paid["access_token"], "analytics", "same"), range(16)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.db.rows("SELECT * FROM calls")), 1)

    def test_concurrent_distinct_calls_cannot_overspend(self):
        paid = self.pay(self.invoice())
        def consume(key):
            try:
                self.app.call_service(paid["access_token"], "analytics", str(key))
                return True
            except Exception:
                return False
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(sum(executor.map(consume, range(16))), 1)

    def test_invoice_idempotency_and_conflict(self):
        quote = self.app.quote(self.token, "analytics")
        first = self.app.create_invoice(self.token, quote["quote_id"], "same")
        second = self.app.create_invoice(self.token, quote["quote_id"], "same")
        self.assertEqual(first["invoice_id"], second["invoice_id"])
        other = self.app.quote(self.token, "analytics")
        self.error("idempotency_conflict", self.app.create_invoice, self.token, other["quote_id"], "same")

    def test_stats_do_not_expose_payment_or_buyer_identifiers(self):
        paid = self.pay(self.invoice())
        self.app.call_service(paid["access_token"], "analytics", "once")
        stats = json.dumps(self.app.stats())
        for private in [self.config.address, self.token, paid["invoice_id"], paid["memo"], self.notes[0]["nullifier"]]:
            self.assertNotIn(private, stats)
        self.assertEqual(self.app.stats()["totals"]["unique_integrations"], 1)

    def test_reorg_revokes_entitlement_and_freezes_credit_owner(self):
        paid = self.pay(self.invoice())
        self.chain.variants[self.notes[0]["height"]] = 1
        self.tick()
        self.error("access_denied", self.app.call_service, paid["access_token"], "analytics", "orphan")
        self.error("account_review", self.app.account, self.token)

    def test_stale_receiver_pauses_purchase_and_consumption(self):
        paid = self.pay(self.invoice())
        self.clock.value += self.config.max_sync_age + 1
        self.error("receiver_unavailable", self.app.quote, self.token, "analytics")
        self.error("receiver_unavailable", self.app.call_service, paid["access_token"], "analytics", "stale")

    def test_receiver_rejects_wrong_wallet(self):
        self.config.wallet_fingerprint = "0" * 64
        self.error("wrong_wallet", self.watcher.tick)
        self.assertFalse(self.watcher.fresh())

    def test_receiver_rejects_duplicate_note_in_snapshot(self):
        self.pay(self.invoice())
        self.notes.append(dict(self.notes[0]))
        self.save_wallet()
        self.error("wallet_invalid", self.watcher.tick)

    def test_receiver_rejects_float_amount(self):
        invoice = self.invoice()
        self.chain.advance()
        self.notes.append({"note": {"value": 0.1}, "memo": invoice["memo"], "nullifier": "0" * 64, "height": self.chain.height})
        self.save_wallet()
        self.error("wallet_invalid", self.watcher.tick)

    def test_receiver_rejects_receipt_mutation(self):
        self.pay(self.invoice())
        self.notes[0]["note"]["value"] += 1
        self.save_wallet()
        self.error("receipt_changed", self.watcher.tick)

    def test_refund_reservation_and_idempotency(self):
        invoice = self.invoice()
        self.pay(invoice, amount=invoice["amount_sat"] * 2)
        self.advance(2)
        refund = self.app.request_refund(self.token, invoice["amount_sat"], self.config.address, "refund")
        self.assertEqual(refund, self.app.request_refund(self.token, invoice["amount_sat"], self.config.address, "refund"))
        self.assertEqual(self.app.account(self.token)["available_credit_sat"], 0)
        self.error("insufficient_credit", self.app.request_refund, self.token, 1, self.config.address, "extra")

    def test_address_checksum(self):
        self.assertTrue(shield_address_valid(simulated_address()))
        self.assertFalse(shield_address_valid(simulated_address()[:-1] + "q"))
        self.assertFalse(shield_address_valid("ps1unverified"))

    def test_refund_cancellation_releases_reserved_credit_once(self):
        invoice = self.invoice()
        self.pay(invoice, amount=invoice['amount_sat'] * 2)
        self.advance(2)
        refund = self.app.request_refund(self.token, invoice['amount_sat'], self.config.address, 'refund')
        self.app.cancel_refund(self.token, refund['refund_id'])
        self.app.cancel_refund(self.token, refund['refund_id'])
        self.assertEqual(self.app.account(self.token)['available_credit_sat'], invoice['amount_sat'])

    def test_expired_invoice_releases_credit_reservation(self):
        original = self.invoice()
        self.pay(original, amount=original['amount_sat'] * 2)
        self.advance(2)
        pending = self.invoice('digest', use_credit=True)
        self.assertEqual(pending['credit_used_sat'], original['amount_sat'])
        self.assertEqual(self.app.account(self.token)['available_credit_sat'], 0)
        self.clock.value = pending['expires_at'] + 1
        self.advance()
        self.assertEqual(self.app.check_invoice(self.token, pending['invoice_id'])['status'], 'expired')
        self.assertEqual(self.app.account(self.token)['available_credit_sat'], original['amount_sat'])

    def test_credit_funded_access_is_revoked_if_original_payment_reorgs(self):
        original = self.invoice()
        self.pay(original, amount=original['amount_sat'] * 2)
        self.advance(2)
        second = self.invoice(use_credit=True)
        self.chain.variants[self.chain.tip()] = 1
        self.chain.variants[self.notes[0]['height']] = 1
        self.tick()
        self.error('access_denied', self.app.call_service, second['access_token'], 'analytics', 'reorg')

    def test_cannot_reuse_ledger_with_another_signing_key(self):
        with self.assertRaisesRegex(ValueError, 'Database belongs'):
            Application(self.config, self.db, self.watcher, self.sources, b'x' * 32, self.clock)

    def test_weekly_totals_include_calls_from_current_second(self):
        paid = self.pay(self.invoice())
        self.app.call_service(paid['access_token'], 'analytics', 'now')
        self.assertEqual(self.app.stats()['weekly'][0]['paid_calls'], 1)

    def test_unused_paid_invoice_can_be_cancelled_for_confirmed_credit(self):
        paid = self.pay(self.invoice())
        cancelled = self.app.cancel_invoice(self.token, paid['invoice_id'])
        self.assertEqual(cancelled['status'], 'expired')
        self.assertEqual(self.app.account(self.token)['available_credit_sat'], 0)
        self.advance(2)
        self.assertEqual(self.app.account(self.token)['available_credit_sat'], paid['amount_sat'])
        self.error('access_denied', self.app.call_service, paid['access_token'], 'analytics', 'cancelled')

    def test_delivered_invoice_cannot_be_cancelled(self):
        paid = self.pay(self.invoice())
        self.app.call_service(paid['access_token'], 'analytics', 'delivered')
        self.error('already_delivered', self.app.cancel_invoice, self.token, paid['invoice_id'])
