"""Invoice accounting runs inside one SQLite write transaction."""


def credit_balance(connection, owner):
    row = connection.execute(
        "SELECT COALESCE(SUM(credit_earned),0)-COALESCE(SUM(CASE WHEN status='expired' THEN 0 ELSE credit_used END),0) FROM invoices WHERE owner=?",
        [owner],
    ).fetchone()
    refunds = connection.execute("SELECT COALESCE(SUM(amount),0) FROM refunds WHERE owner=? AND status IN ('requested','broadcast')", [owner]).fetchone()[0]
    return row[0] - refunds


def reconcile_invoices(connection, now, settled_through, config):
    for invoice in connection.execute("SELECT * FROM invoices ORDER BY created_at,id").fetchall():
        receipts = connection.execute("SELECT * FROM receipts WHERE invoice_id=?", [invoice["id"]]).fetchall()
        if any(not receipt["valid"] for receipt in receipts):
            connection.execute("UPDATE invoices SET status='review',credit_earned=0 WHERE id=?", [invoice["id"]])
            connection.execute("UPDATE sessions SET frozen=1 WHERE id=?", [invoice["owner"]])
            continue
        if invoice["status"] == "review":
            continue
        timely = [receipt for receipt in receipts if invoice["created_at"] - 120 <= receipt["block_time"] <= invoice["expires_at"]]
        received = sum(receipt["amount"] for receipt in timely)
        confirmed = sum(receipt["amount"] for receipt in timely if receipt["confs"] >= invoice["required_confs"])
        due = invoice["amount"] - invoice["credit_used"]
        # A previously issued entitlement never silently reactivates after a rollback.
        if invoice["paid_at"] is not None and confirmed < due:
            connection.execute("UPDATE invoices SET status='review',credit_earned=0 WHERE id=?", [invoice["id"]])
            connection.execute("UPDATE sessions SET frozen=1 WHERE id=?", [invoice["owner"]])
            continue
        status = invoice["status"]
        if status != "expired":
            if confirmed >= due:
                status = "paid"
                if invoice["paid_at"] is None:
                    access = 7 * 86400 if invoice["product"] == "week" else config.single_access_seconds
                    connection.execute("UPDATE invoices SET paid_at=?,access_until=? WHERE id=?", [now, now + access, invoice["id"]])
            elif received >= due:
                status = "confirming"
            elif min(now, settled_through) >= invoice["expires_at"]:
                status = "expired"
            elif received:
                status = "partially_paid"
            else:
                status = "pending"
        confirmed_for_credit = sum(receipt["amount"] for receipt in receipts if receipt["confs"] >= 3)
        earned = confirmed_for_credit if status == "expired" else max(0, confirmed_for_credit - due) if status == "paid" else 0
        connection.execute("UPDATE invoices SET status=?,credit_earned=? WHERE id=?", [status, earned, invoice["id"]])
    for session in connection.execute("SELECT id FROM sessions").fetchall():
        if credit_balance(connection, session["id"]) < 0:
            connection.execute("UPDATE sessions SET frozen=1 WHERE id=?", [session["id"]])

