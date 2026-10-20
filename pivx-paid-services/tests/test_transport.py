from http.client import HTTPConnection
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

from paid_services.http_server import Handler, Server
from paid_services.mcp import Client, serve
from .support import Harness


class WireResponse(BytesIO):
    def __init__(self, body, status, headers):
        super().__init__(body)
        self.status, self.headers = status, headers


class HTTPTests(Harness):
    def setUp(self):
        super().setUp()
        # The sandbox blocks listen/bind. Accepted socketpairs still exercise
        # the actual HTTP parser, authentication, routing and response framing.
        self.server = Server(("127.0.0.1", 8787), self.app, bind_and_activate=False)
        self.server.server_port = 8787
        self.addCleanup(self.server.server_close)

    def wire(self, method, path, body=None, token=None, headers=None, raw=None):
        left, right = socket.socketpair()
        left.settimeout(5)
        right.settimeout(5)
        thread = threading.Thread(target=Handler, args=(right, ("127.0.0.1", 50000), self.server), daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", 8787, timeout=5)
        connection.sock = left
        values = {"Content-Type": "application/json", **(headers or {})}
        if token:
            values["Authorization"] = "Bearer " + token
        encoded = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        connection.request(method, path, encoded, values)
        response = connection.getresponse()
        status, result, response_headers = response.status, response.read(), response.headers
        connection.close()
        thread.join(5)
        right.close()
        return status, result, response_headers

    def test_actual_http_purchase_and_result(self):
        status, raw, _ = self.wire("POST", "/v1/quotes", {"service_id": "analytics"}, self.token)
        self.assertEqual(status, 200)
        quote = json.loads(raw)
        status, raw, _ = self.wire("POST", "/v1/invoices", {"quote_id": quote["quote_id"], "request_id": "wire"}, self.token)
        self.assertEqual(status, 200)
        invoice = json.loads(raw)
        self.pay(invoice)
        status, raw, _ = self.wire("GET", "/v1/invoices/" + invoice["invoice_id"], token=self.token)
        self.assertEqual(status, 200)
        paid = json.loads(raw)
        status, raw, _ = self.wire("POST", "/v1/services/analytics/call", {"request_id": "result"}, paid["access_token"])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["data"]["name"], "analytics")

    def test_http_auth_origin_host_json_and_limits(self):
        for method, path, body, headers, expected in [
            ("POST", "/v1/quotes", {"service_id": "analytics"}, {}, 401),
            ("POST", "/v1/sessions", {}, {"Origin": "https://attacker.invalid"}, 403),
            ("GET", "/v1/services", None, {"Host": "attacker.invalid"}, 403),
            ("POST", "/v1/sessions", [], {}, 400),
            ("POST", "/v1/sessions", {}, {"Content-Type": "text/plain"}, 415),
            ("POST", "/v1/sessions", {}, {"Content-Length": "20000"}, 413),
        ]:
            with self.subTest(expected=expected, path=path):
                self.assertEqual(self.wire(method, path, body, headers=headers)[0], expected)
        self.assertEqual(self.wire("POST", "/v1/sessions", raw=b'{"x":NaN}')[0], 400)
        self.assertEqual(self.wire("POST", "/v1/sessions", raw=b'{')[0], 400)

    def test_readiness_and_public_stats_headers(self):
        self.assertEqual(self.wire("GET", "/readyz")[0], 200)
        status, body, headers = self.wire("GET", "/stats.json")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.clock.value += self.config.max_sync_age + 1
        self.assertEqual(self.wire("GET", "/readyz")[0], 503)
        self.assertEqual(self.wire("GET", "/healthz")[0], 200)

    def test_mcp_all_five_tools_over_real_http_wire(self):
        harness = self
        class Opener:
            def open(self, request, timeout=0):
                parsed = urlsplit(request.full_url)
                headers = {key: value for key, value in request.header_items()}
                status, raw, response_headers = harness.wire(request.get_method(), parsed.path, headers=headers, raw=request.data)
                if status >= 400:
                    raise HTTPError(request.full_url, status, "error", response_headers, BytesIO(raw))
                return WireResponse(raw, status, response_headers)
        client = Client("http://127.0.0.1:8787", self.root / "session.json")
        with patch("paid_services.mcp.build_opener", return_value=Opener()):
            self.assertEqual(len(client.invoke("list_services", {})["services"]), 2)
            quote = client.invoke("get_quote", {"service_id": "analytics"})
            invoice = client.invoke("create_invoice", {"quote_id": quote["quote_id"], "request_id": "mcp"})
            # Pay using a local SerializedNote fixture, through the real watcher.
            original_token = self.token
            self.token = client.session()
            try:
                self.pay(invoice)
            finally:
                self.token = original_token
            paid = client.invoke("check_invoice", {"invoice_id": invoice["invoice_id"]})
            self.assertEqual(paid["status"], "paid")
            result = client.invoke("call_service", {"invoice_id": invoice["invoice_id"], "service_id": "analytics", "request_id": "consume"})
            self.assertEqual(result["data"]["name"], "analytics")
            self.assertEqual(result, client.invoke("call_service", {"invoice_id": invoice["invoice_id"], "service_id": "analytics", "request_id": "consume"}))
            self.error("quota_exhausted", client.invoke, "call_service", {"invoice_id": invoice["invoice_id"], "service_id": "analytics", "request_id": "extra"})
        self.assertEqual((self.root / "session.json").stat().st_mode & 0o777, 0o600)

    def test_http_session_endpoint_and_cross_account(self):
        status, raw, _ = self.wire("POST", "/v1/sessions", {})
        self.assertEqual(status, 200)
        other = json.loads(raw)["session_token"]
        invoice = self.invoice()
        self.assertEqual(self.wire("GET", "/v1/invoices/" + invoice["invoice_id"], token=other)[0], 404)

    def test_service_and_payer_mcp_complete_core_fixture_purchase(self):
        """Both MCP dispatchers + actual HTTP framing + Core-shaped receipts.

        RPC transport is a local fixture. No broadcast or decryption is claimed.
        """
        from dataclasses import replace
        from decimal import Decimal
        from paid_services.app import Application
        from paid_services.db import Database
        from paid_services.payments import CoreReceiver, PaymentWatcher
        from paid_services.testnet_wallet import TestnetWallet, WalletConfig
        from .test_testnet_wallet import sender_address
        config = replace(self.config, demo=False, receiver="core", rpc_mode="core", data_dir=self.root / "core-service")
        key = config.signing_key()
        db = Database(config.data_dir / "services.sqlite3")
        notes = []
        self.chain.call = lambda method, params: notes
        watcher = PaymentWatcher(config, db, self.chain, CoreReceiver(config, self.chain), self.clock)
        watcher.tick()
        app = Application(config, db, watcher, self.sources, key, self.clock)
        self.server.app = app
        harness = self
        class Opener:
            def open(self, request, timeout=0):
                parsed = urlsplit(request.full_url)
                status, raw, headers = harness.wire(request.get_method(), parsed.path,
                    headers=dict(request.header_items()), raw=request.data)
                if status >= 400:
                    raise HTTPError(request.full_url, status, "error", headers, BytesIO(raw))
                return WireResponse(raw, status, headers)
        class PayerFixture:
            broadcasts = 0
            def require_network(self):
                return {"network": "testnet6", "fixture": True}
            def call(self, method, params):
                harness.assertEqual(method, "shieldsendmany")
                self.broadcasts += 1
                self.last_payment = params[1][0]
                return "a" * 64
        rpc = PayerFixture()
        payer = TestnetWallet(WalletConfig(self.root / "core-payer", sender_address(), [config.address],
                             "http://127.0.0.1:51477"), rpc, self.clock)
        client = Client(config.public_url, self.root / "two-mcp-session.json")
        with patch("paid_services.mcp.build_opener", return_value=Opener()):
            self.assertEqual(client.invoke("list_services", {})["payment_asset"], "tPIV")
            quote = client.invoke("get_quote", {"service_id": "digest"})
            invoice = client.invoke("create_invoice", {"quote_id": quote["quote_id"], "request_id": "two-mcp"})
            payer.invoke("pay_testnet_invoice", {"invoice": invoice})
            self.chain.advance()
            notes.append({"txid": "a" * 64, "outindex": 0, "amount": Decimal(rpc.last_payment["amount"]),
                          "memo": rpc.last_payment["memo"].encode().hex(), "confirmations": 1,
                          "blockheight": self.chain.height})
            watcher.tick()
            self.assertEqual(client.invoke("check_invoice", {"invoice_id": invoice["invoice_id"]})["status"], "paid")
            request = {"invoice_id": invoice["invoice_id"], "service_id": "digest", "request_id": "two-mcp-result"}
            result = client.invoke("call_service", request)
            self.assertEqual(result["service_id"], "digest")
            self.assertEqual(result, client.invoke("call_service", request))
            self.assertTrue(payer.invoke("pay_testnet_invoice", {"invoice": invoice})["replayed"])
        self.assertEqual(rpc.broadcasts, 1)
        self.assertEqual(app.stats()["totals"]["paid_calls"], 1)

    def test_all_static_pages_and_assets_are_served(self):
        for path, content_type in [('/', 'text/html'), ('/stats', 'text/html'), ('/quickstart', 'text/html'),
                                   ('/llms.txt', 'text/plain'), ('/style.css', 'text/css'), ('/app.js', 'text/javascript')]:
            with self.subTest(path=path):
                status, raw, headers = self.wire('GET', path)
                self.assertEqual(status, 200)
                self.assertTrue(headers['Content-Type'].startswith(content_type))
                self.assertGreater(len(raw), 100)
        status, raw, headers = self.wire('HEAD', '/stats')
        self.assertEqual(status, 200)
        self.assertEqual(raw, b'')
        self.assertGreater(int(headers['Content-Length']), 100)


class MCPProtocolTests(Harness):
    def test_stdio_subprocess_handshake_notifications_and_five_tools(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "unknown", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ]
        result = subprocess.run([sys.executable, "-m", "paid_services.mcp", "--session-file", str(self.root / "mcp.json")],
                                input="\n".join(json.dumps(r) for r in requests) + "\n", text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(responses), 4)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(len(responses[1]["result"]["tools"]), 5)
        self.assertTrue(responses[2]["result"]["isError"])
        self.assertEqual(responses[3]["id"], 4)
        self.assertFalse((self.root / "mcp.json").exists())

    def test_protocol_errors_are_jsonrpc_errors(self):
        output = StringIO()
        raw = '\n'.join(['{', '[]', '{"jsonrpc":"2.0","id":1,"method":"tools/list"}']) + '\n'
        serve(Client("http://127.0.0.1:8787", self.root / "unused"), StringIO(raw), output)
        codes = [json.loads(line)["error"]["code"] for line in output.getvalue().splitlines()]
        self.assertEqual(codes, [-32700, -32600, -32002])

