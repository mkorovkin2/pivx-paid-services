"""Aggregate-only static export and explicitly labelled payment measurements."""
import json
from math import ceil
from pathlib import Path
from statistics import median

from .crypto import canonical
from .errors import require


def latency_report(app):
    rows = app.db.rows("""SELECT i.required_confs,i.paid_at,MIN(r.first_seen) AS detected_at,
                         MIN(c.created_at) AS result_at
                         FROM invoices i JOIN receipts r ON r.invoice_id=i.id
                         LEFT JOIN calls c ON c.invoice_id=i.id
                         WHERE i.status='paid' AND r.valid=1 AND i.required_confs=1
                         GROUP BY i.id""")
    access = sorted(max(0, r["paid_at"] - r["detected_at"]) for r in rows)
    results = sorted(max(0, r["result_at"] - r["detected_at"]) for r in rows if r["result_at"] is not None)
    def summarize(values):
        return {"samples": len(values), "median_seconds": median(values) if values else None,
                "p95_seconds": values[ceil(len(values) * .95) - 1] if values else None}
    enough = len(access) >= 50
    return {"network": app.config.network, "simulation": app.config.demo,
            "measurement": "First receiver observation to access grant/result; excludes pre-detection broadcast time",
            "detection_to_access": summarize(access), "detection_to_result": summarize(results),
            "minimum_samples": 50,
            "live_target_met": not app.config.demo and enough and access[ceil(len(access) * .95) - 1] <= 120,
            "notice": "Simulation timing is not on-chain performance evidence. Broadcast-to-result and external beta metrics require separate live records."}


def export_site(app, destination):
    """Reuse the dashboard with relative assets for GitHub Pages project URLs."""
    destination = Path(destination)
    require(not destination.exists() or not any(destination.iterdir()),
            409, "export_exists", "Static export requires an empty output directory")
    destination.mkdir(parents=True, exist_ok=True)
    static = Path(__file__).with_name("static")
    page = (static / "stats.html").read_text()
    page = page.replace('data-page="stats"', 'data-page="stats" data-static="true"')
    page = page.replace('href="/"', 'href="./"').replace('href="/stats"', 'href="./"')
    page = page.replace('<a href="/quickstart">Quickstart</a>', '')
    for asset in ("style.css", "app.js", "stats.json"):
        page = page.replace('"/' + asset + '"', '"./' + asset + '"')
    page = page.replace('<a href="/llms.txt">Agent discovery</a>', '<span>Public testnet6 aggregates</span>')
    page = page.replace("Statistics update from the payment ledger on every request.",
                        "Exported ledger snapshot. Totals refresh when the operator publishes a new export.")
    (destination / "index.html").write_text(page)
    for asset in ("style.css", "app.js"):
        (destination / asset).write_bytes((static / asset).read_bytes())
    (destination / "stats.json").write_text(canonical(app.stats()) + "\n")
    (destination / ".nojekyll").write_text("")
    return {"exported": str(destination), "network": app.config.network, "simulation": app.config.demo,
            "published": False, "notice": "Upload these static files to GitHub Pages after review. No private ledger or payment identifiers are included."}
