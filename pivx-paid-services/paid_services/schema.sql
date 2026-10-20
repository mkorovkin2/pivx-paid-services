PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, created_at INTEGER NOT NULL, frozen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS quotes (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES sessions(id), service TEXT NOT NULL, product TEXT NOT NULL,
 usd TEXT NOT NULL, piv_usd TEXT NOT NULL, price_at INTEGER NOT NULL, amount INTEGER NOT NULL CHECK(amount>0),
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES sessions(id), quote_id TEXT NOT NULL UNIQUE REFERENCES quotes(id),
 request_key TEXT NOT NULL, service TEXT NOT NULL, product TEXT NOT NULL, amount INTEGER NOT NULL,
 credit_used INTEGER NOT NULL DEFAULT 0, credit_earned INTEGER NOT NULL DEFAULT 0,
 required_confs INTEGER NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', paid_at INTEGER, access_until INTEGER, jti TEXT NOT NULL,
 calls_remaining INTEGER NOT NULL DEFAULT 1, UNIQUE(owner,request_key)
);
CREATE TABLE IF NOT EXISTS receipts (
 nullifier TEXT PRIMARY KEY, invoice_id TEXT REFERENCES invoices(id), memo TEXT NOT NULL, amount INTEGER NOT NULL,
 height INTEGER NOT NULL, block_hash TEXT NOT NULL, block_time INTEGER NOT NULL,
 first_seen INTEGER NOT NULL, confs INTEGER NOT NULL DEFAULT 0, valid INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS receipts_invoice ON receipts(invoice_id);
CREATE TABLE IF NOT EXISTS calls (
 invoice_id TEXT NOT NULL REFERENCES invoices(id), request_key TEXT NOT NULL,
 params_hash TEXT NOT NULL, response TEXT NOT NULL, created_at INTEGER NOT NULL,
 PRIMARY KEY(invoice_id,request_key)
);
CREATE TABLE IF NOT EXISTS refunds (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES sessions(id), amount INTEGER NOT NULL CHECK(amount>0),
 address TEXT NOT NULL, request_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'requested',
 txid TEXT UNIQUE, fee INTEGER, created_at INTEGER NOT NULL, completed_at INTEGER, UNIQUE(owner,request_key)
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
PRAGMA user_version=1;
