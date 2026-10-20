from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json
import secrets
import time

from .crypto import canonical, digest, random_id, shield_address_valid, sign_token, verify_token
from .errors import ServiceError, require
from .ledger import credit_balance, reconcile_invoices
from .network import ASSET, GENESIS

SAT = Decimal(100_000_000)


def request_key(value):
    require(isinstance(value, str) and 1 <= len(value) <= 128 and all(32 < ord(c) < 127 for c in value),
            400, "invalid_request_id", "request_id must be 1-128 printable ASCII characters")
    return value


class Application:
    def __init__(self, config, db, watcher, sources, key, clock=time.time):
        self.config, self.db, self.watcher, self.sources, self.key, self.clock = config, db, watcher, sources, key, clock
        binding = canonical({"wallet_fingerprint": config.wallet_fingerprint, "address": config.address, "signing_key": digest(key.hex()),
                             "simulation": config.demo, "network": config.network, "genesis": GENESIS, "receiver": config.receiver})
        with db.transaction() as connection:
            previous = connection.execute("SELECT value FROM state WHERE key='installation_binding'").fetchone()
            if previous and previous[0] != binding:
                raise ValueError("Database belongs to another wallet, signing key or simulation mode; preserve its original configuration")
            connection.execute("INSERT OR IGNORE INTO state VALUES ('installation_binding',?)", [binding])

    def now(self):
        return int(self.clock())

    def settled_through(self, connection):
        row = connection.execute("SELECT value FROM state WHERE key='watcher_settled_through'").fetchone()
        return int(row[0]) if row else 0

    def create_session(self):
        token, owner = secrets.token_urlsafe(32), random_id()
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO sessions(id,token_hash,created_at) VALUES (?,?,?)", [owner, digest(token), self.now()])
        return {"session_token": token, "integration_id": owner,
                "notice": "Keep this capability private. It controls invoices, access and credit; it does not identify a wallet."}

    def owner(self, token):
        require(isinstance(token, str) and len(token) <= 256, 401, "unauthorized", "A session token is required")
        rows = self.db.rows("SELECT id,frozen FROM sessions WHERE token_hash=?", [digest(token)])
        require(bool(rows), 401, "unauthorized", "Invalid session token")
        require(not rows[0]["frozen"], 409, "account_review", "Account requires operator reconciliation")
        return rows[0]["id"]

    def services(self):
        return {"schema_version": "1.0", "simulation": self.config.demo, "payment_asset": ASSET, "network": self.config.network,
                "price_mode": self.config.price_mode, "reference_rate": self.config.testnet_reference_rate,
                "payment_channel": "shield", "quote_lifetime_seconds": self.config.quote_seconds,
                "receiver_ready": self.watcher.fresh(),
                "services": [
                    {"id": "analytics", "name": "PIVX chain & governance analytics",
                     "description": "Current network, budget proposals and superblock state from PIVX Core.",
                     "products": [{"id": "single", "usd": self.config.analytics_usd, "calls": 1}]},
                    {"id": "digest", "name": "Privacy-coin & agent research digest",
                     "description": "Source-linked release and news excerpts for PIVX, Monero, Zcash and agent payments. Coverage is declared in each response.",
                     "products": [{"id": "single", "usd": self.config.digest_usd, "calls": 1},
                                  {"id": "week", "usd": self.config.week_usd, "duration_seconds": 604800}]},
                ], "refund_policy": "Underpayments after expiry, late payments and excess payments become private account credit after 3 confirmations. An unused invoice may be cancelled for credit; delivered calls cannot be cancelled. Refunds are requested from credit and manually reconciled, less the network fee. No automatic spend."}

    def quote(self, token, service, product="single"):
        owner = self.owner(token)
        selected = next((s for s in self.services()["services"] if s["id"] == service), None)
        require(selected is not None, 404, "unknown_service", "Unknown service")
        offer = next((p for p in selected["products"] if p["id"] == product), None)
        require(offer is not None, 400, "unknown_product", "Unknown product")
        self.watcher.require_fresh()
        # Do not sell a service whose data cannot be produced.
        self.sources.service(service, {})
        price = self.sources.price()
        rate = Decimal(price["piv_usd"])
        require(rate.is_finite() and rate > 0 and -60 <= self.clock() - price["updated_at"] <= 300,
                503, "price_unavailable", "A fresh PIV/USD price is required")
        amount = int((Decimal(offer["usd"]) / rate * SAT).to_integral_value(rounding=ROUND_CEILING))
        require(0 < amount <= 2**63 - 1, 503, "price_invalid", "Quoted amount is outside supported bounds")
        now, identifier = self.now(), random_id()
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?)",
                               [identifier, owner, service, product, offer["usd"], str(rate), price["updated_at"], amount, now, now + self.config.quote_seconds])
        return {"quote_id": identifier, "service_id": service, "product": product, "usd_reference": offer["usd"],
                "network": self.config.network, "payment_asset": ASSET, "simulation": self.config.demo,
                "piv_usd": str(rate), "price_at": price["updated_at"], "price_source": price["source"],
                "amount_sat": amount, "amount_piv": format(Decimal(amount) / SAT, ".8f"), "expires_at": now + self.config.quote_seconds}

    def create_invoice(self, token, quote_id, key, use_credit=False):
        owner, key = self.owner(token), request_key(key)
        require(type(use_credit) is bool and isinstance(quote_id, str), 400, "invalid_invoice", "Invalid invoice parameters")
        self.watcher.require_fresh()
        with self.db.transaction() as connection:
            previous = connection.execute("SELECT * FROM invoices WHERE owner=? AND request_key=?", [owner, key]).fetchone()
            if previous:
                require(previous["quote_id"] == quote_id, 409, "idempotency_conflict", "request_id already belongs to another quote")
                identifier = previous["id"]
            else:
                quote = connection.execute("SELECT * FROM quotes WHERE id=? AND owner=?", [quote_id, owner]).fetchone()
                require(quote is not None, 404, "quote_not_found", "Quote not found")
                require(quote["expires_at"] > self.now(), 410, "quote_expired", "Quote expired; request a new quote")
                require(connection.execute("SELECT 1 FROM invoices WHERE quote_id=?", [quote_id]).fetchone() is None,
                        409, "quote_already_invoiced", "Quote already has an invoice")
                identifier = random_id()
                used = min(quote["amount"], max(0, credit_balance(connection, owner))) if use_credit else 0
                connection.execute(
                    "INSERT INTO invoices(id,owner,quote_id,request_key,service,product,amount,credit_used,required_confs,created_at,expires_at,jti,calls_remaining) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [identifier, owner, quote_id, key, quote["service"], quote["product"], quote["amount"], used,
                     1 if Decimal(quote["usd"]) < 1 else 3, self.now(), quote["expires_at"], random_id(), -1 if quote["product"] == "week" else 1],
                )
                if used == quote["amount"]:
                    reconcile_invoices(connection, self.now(), self.settled_through(connection), self.config)
        return self.check_invoice(token, identifier)

    def check_invoice(self, token, identifier):
        owner = self.owner(token)
        rows = self.db.rows("SELECT * FROM invoices WHERE id=? AND owner=?", [identifier, owner])
        require(bool(rows), 404, "invoice_not_found", "Invoice not found")
        invoice = rows[0]
        receipts = self.db.rows("SELECT amount,confs,block_time,valid FROM receipts WHERE invoice_id=?", [identifier])
        received = sum(r["amount"] for r in receipts if r["valid"])
        timely = [r for r in receipts if r["valid"] and invoice["created_at"] - 120 <= r["block_time"] <= invoice["expires_at"]]
        timely_amount = sum(r["amount"] for r in timely)
        amount_to_send = max(0, invoice["amount"] - invoice["credit_used"] - timely_amount)
        result = {"invoice_id": identifier, "service_id": invoice["service"], "product": invoice["product"],
                  "network": self.config.network, "payment_asset": ASSET,
                  "status": invoice["status"], "address": self.config.address, "memo": "pivxai:inv:" + identifier,
                  "amount_sat": invoice["amount"], "credit_used_sat": invoice["credit_used"],
                  "amount_to_send_sat": amount_to_send, "amount_to_send_piv": format(Decimal(amount_to_send) / SAT, ".8f"),
                  "received_sat": received, "credit_earned_sat": invoice["credit_earned"],
                  "required_confirmations": invoice["required_confs"], "expires_at": invoice["expires_at"],
                  "access_until": invoice["access_until"], "calls_remaining": invoice["calls_remaining"],
                  "receiver_ready": self.watcher.fresh(), "simulation": self.config.demo}
        if invoice["status"] == "paid" and invoice["access_until"] > self.now() and self.watcher.fresh():
            expires = min(self.now() + self.config.token_seconds, invoice["access_until"])
            result["access_token"] = sign_token(self.key, {"aud": "pivx-paid-services", "sub": owner, "iid": identifier,
                "service": invoice["service"], "jti": invoice["jti"], "nbf": self.now() - 1, "exp": expires})
            result["token_expires_at"] = expires
        return result

    def _authorize_call(self, connection, claims, service):
        invoice = connection.execute("SELECT i.*,s.frozen FROM invoices i JOIN sessions s ON s.id=i.owner WHERE i.id=?", [claims["iid"]]).fetchone()
        require(invoice is not None and not invoice["frozen"] and invoice["status"] == "paid"
                and invoice["owner"] == claims["sub"] and invoice["jti"] == claims["jti"]
                and invoice["service"] == service == claims["service"] and invoice["access_until"] > self.now(),
                403, "access_denied", "Token does not grant access to this service")
        return invoice

    def call_service(self, access_token, service, key, parameters=None):
        require(isinstance(access_token, str) and len(access_token) <= 4096, 401, "invalid_token", "Access token required")
        claims, key = verify_token(self.key, access_token, self.now()), request_key(key)
        parameters = {} if parameters is None else parameters
        require(isinstance(parameters, dict), 400, "invalid_parameters", "parameters must be an object")
        params_hash = digest(canonical(parameters))
        self.watcher.require_fresh()
        with self.db.transaction() as connection:
            invoice = self._authorize_call(connection, claims, service)
            cached = connection.execute("SELECT * FROM calls WHERE invoice_id=? AND request_key=?", [invoice["id"], key]).fetchone()
            if cached:
                require(cached["params_hash"] == params_hash, 409, "idempotency_conflict", "request_id already used with different parameters")
                return json.loads(cached["response"])
            require(invoice["calls_remaining"] != 0, 402, "quota_exhausted", "This invoice has no calls remaining")
        data = self.sources.service(service, parameters)
        response = {"schema_version": "1.0", "service_id": service, "request_id": key, "data": data}
        encoded = canonical(response)
        # Source I/O happens outside the write lock; quota and idempotency are checked again atomically.
        self.watcher.require_fresh()
        with self.db.transaction() as connection:
            invoice = self._authorize_call(connection, claims, service)
            cached = connection.execute("SELECT * FROM calls WHERE invoice_id=? AND request_key=?", [invoice["id"], key]).fetchone()
            if cached:
                require(cached["params_hash"] == params_hash, 409, "idempotency_conflict", "request_id already used with different parameters")
                return json.loads(cached["response"])
            require(invoice["calls_remaining"] != 0, 402, "quota_exhausted", "This invoice has no calls remaining")
            connection.execute("INSERT INTO calls VALUES (?,?,?,?,?)", [invoice["id"], key, params_hash, encoded, self.now()])
            connection.execute("UPDATE invoices SET calls_remaining=CASE WHEN calls_remaining>0 THEN calls_remaining-1 ELSE calls_remaining END WHERE id=?", [invoice["id"]])
        return response

    def account(self, token):
        owner = self.owner(token)
        with self.db.transaction() as connection:
            balance = credit_balance(connection, owner)
            refunds = [dict(r) for r in connection.execute("SELECT id,amount,status,created_at FROM refunds WHERE owner=?", [owner])]
        return {"available_credit_sat": balance, "refunds": refunds}

    def request_refund(self, token, amount, address, key):
        owner, key = self.owner(token), request_key(key)
        require(type(amount) is int and 0 < amount <= 2**63 - 1 and shield_address_valid(address),
                400, "invalid_refund", "Refund requires a positive base-unit amount and a valid shielded address")
        self.watcher.require_fresh()
        with self.db.transaction() as connection:
            existing = connection.execute("SELECT * FROM refunds WHERE owner=? AND request_key=?", [owner, key]).fetchone()
            if existing:
                require(existing["amount"] == amount and existing["address"] == address, 409, "idempotency_conflict", "Refund request_id already used")
                identifier = existing["id"]
            else:
                require(credit_balance(connection, owner) >= amount, 409, "insufficient_credit", "Refund exceeds confirmed available credit")
                identifier = random_id()
                connection.execute("INSERT INTO refunds(id,owner,amount,address,request_key,created_at) VALUES (?,?,?,?,?,?)",
                                   [identifier, owner, amount, address, key, self.now()])
        return {"refund_id": identifier, "status": "requested", "reserved_sat": amount,
                "policy": "Operator refunds to the supplied shielded address after reconciliation, less the network fee. Dust below the fee stays as credit."}

    def stats(self):
        now = self.now()
        start = now - 30 * 86400
        call_rows = self.db.rows("SELECT strftime('%Y-%m-%d',c.created_at,'unixepoch') AS day,COUNT(*) AS paid_calls,COUNT(DISTINCT i.owner) AS integrations FROM calls c JOIN invoices i ON c.invoice_id=i.id WHERE c.created_at>=? GROUP BY day", [start])
        volume_rows = self.db.rows("SELECT strftime('%Y-%m-%d',r.first_seen,'unixepoch') AS day,SUM(r.amount) AS piv_volume_sat,COUNT(*) AS payment_count FROM receipts r JOIN invoices i ON r.invoice_id=i.id WHERE r.valid=1 AND r.confs>=i.required_confs AND r.first_seen>=? GROUP BY day", [start])
        days = {}
        for row in call_rows + volume_rows:
            days.setdefault(row["day"], {"day": row["day"], "paid_calls": 0, "integrations": 0, "piv_volume_sat": 0, "payment_count": 0}).update(row)
        totals = self.db.rows("SELECT COUNT(*) AS paid_calls,COUNT(DISTINCT i.owner) AS unique_integrations FROM calls c JOIN invoices i ON c.invoice_id=i.id WHERE c.created_at>=?", [start])[0]
        totals.update({"piv_volume_sat": sum(row["piv_volume_sat"] for row in volume_rows), "payment_count": sum(row["payment_count"] for row in volume_rows)})
        weeks = []
        for index in range(4):
            lower, upper = now - (index + 1) * 7 * 86400, now - index * 7 * 86400 + (1 if index == 0 else 0)
            calls = self.db.rows("SELECT COUNT(*) AS paid_calls,COUNT(DISTINCT i.owner) AS unique_integrations FROM calls c JOIN invoices i ON i.id=c.invoice_id WHERE c.created_at>=? AND c.created_at<?", [lower, upper])[0]
            volume = self.db.rows("SELECT COALESCE(SUM(r.amount),0) AS piv_volume_sat,COUNT(*) AS payment_count FROM receipts r JOIN invoices i ON i.id=r.invoice_id WHERE r.valid=1 AND r.confs>=i.required_confs AND r.first_seen>=? AND r.first_seen<?", [lower, upper])[0]
            weeks.append({"from": lower, "to": upper, **calls, **volume})
        return {"schema_version": "1.0", "generated_at": now, "timezone": "UTC", "window_days": 30,
                "network": self.config.network, "payment_asset": ASSET,
                "simulation": self.config.demo, "receiver_ready": self.watcher.fresh(), "totals": totals,
                "daily": sorted(days.values(), key=lambda row: row["day"]), "weekly": weeks,
                "payer_metric": "Unique anonymous integration sessions that consumed a paid call; not unique wallets.",
                "volume_metric": "Confirmed invoice-bound receipts, including excess and late credit, before refunds. Duplicate observations count once."}

    def cancel_refund(self, token, identifier):
        owner = self.owner(token)
        with self.db.transaction() as connection:
            refund = connection.execute("SELECT * FROM refunds WHERE id=? AND owner=?", [identifier, owner]).fetchone()
            require(refund is not None, 404, "refund_not_found", "Refund not found")
            require(refund["status"] in {"requested", "cancelled"}, 409, "refund_already_sent", "A recorded broadcast cannot be cancelled")
            connection.execute("UPDATE refunds SET status='cancelled' WHERE id=?", [identifier])
        return {"refund_id": identifier, "status": "cancelled", "released_credit_sat": refund["amount"]}

    def cancel_invoice(self, token, identifier):
        owner = self.owner(token)
        with self.db.transaction() as connection:
            invoice = connection.execute("SELECT * FROM invoices WHERE id=? AND owner=?", [identifier, owner]).fetchone()
            require(invoice is not None, 404, "invoice_not_found", "Invoice not found")
            require(invoice["status"] != "review", 409, "invoice_review", "Invoice requires operator reconciliation")
            require(connection.execute("SELECT 1 FROM calls WHERE invoice_id=? LIMIT 1", [identifier]).fetchone() is None,
                    409, "already_delivered", "An invoice with a delivered result cannot be cancelled")
            connection.execute("UPDATE invoices SET status='expired' WHERE id=?", [identifier])
            reconcile_invoices(connection, self.now(), self.settled_through(connection), self.config)
        return self.check_invoice(token, identifier)
