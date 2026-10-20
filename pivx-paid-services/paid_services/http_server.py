from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit

from .crypto import canonical
from .errors import ServiceError, require

STATIC = Path(__file__).with_name("static")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = True

    def __init__(self, address, app, bind_and_activate=True):
        self.app = app
        self.rate_lock = threading.Lock()
        self.rate = {}
        self.capacity = threading.BoundedSemaphore(32)
        super().__init__(address, Handler, bind_and_activate=bind_and_activate)

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.capacity.release()

    def throttle(self, ip, expensive):
        now = time.monotonic()
        with self.rate_lock:
            if len(self.rate) > 10000:
                self.rate = {key: value for key, value in self.rate.items() if now - value[0] < 60}
            key = (ip, expensive)
            start, count = self.rate.get(key, (now, 0))
            if now - start >= 60:
                start, count = now, 0
            self.rate[key] = start, count + 1
            require(count < (60 if expensive else 600), 429, "rate_limited", "Too many requests; retry in one minute")


class Handler(BaseHTTPRequestHandler):
    server_version = "PIVXServices/0.1"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass  # Never put invoice identifiers, tokens or payment metadata in access logs.

    def send_content(self, status, data, content_type="application/json; charset=utf-8"):
        body = data if isinstance(data, bytes) else canonical(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def bearer(self):
        auth = self.headers.get("Authorization", "")
        require(auth.startswith("Bearer "), 401, "unauthorized", "Bearer authentication is required")
        return auth[7:]

    def handle_request(self):
        try:
            app = self.server.app
            origin = self.headers.get("Origin")
            expected_origin = app.config.public_url.rstrip("/")
            require(not origin or origin == expected_origin, 403, "invalid_origin", "Origin is not allowed")
            expected_host = urlsplit(app.config.public_url).netloc
            local_host = f"{app.config.bind}:{self.server.server_port}"
            require(self.headers.get("Host", "") in {expected_host, local_host}, 403, "invalid_host", "Host is not allowed")
            path = urlsplit(self.path).path
            require(len(self.path) <= 2048, 414, "path_too_long", "Request path is too long")
            self.server.throttle(self.client_address[0], self.command == "POST" and path in {"/v1/sessions", "/v1/quotes"})
            body = {}
            if self.command == "POST":
                require(not self.headers.get("Transfer-Encoding"), 400, "invalid_framing", "Transfer-Encoding is unsupported")
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise ServiceError(400, "invalid_body", "Invalid content length") from None
                require(0 <= size <= 16384, 413, "body_too_large", "Request body exceeds 16 KiB")
                require(self.headers.get_content_type() == "application/json", 415, "content_type", "Use application/json")
                try:
                    body = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, UnicodeError):
                    raise ServiceError(400, "invalid_json", "Invalid JSON body") from None
                require(isinstance(body, dict), 400, "invalid_body", "Request body must be an object")
            method = "GET" if self.command == "HEAD" else self.command
            if method == "GET" and path in {"/healthz", "/readyz"}:
                ready = app.watcher.fresh()
                self.send_content(200 if path == "/healthz" or ready else 503,
                                  {"status": "ok" if path == "/healthz" else "ready" if ready else "waiting_for_receiver", "receiver_ready": ready, "simulation": app.config.demo})
                return
            if method == "GET" and path == "/v1/services":
                result = app.services()
            elif method == "POST" and path == "/v1/sessions":
                result = app.create_session()
            elif method == "POST" and path == "/v1/quotes":
                result = app.quote(self.bearer(), body.get("service_id"), body.get("product", "single"))
            elif method == "POST" and path == "/v1/invoices":
                result = app.create_invoice(self.bearer(), body.get("quote_id"), body.get("request_id"), body.get("use_credit", False))
            elif method == "GET" and re.fullmatch(r"/v1/invoices/[A-Z2-7]{16}", path):
                result = app.check_invoice(self.bearer(), path.rsplit("/", 1)[1])
            elif method == "POST" and re.fullmatch(r"/v1/invoices/[A-Z2-7]{16}/cancel", path):
                result = app.cancel_invoice(self.bearer(), path.split("/")[3])
            elif method == "GET" and path == "/v1/account":
                result = app.account(self.bearer())
            elif method == "POST" and path == "/v1/refunds":
                result = app.request_refund(self.bearer(), body.get("amount_sat"), body.get("address"), body.get("request_id"))
            elif method == "POST" and re.fullmatch(r"/v1/refunds/[A-Z2-7]{16}/cancel", path):
                result = app.cancel_refund(self.bearer(), path.split("/")[3])
            elif method == "POST" and re.fullmatch(r"/v1/services/(analytics|digest)/call", path):
                result = app.call_service(self.bearer(), path.split("/")[3], body.get("request_id"), body.get("parameters"))
            elif method == "GET" and path == "/stats.json":
                result = app.stats()
            elif method == "GET" and path in {"/", "/stats", "/quickstart", "/llms.txt", "/app.js", "/style.css"}:
                filename, content_type = {"/": ("index.html", "text/html"), "/stats": ("stats.html", "text/html"),
                    "/quickstart": ("quickstart.html", "text/html"), "/llms.txt": ("llms.txt", "text/plain"),
                    "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}[path]
                self.send_content(200, (STATIC / filename).read_bytes(), content_type + "; charset=utf-8")
                return
            else:
                raise ServiceError(404, "not_found", "Route not found")
            self.send_content(200, result)
        except ServiceError as error:
            self.send_content(error.status, {"error": {"code": error.code, "message": error.message}})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self.send_content(500, {"error": {"code": "internal_error", "message": "Request failed; retry using the same request_id"}})

    do_GET = handle_request
    do_POST = handle_request
    do_HEAD = handle_request
