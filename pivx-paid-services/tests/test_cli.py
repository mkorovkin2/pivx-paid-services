from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import patch

from paid_services.__main__ import main
from paid_services.errors import ServiceError
from .support import Harness


class CLITests(Harness):
    def invoke(self, arguments, client=None):
        output, errors = StringIO(), StringIO()
        with patch.object(sys, 'argv', ['paid_services', *arguments]), redirect_stdout(output), redirect_stderr(errors), \
             patch('paid_services.__main__.Client', return_value=client), \
             patch('paid_services.__main__.Config.load', return_value=self.config), \
             patch('paid_services.__main__.build', return_value=self.app):
            main()
        return json.loads(output.getvalue())

    def test_cli_recovers_after_invoice_response_is_lost(self):
        harness = self
        class LocalClient:
            lose_once = True
            quote_count = 0
            def invoke(self, name, arguments):
                if name == 'get_quote':
                    self.quote_count += 1
                    return harness.app.quote(harness.token, arguments['service_id'], arguments['product'])
                if name == 'create_invoice':
                    result = harness.app.create_invoice(harness.token, arguments['quote_id'], arguments['request_id'], arguments['use_credit'])
                    if self.lose_once:
                        self.lose_once = False
                        raise ServiceError(503, 'lost_response', 'Simulated interrupted response')
                    return result
                return harness.app.check_invoice(harness.token, arguments['invoice_id'])
        client = LocalClient()
        args = ['invoice', '--service', 'analytics', '--request-id', 'stable', '--session-file', str(self.root / 'client.json')]
        with self.assertRaises(SystemExit):
            self.invoke(args, client)
        result = self.invoke(args, client)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(client.quote_count, 1)
        self.assertEqual(len(self.db.rows('SELECT * FROM invoices')), 1)
        self.assertEqual(result['invoice_id'], self.invoke(args, client)['invoice_id'])

    def test_headless_demo_subprocess_completes_both_services_without_network(self):
        result = subprocess.run([sys.executable, '-m', 'paid_services', 'demo', '--headless', '--data-dir', str(self.root / 'demo')],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(result.stdout)
        self.assertEqual(proof['real_transactions'], 0)
        self.assertTrue(proof['simulation'])
        self.assertEqual([item['state'] for item in proof['purchases']], ['paid', 'paid'])
        self.assertEqual(proof['stats']['totals']['paid_calls'], 2)

    def test_operator_backup_has_integrity_and_owner_only_permissions(self):
        self.invoice()
        path = self.root / 'backup.sqlite3'
        self.invoke(['operator', '--config', 'unused.toml', 'backup', '--output', str(path)])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM invoices').fetchone()[0], 1)
        finally:
            connection.close()

    def test_operator_refund_record_is_idempotent(self):
        invoice = self.invoice()
        self.pay(invoice, amount=invoice['amount_sat'] * 2)
        self.advance(2)
        refund = self.app.request_refund(self.token, invoice['amount_sat'], self.config.address, 'refund')
        args = ['operator', '--config', 'unused.toml', 'record-refund', '--refund-id', refund['refund_id'], '--txid', 'a' * 64, '--fee-sat', '1']
        self.assertEqual(self.invoke(args)['status'], 'broadcast')
        self.assertEqual(self.invoke(args)['status'], 'broadcast')
        self.assertEqual(self.app.account(self.token)['available_credit_sat'], 0)

    def test_operator_exports_only_aggregate_statistics(self):
        paid = self.pay(self.invoice())
        self.app.call_service(paid['access_token'], 'analytics', 'once')
        path = self.root / 'stats.json'
        self.invoke(['operator', '--config', 'unused.toml', 'export-stats', '--output', str(path)])
        raw = path.read_text()
        self.assertNotIn(paid['invoice_id'], raw)
        self.assertNotIn(self.token, raw)
        self.assertEqual(json.loads(raw)['totals']['paid_calls'], 1)
