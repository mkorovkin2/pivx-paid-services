import json

from paid_services.artifacts import export_site, latency_report
from .support import Harness


class ArtifactTests(Harness):
    def test_static_export_has_relative_assets_and_only_public_aggregates(self):
        invoice = self.invoice()
        paid = self.pay(invoice)
        self.app.call_service(paid["access_token"], "analytics", "export-test")
        output = self.root / "pages"
        result = export_site(self.app, output)
        self.assertFalse(result["published"])
        page = (output / "index.html").read_text()
        self.assertIn('data-static="true"', page)
        self.assertIn('src="./app.js"', page)
        self.assertIn('href="./style.css"', page)
        self.assertNotIn('href="/quickstart"', page)
        self.assertNotIn('href="/stats', page)
        stats = (output / "stats.json").read_text()
        for private in (invoice["invoice_id"], invoice["memo"], self.token, self.config.address):
            self.assertNotIn(private, stats)
        self.assertTrue(json.loads(stats)["simulation"])
        self.assertEqual(json.loads(stats)["totals"]["paid_calls"], 1)
        self.assertEqual({p.name for p in output.iterdir()}, {"index.html", "app.js", "style.css", "stats.json", ".nojekyll"})
        self.error("export_exists", export_site, self.app, output)

    def test_empty_latency_report_does_not_claim_live_target(self):
        report = latency_report(self.app)
        self.assertFalse(report["live_target_met"])
        self.assertEqual(report["detection_to_access"]["samples"], 0)
        self.assertIsNone(report["detection_to_access"]["p95_seconds"])

    def test_sixty_simulated_payments_do_not_claim_live_latency_target(self):
        for index in range(60):
            invoice = self.invoice(key=f"benchmark-{index}")
            paid = self.pay(invoice)
            self.app.call_service(paid["access_token"], "analytics", f"result-{index}")
        report = latency_report(self.app)
        self.assertEqual(report["detection_to_access"]["samples"], 60)
        self.assertEqual(report["detection_to_result"]["samples"], 60)
        self.assertTrue(report["simulation"])
        self.assertFalse(report["live_target_met"])
