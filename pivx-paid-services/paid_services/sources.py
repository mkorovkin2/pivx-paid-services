"""Free, read-only data adapters. Failures never become invented service data."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
import base64
import html
import json
import os
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from types import SimpleNamespace
import xml.etree.ElementTree as ET

from .config import safe_url
from .errors import ServiceError, require
from .network import GENESIS, NETWORK


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward node credentials to a redirected host.
        return None


def fetch(url, body=None, headers=None, max_bytes=4_194_304, timeout=8):
    safe_url(url)
    request = Request(url, data=body, headers={"User-Agent": "pivx-paid-services/0.1.0", **(headers or {})})
    try:
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("Source response too large")
            return raw
    except HTTPError as error:
        error.close()
        raise ServiceError(503, "source_unavailable", "A required upstream source is unavailable") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ServiceError(503, "source_unavailable", "A required upstream source is unavailable") from None


def decode_json(raw, exact=False):
    try:
        return json.loads(raw, parse_float=Decimal if exact else float,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (ValueError, UnicodeError):
        raise ServiceError(503, "source_invalid", "Upstream returned invalid JSON") from None


def require_synced(info):
    # Current PIVX Core uses initial_block_downloading; accept older proxy
    # aliases only if every reported flag explicitly says false.
    flags = [info[name] for name in ("initial_block_downloading", "initial_block_download", "initialblockdownload") if name in info]
    require(bool(flags) and all(flag is False for flag in flags),
            503, "node_syncing", "The node is syncing or its sync status is unknown")


class Chain:
    ALLOWED = {"getblockcount", "getblockhash", "getblockheader", "getblockchaininfo", "getnetworkinfo", "getbudgetinfo", "getnextsuperblock", "getmasternodecount"}

    def __init__(self, config, auth_env_prefix="PIVX_RPC"):
        self.config = config
        self.auth_env_prefix = auth_env_prefix

    def call(self, method, params=()):
        receiver_read = method == "listreceivedbyshieldaddress" and self.config.rpc_mode == "core" and self.config.receiver == "core"
        if method not in self.ALLOWED and not receiver_read:
            raise ValueError("Only read-only RPC methods are allowed")
        headers = {}
        if self.config.rpc_mode == "core":
            credentials = os.environ.get(self.auth_env_prefix + "_USER", "") + ":" + os.environ.get(self.auth_env_prefix + "_PASSWORD", "")
            if credentials != ":":
                headers["Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
            headers["Content-Type"] = "application/json"
            body = json.dumps({"jsonrpc": "1.0", "id": "paid-services", "method": method, "params": list(params)}).encode()
            result = decode_json(fetch(self.config.rpc_url, body, headers,
                                       timeout=getattr(self.config, "rpc_timeout_seconds", 8)), exact=receiver_read)
            require(isinstance(result, dict) and not result.get("error") and "result" in result,
                    503, "rpc_error", "The node did not return a successful RPC result")
            return result["result"]
        query = urlencode({"params": ",".join(str(p).lower() if isinstance(p, bool) else str(p) for p in params)}) if params else ""
        result = decode_json(fetch(f"{self.config.rpc_url.rstrip('/')}/{method}" + ("?" + query if query else "")))
        # Both the bridge raw-result format and JSON-RPC envelopes are supported.
        if isinstance(result, dict) and "result" in result and "error" in result:
            require(not result["error"], 503, "rpc_error", "The node returned an RPC error")
            return result["result"]
        return result

    def require_network(self):
        info = self.call("getblockchaininfo")
        require(isinstance(info, dict) and info.get("chain") == "test" and self.block_hash(0) == GENESIS,
                503, "wrong_network", "Receiver must use the pinned PIVX testnet6 chain")
        require_synced(info)
        return {"network": NETWORK, "genesis": GENESIS}

    def tip(self):
        value = self.call("getblockcount")
        require(type(value) is int and value > 0, 503, "rpc_invalid", "Invalid chain height")
        return value

    def block_hash(self, height):
        value = self.call("getblockhash", [height])
        require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), 503, "rpc_invalid", "Invalid block hash")
        return value

    def header(self, block_hash):
        value = self.call("getblockheader", [block_hash])
        require(isinstance(value, dict) and type(value.get("height")) is int and type(value.get("time")) is int,
                503, "rpc_invalid", "Invalid block header")
        require(value.get("hash") == block_hash and value.get("confirmations", 1) > 0,
                503, "rpc_invalid", "Block header is not on the active chain")
        return value


def parse_feed(raw, source, now, window_days):
    # ElementTree does not resolve external entities. Reject DTDs as well.
    require(b"<!DOCTYPE" not in raw.upper() and b"<!ENTITY" not in raw.upper(),
            503, "feed_invalid", "Feed contains an unsupported document type")
    try:
        root = ET.fromstring(raw)
        atom = "{http://www.w3.org/2005/Atom}"
        entries = root.findall(atom + "entry") if root.tag == atom + "feed" else root.findall("./channel/item")
        items = []
        for entry in entries[:100]:
            is_atom = entry.tag == atom + "entry"
            def value(tag):
                return entry.findtext((atom if is_atom else "") + tag, "")
            date = (value("published") or value("updated")) if is_atom else value("pubDate")
            try:
                parsed = datetime.fromisoformat(date.replace("Z", "+00:00")) if is_atom else parsedate_to_datetime(date)
                if parsed.tzinfo is None:
                    continue
                published = int(parsed.timestamp())
            except (ValueError, TypeError, OverflowError):
                continue
            if not now - window_days * 86400 <= published <= now + 300:
                continue
            if is_atom:
                links = entry.findall(atom + "link")
                url = next((link.get("href", "") for link in links if link.get("rel", "alternate") == "alternate"), "")
            else:
                url = value("link")
            if urlsplit(url).scheme != "https":
                continue
            title = html.unescape(re.sub(r"<[^>]*>", " ", value("title"))).strip()[:300]
            content = value("summary") or value("content") if is_atom else value("description")
            summary = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", content)).split())[:1200]
            items.append({"title": title, "summary": summary, "url": url, "published_at": published,
                          "source": source["name"], "topic": source["topic"]})
        return items
    except ET.ParseError:
        raise ServiceError(503, "feed_invalid", "Source returned invalid RSS/Atom") from None


class Sources:
    def __init__(self, config, chain, db, clock=time.time):
        self.config, self.chain, self.db, self.clock = config, chain, db, clock
        # Public mainnet data can be sold for testnet payments. It has no route
        # to the receiver/payer RPCs or their credentials.
        self.analytics_chain = Chain(SimpleNamespace(rpc_url=config.analytics_rpc_url, rpc_mode="gateway", receiver="analytics")) if config.analytics_rpc_url else chain
        self.lock = threading.RLock()

    def cached(self, key, age, loader):
        with self.lock:
            row = self.db.rows("SELECT value FROM state WHERE key=?", ["source:" + key])
            if row:
                cached = json.loads(row[0]["value"])
                if 0 <= self.clock() - cached["fetched_at"] < age:
                    return cached["data"]
            data = loader()
            with self.db.transaction() as connection:
                connection.execute("INSERT OR REPLACE INTO state VALUES (?,?)",
                                   ["source:" + key, json.dumps({"fetched_at": int(self.clock()), "data": data}, allow_nan=False)])
            return data

    def price(self):
        if self.config.price_mode == "fixed_testnet":
            return {"piv_usd": self.config.testnet_reference_rate, "updated_at": int(self.clock()),
                    "source": "Fixed testnet reference only; tPIV has no cash value"}
        def load():
            data = decode_json(fetch(self.config.price_url))
            try:
                rate = Decimal(str(data["pivx"]["usd"]))
                updated = data["pivx"]["last_updated_at"]
                if not rate.is_finite() or rate <= 0 or type(updated) is not int or not -60 <= self.clock() - updated <= 300:
                    raise ValueError("Stale/invalid price")
            except (KeyError, TypeError, ValueError, InvalidOperation):
                raise ServiceError(503, "price_unavailable", "A fresh PIV/USD price is unavailable") from None
            return {"piv_usd": str(rate), "updated_at": updated, "source": "CoinGecko"}
        return self.cached("price", 30, load)

    def analytics(self):
        def load():
            chain = self.analytics_chain.call("getblockchaininfo")
            network = self.analytics_chain.call("getnetworkinfo")
            proposals = self.analytics_chain.call("getbudgetinfo")
            next_superblock = self.analytics_chain.call("getnextsuperblock")
            masternodes = self.analytics_chain.call("getmasternodecount")
            expected_network = "main" if self.config.analytics_rpc_url else "test"
            require(isinstance(chain, dict) and chain.get("chain") == expected_network and type(chain.get("blocks")) is int
                    and isinstance(network, dict) and isinstance(proposals, list) and type(next_superblock) is int
                    and isinstance(masternodes, dict), 503, "analytics_invalid", "Node analytics schema is incomplete")
            require_synced(chain)
            # Published proposal payee addresses are intentionally omitted from this product schema.
            fields = ["Name", "URL", "Hash", "BlockStart", "BlockEnd", "MonthlyPayment", "RemainingPaymentCount", "Yeas", "Nays", "Abstains", "IsValid"]
            return {"schema_version": "1.0", "generated_at": int(self.clock()),
                    "network": {k: chain[k] for k in ("chain", "blocks", "headers", "bestblockhash", "difficulty") if k in chain},
                    "connections": network.get("connections"), "masternodes": masternodes,
                    "next_superblock_height": next_superblock,
                    "blocks_until_superblock": max(0, next_superblock - chain["blocks"]),
                    "proposals": [{k: p[k] for k in fields if k in p} for p in proposals if isinstance(p, dict)],
                    "sources": [{"name": "PIVX Core RPC", "methods": ["getblockchaininfo", "getnetworkinfo", "getbudgetinfo", "getnextsuperblock", "getmasternodecount"]}]}
        return self.cached("analytics:" + self.config.analytics_rpc_url, 30, load)

    def digest(self):
        def load():
            items, coverage = [], []
            now = int(self.clock())
            for source in self.config.feeds:
                try:
                    parsed = parse_feed(fetch(source["url"]), source, now, self.config.digest_window_days)
                    items.extend(parsed)
                    coverage.append({"name": source["name"], "url": source["url"], "fetched_at": now, "status": "ok"})
                except ServiceError:
                    coverage.append({"name": source["name"], "url": source["url"], "status": "unavailable"})
            require(any(c["status"] == "ok" for c in coverage), 503, "digest_unavailable", "No research sources are available")
            unique = {item["url"]: item for item in items}
            return {"schema_version": "1.0", "generated_at": now,
                    "window": {"from": now - self.config.digest_window_days * 86400, "to": now},
                    "method": "Sourced release and news excerpts; no generated factual claims",
                    "partial_coverage": any(c["status"] != "ok" for c in coverage),
                    "coverage": coverage, "items": sorted(unique.values(), key=lambda i: i["published_at"], reverse=True)[:100]}
        return self.cached("digest", min(900, self.config.digest_max_age), load)

    def service(self, name, params):
        require(isinstance(params, dict) and set(params) <= {"topic"}, 400, "invalid_parameters", "Only the optional digest topic filter is supported")
        if name == "analytics":
            require(not params, 400, "invalid_parameters", "Analytics accepts no parameters")
            return self.analytics()
        if name == "digest":
            result = self.digest()
            if params.get("topic"):
                require(params["topic"] in {"PIVX", "XMR", "ZEC", "agent-payments"}, 400, "invalid_topic", "Unknown digest topic")
                result = {**result, "items": [item for item in result["items"] if item["topic"] == params["topic"]]}
            return result
        raise ServiceError(404, "unknown_service", "Unknown service")
