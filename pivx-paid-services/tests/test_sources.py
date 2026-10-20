from decimal import Decimal
import json
from unittest.mock import patch

from paid_services.sources import Chain, Sources, parse_feed
from .support import Harness


class SourceTests(Harness):
    def setUp(self):
        super().setUp()
        self.config.price_mode = "coingecko_reference"
        self.real_sources = Sources(self.config, self.chain, self.db, self.clock)

    def test_fresh_price_and_restart_cache(self):
        payload = json.dumps({"pivx": {"usd": 0.125, "last_updated_at": self.clock()}}).encode()
        with patch("paid_services.sources.fetch", return_value=payload) as mocked:
            self.assertEqual(self.real_sources.price()["piv_usd"], "0.125")
            restarted = Sources(self.config, self.chain, self.db, self.clock)
            self.assertEqual(restarted.price()["piv_usd"], "0.125")
            self.assertEqual(mocked.call_count, 1)

    def test_stale_and_invalid_prices_rejected(self):
        for rate, timestamp in [(1, self.clock() - 301), (0, self.clock()), (-1, self.clock()), (1, self.clock() + 61), ("NaN", self.clock()), ("invalid", self.clock())]:
            with self.subTest(rate=rate, timestamp=timestamp):
                raw = json.dumps({"pivx": {"usd": rate, "last_updated_at": timestamp}}).encode()
                with patch("paid_services.sources.fetch", return_value=raw):
                    self.error("price_unavailable", self.real_sources.price)

    def test_atom_and_rss_digest_excerpts_are_sourced(self):
        now = "2027-01-15T08:00:00Z"
        from datetime import datetime
        timestamp = int(datetime.fromisoformat(now).timestamp())
        atom = f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Release &amp; fixes</title><published>{now}</published><link href="https://example.org/release"/><content>&lt;p&gt;Updated protocol.&lt;/p&gt;</content></entry></feed>'''.encode()
        source = {"name": "Test", "topic": "PIVX", "url": "https://example.org/feed"}
        items = parse_feed(atom, source, timestamp, 7)
        self.assertEqual(items[0]["summary"], "Updated protocol.")
        self.assertEqual(items[0]["url"], "https://example.org/release")
        rss = b'<rss><channel><item><title>News</title><pubDate>Fri, 15 Jan 2027 08:00:00 GMT</pubDate><link>https://example.org/news</link><description>Facts.</description></item></channel></rss>'
        self.assertEqual(parse_feed(rss, source, timestamp, 7)[0]["title"], "News")

    def test_feed_entities_and_invalid_xml_rejected(self):
        self.error("feed_invalid", parse_feed, b'<!DOCTYPE rss><rss/>', {}, self.clock(), 7)
        self.error("feed_invalid", parse_feed, b'<invalid', {}, self.clock(), 7)

    def test_digest_declares_partial_coverage_and_no_fabrication(self):
        from paid_services.errors import ServiceError
        with patch("paid_services.sources.fetch", side_effect=[b'<rss><channel/></rss>', ServiceError(503, "bad", "bad"), b'<rss><channel/></rss>', b'<rss><channel/></rss>']):
            result = self.real_sources.digest()
        self.assertTrue(result["partial_coverage"])
        self.assertEqual(result["items"], [])
        self.assertEqual(len(result["coverage"]), 4)

    def test_all_digest_sources_down_fails(self):
        from paid_services.errors import ServiceError
        with patch("paid_services.sources.fetch", side_effect=ServiceError(503, "down", "down")):
            self.error("digest_unavailable", self.real_sources.digest)

    def test_core_and_gateway_rpc_formats_and_no_mutation_methods(self):
        chain = Chain(self.config)
        with patch("paid_services.sources.fetch", return_value=b'100') as mocked:
            self.assertEqual(chain.tip(), 100)
            self.assertTrue(mocked.call_args.args[0].endswith('/getblockcount'))
        self.config.rpc_mode = 'core'
        with patch("paid_services.sources.fetch", return_value=b'{"result":100,"error":null}') as mocked:
            self.assertEqual(chain.tip(), 100)
            self.assertEqual(json.loads(mocked.call_args.args[1])["method"], "getblockcount")
        with self.assertRaises(ValueError):
            chain.call("sendrawtransaction", ["fake"])

    def test_rpc_rejects_invalid_tip_and_hash(self):
        chain = Chain(self.config)
        with patch("paid_services.sources.fetch", return_value=b'{"error":"RPC not supported"}'):
            self.error("rpc_invalid", chain.tip)
        with patch("paid_services.sources.fetch", return_value=b'"not-a-hash"'):
            self.error("rpc_invalid", chain.block_hash, 100)

    def test_analytics_preserves_source_schema_and_omits_payee(self):
        replies = [
            {"chain": "test", "blocks": 100, "headers": 100, "difficulty": 2, "initial_block_download": False},
            {"connections": 8}, [{"Name": "Proposal", "MonthlyPayment": 100, "PaymentAddress": "PRIVATE-PAYEE"}], 43200, {"total": 99},
        ]
        self.chain.call = lambda method: replies.pop(0)
        result = self.real_sources.analytics()
        self.assertEqual(result["blocks_until_superblock"], 43100)
        self.assertNotIn("PRIVATE-PAYEE", json.dumps(result))
        self.assertEqual(result["proposals"][0]["Name"], "Proposal")
