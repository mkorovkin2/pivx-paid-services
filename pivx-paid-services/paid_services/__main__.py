import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time

from .app import Application
from .artifacts import export_site, latency_report
from .config import Config
from .crypto import canonical
from .db import Database
from .errors import ServiceError, require
from .http_server import Server
from .mcp import Client
from .payments import CoreReceiver, PaymentWatcher
from .simulation import Simulation
from .sources import Chain, Sources


def build(config, simulation=None):
    config.validate()
    key = config.signing_key()
    db = Database(config.data_dir / "services.sqlite3")
    chain = simulation or Chain(config)
    snapshot = CoreReceiver(config, chain) if config.receiver == "core" else None
    watcher = PaymentWatcher(config, db, chain, snapshot=snapshot)
    sources = simulation or Sources(config, chain, db)
    return Application(config, db, watcher, sources, key)


def run_server(app, simulation=None):
    config = app.config
    stop = threading.Event()
    server = Server((config.bind, config.port), app)
    def shutdown(*_):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown)
    def simulate():
        while not stop.is_set():
            simulation.advance(config, app.db)
            try:
                app.watcher.tick()
            except ServiceError:
                pass
            stop.wait(2)
    worker = threading.Thread(target=simulate if simulation else app.watcher.run, args=() if simulation else (stop,), daemon=True)
    worker.start()
    print(("LOCAL SIMULATION; no money is sent. " if config.demo else "") + "Serving " + config.public_url, file=sys.stderr)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=15)


def main():
    parser = argparse.ArgumentParser(description="Shielded PIV data services; no paid upstream APIs")
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("serve", help="Run with a real, dedicated receiving wallet")
    live.add_argument("--config", required=True)
    demo = sub.add_parser("demo", help="Explicit zero-cost local simulation; never sends a transaction")
    demo.add_argument("--data-dir", default="data/simulation")
    demo.add_argument("--port", type=int, default=8787)
    demo.add_argument("--headless", action="store_true", help="Run both purchases without opening a network listener")
    for name in ("invoice", "result", "account", "refund", "cancel-refund", "cancel-invoice"):
        command = sub.add_parser(name)
        command.add_argument("--url", default=os.environ.get("PIVX_SERVICES_URL", "http://127.0.0.1:8787"))
        command.add_argument("--session-file", default="data/cli-session.json")
        if name == "invoice":
            command.add_argument("--service", choices=["analytics", "digest"], required=True)
            command.add_argument("--product", choices=["single", "week"], default="single")
            command.add_argument("--request-id", required=True)
            command.add_argument("--use-credit", action="store_true")
        elif name == "result":
            command.add_argument("--invoice-id", required=True)
            command.add_argument("--service", choices=["analytics", "digest"], required=True)
            command.add_argument("--request-id", required=True)
            command.add_argument("--wait", type=int, default=180)
        elif name == "refund":
            command.add_argument("--amount-sat", type=int, required=True)
            command.add_argument("--address", required=True)
            command.add_argument("--request-id", required=True)
        elif name == "cancel-refund":
            command.add_argument("--refund-id", required=True)
        elif name == "cancel-invoice":
            command.add_argument("--invoice-id", required=True)
    operator = sub.add_parser("operator", help="Inspect and reconcile the local ledger; never sends funds")
    operator.add_argument("--config", required=True)
    operator.add_argument("action", choices=["status", "refunds", "record-refund", "export-stats", "export-site", "latency", "backup"])
    operator.add_argument("--refund-id")
    operator.add_argument("--txid")
    operator.add_argument("--fee-sat", type=int)
    operator.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            run_server(build(Config.load(args.config)))
            return
        if args.command == "demo":
            simulation = Simulation(args.data_dir)
            config = simulation.config(args.port)
            app = build(config, simulation)
            simulation.advance(config)
            app.watcher.tick()
            if not args.headless:
                run_server(app, simulation)
                return
            session = app.create_session()["session_token"]
            transcript = []
            for service, product in [("analytics", "single"), ("digest", "week")]:
                quote = app.quote(session, service, product)
                invoice = app.create_invoice(session, quote["quote_id"], "demo:" + quote["quote_id"])
                for _ in range(3):
                    simulation.advance(config, invoice=invoice)
                    app.watcher.tick()
                paid = app.check_invoice(session, invoice["invoice_id"])
                result = app.call_service(paid["access_token"], service, "demo-result")
                assert result == app.call_service(paid["access_token"], service, "demo-result")
                transcript.append({"service": service, "product": product, "state": paid["status"],
                                   "required_confirmations": paid["required_confirmations"], "result": result})
            print(json.dumps({"simulation": True, "real_transactions": 0, "purchases": transcript, "stats": app.stats()}, indent=2))
            return
        if args.command == "operator":
            config = Config.load(args.config)
            app = build(config)
            if args.action == "status":
                output = {"watcher": app.db.rows("SELECT * FROM state WHERE key LIKE 'watcher_%'"),
                          "invoice_states": app.db.rows("SELECT status,COUNT(*) AS count FROM invoices GROUP BY status")}
            elif args.action == "refunds":
                output = app.db.rows("SELECT id,amount,address,status,txid,fee,created_at FROM refunds ORDER BY created_at")
            elif args.action == "record-refund":
                import re
                require(bool(args.refund_id and args.txid and re.fullmatch(r"[0-9a-f]{64}", args.txid)) and args.fee_sat is not None and args.fee_sat >= 0,
                        400, "invalid_refund_record", "Supply refund-id, 64-character txid and non-negative fee-sat")
                with app.db.transaction() as connection:
                    refund = connection.execute("SELECT * FROM refunds WHERE id=?", [args.refund_id]).fetchone()
                    require(refund is not None and args.fee_sat < refund["amount"], 400, "invalid_refund_record", "Refund must exist and exceed the network fee")
                    if refund["status"] == "broadcast":
                        require(refund["txid"] == args.txid and refund["fee"] == args.fee_sat, 409, "refund_conflict", "Refund already recorded differently")
                    else:
                        connection.execute("UPDATE refunds SET status='broadcast',txid=?,fee=?,completed_at=? WHERE id=?", [args.txid, args.fee_sat, int(time.time()), args.refund_id])
                output = {"refund_id": args.refund_id, "status": "broadcast", "notice": "Operator record only; verify the chain independently"}
            elif args.action == "export-stats":
                require(bool(args.output), 400, "missing_output", "Supply --output")
                destination = Path(args.output)
                temporary = destination.with_suffix(".next")
                temporary.write_text(canonical(app.stats()) + "\n")
                temporary.replace(destination)
                output = {"exported": str(destination)}
            elif args.action == "export-site":
                require(bool(args.output), 400, "missing_output", "Supply --output")
                output = export_site(app, args.output)
            elif args.action == "latency":
                output = latency_report(app)
            else:
                require(bool(args.output), 400, "missing_output", "Supply --output")
                source = app.db.connect()
                fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
                target = sqlite3.connect(args.output)
                try:
                    source.backup(target)
                finally:
                    source.close()
                    target.close()
                output = {"backup": args.output, "notice": "Back up signing.key and the merchant wallet separately using owner-only storage"}
            print(json.dumps(output, indent=2))
            return
        client = Client(args.url, args.session_file, os.environ.get("PIVX_SERVICES_SESSION_TOKEN"))
        if args.command == "invoice":
            # Persist the request-to-invoice binding so a CLI retry does not create a second quote/purchase.
            cache = Path(args.session_file + ".invoices.json")
            cache.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            import fcntl
            fd = os.open(cache, os.O_RDWR | os.O_CREAT, 0o600)
            with os.fdopen(fd, "r+") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                state = json.loads(handle.read() or "{}")
                identity = canonical([args.url, args.request_id])
                previous = state.get(identity)
                if previous:
                    require(previous["service"] == args.service and previous["product"] == args.product and previous["use_credit"] == args.use_credit,
                            409, "idempotency_conflict", "Request ID already used for a different purchase")
                else:
                    quote = client.invoke("get_quote", {"service_id": args.service, "product": args.product})
                    previous = {"service": args.service, "product": args.product, "use_credit": args.use_credit, "quote_id": quote["quote_id"], "invoice_id": None}
                    state[identity] = previous
                def persist():
                    handle.seek(0)
                    handle.write(canonical(state))
                    handle.truncate()
                    handle.flush()
                    os.fsync(handle.fileno())
                persist()
                if previous["invoice_id"]:
                    result = client.invoke("check_invoice", {"invoice_id": previous["invoice_id"]})
                else:
                    result = client.invoke("create_invoice", {"quote_id": previous["quote_id"], "request_id": args.request_id, "use_credit": args.use_credit})
                    previous["invoice_id"] = result["invoice_id"]
                    persist()
        elif args.command == "result":
            deadline = time.monotonic() + max(0, min(args.wait, 900))
            while True:
                invoice = client.invoke("check_invoice", {"invoice_id": args.invoice_id})
                if "access_token" in invoice:
                    result = client.invoke("call_service", {"invoice_id": args.invoice_id, "service_id": args.service, "request_id": args.request_id})
                    break
                require(invoice["status"] not in {"expired", "review"}, 402, "invoice_unpaid", "Invoice expired or requires reconciliation")
                require(time.monotonic() < deadline, 408, "payment_timeout", "Payment not confirmed yet; retry with the same request-id")
                time.sleep(min(10, max(0, deadline - time.monotonic())))
        elif args.command == "account":
            result = client.request("GET", "/v1/account", token=client.session())
        elif args.command == "cancel-refund":
            result = client.request("POST", "/v1/refunds/" + args.refund_id + "/cancel", {}, client.session())
        elif args.command == "cancel-invoice":
            result = client.request("POST", "/v1/invoices/" + args.invoice_id + "/cancel", {}, client.session())
        else:
            result = client.request("POST", "/v1/refunds", {"amount_sat": args.amount_sat, "address": args.address, "request_id": args.request_id}, client.session())
        print(json.dumps(result, indent=2))
    except (ServiceError, ValueError, OSError) as error:
        if isinstance(error, ServiceError):
            print(canonical({"error": {"code": error.code, "message": error.message}}), file=sys.stderr)
        else:
            print("Configuration or I/O failure: " + str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
