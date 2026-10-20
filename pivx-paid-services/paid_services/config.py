from dataclasses import dataclass, field
from pathlib import Path
import os
import secrets
import tomllib
from urllib.parse import urlsplit


DEFAULT_FEEDS = [
    {"name": "PIVX Core", "url": "https://github.com/PIVX-Project/PIVX/releases.atom", "topic": "PIVX"},
    {"name": "PIVX Agent Kit", "url": "https://github.com/PIVX-Labs/pivx-agent-kit/releases.atom", "topic": "agent-payments"},
    {"name": "Monero", "url": "https://github.com/monero-project/monero/releases.atom", "topic": "XMR"},
    {"name": "Zcash", "url": "https://github.com/zcash/zcash/releases.atom", "topic": "ZEC"},
]


def safe_url(url):
    parts = urlsplit(url)
    if parts.username or parts.password or parts.fragment:
        raise ValueError("URLs must not contain credentials or fragments")
    if parts.scheme != "https" and not (
        parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("Use HTTPS, or HTTP on loopback")
    return url.rstrip("/")


@dataclass
class Config:
    data_dir: Path
    address: str
    rpc_url: str
    wallet_path: Path = Path("wallet.json")
    wallet_fingerprint: str = ""
    network: str = "testnet6"
    receiver: str = "core"
    rpc_mode: str = "core"
    price_mode: str = "fixed_testnet"
    testnet_reference_rate: str = "1"
    analytics_rpc_url: str = ""
    bind: str = "127.0.0.1"
    port: int = 8787
    public_url: str = "http://127.0.0.1:8787"
    poll_seconds: int = 10
    max_sync_age: int = 180
    max_sync_lag: int = 2
    quote_seconds: int = 600
    token_seconds: int = 900
    single_access_seconds: int = 86400
    digest_max_age: int = 86400
    digest_window_days: int = 7
    price_url: str = "https://api.coingecko.com/api/v3/simple/price?ids=pivx&vs_currencies=usd&include_last_updated_at=true"
    analytics_usd: str = "0.05"
    digest_usd: str = "0.25"
    week_usd: str = "2.00"
    feeds: list = field(default_factory=lambda: list(DEFAULT_FEEDS))
    demo: bool = False

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        raw = tomllib.loads(path.read_text())
        if "demo" in raw:
            raise ValueError("Demo mode is only available through the explicit demo command")
        for key in ("data_dir", "wallet_path"):
            if key in raw:
                raw[key] = (path.parent / raw[key]).resolve()
        config = cls(**raw)
        config.validate()
        return config

    def validate(self):
        from .crypto import shield_address_valid
        if self.network != "testnet6":
            raise ValueError("Phase 1 supports testnet6 only")
        if not shield_address_valid(self.address):
            raise ValueError("address must be a checksummed testnet Sapling ptestsapling1 address")
        if self.receiver not in {"core", "snapshot"}:
            raise ValueError("receiver must be core or snapshot")
        if self.receiver == "snapshot" and not self.demo:
            raise ValueError("Snapshot receiver is simulation-only until Wallet Kit testnet support is proven")
        if self.receiver == "snapshot" and (len(self.wallet_fingerprint) != 64 or any(c not in "0123456789abcdef" for c in self.wallet_fingerprint)):
            raise ValueError("wallet_fingerprint must be SHA256 of the receiving wallet extfvk")
        if self.rpc_mode not in {"core", "gateway"}:
            raise ValueError("rpc_mode must be core or gateway")
        if self.receiver == "core" and (self.rpc_mode != "core" or urlsplit(self.rpc_url).hostname not in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("Core receiver requires local Core JSON-RPC, never a public gateway")
        if self.price_mode not in {"fixed_testnet", "coingecko_reference"}:
            raise ValueError("Invalid price_mode")
        for url in [self.rpc_url, self.public_url, self.price_url, *(f["url"] for f in self.feeds)]:
            safe_url(url)
        if self.analytics_rpc_url:
            safe_url(self.analytics_rpc_url)
            if urlsplit(self.analytics_rpc_url).scheme != "https":
                raise ValueError("Public analytics source must use HTTPS")
        if not 1 <= self.poll_seconds <= 30 or self.max_sync_age < 30 or self.max_sync_lag < 0:
            raise ValueError("Invalid sync configuration")
        if not 60 <= self.quote_seconds <= 600 or not 60 <= self.token_seconds <= 3600:
            raise ValueError("Invalid quote/token lifetime")
        if self.demo and self.bind not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Simulation must bind to loopback")
        from decimal import Decimal
        rate = Decimal(self.testnet_reference_rate)
        if not rate.is_finite() or not Decimal("0.000001") <= rate <= Decimal("1000000"):
            raise ValueError("testnet_reference_rate must be a finite positive reference")
        for price in (self.analytics_usd, self.digest_usd, self.week_usd):
            value = Decimal(price)
            if not value.is_finite() or not Decimal("0") < value <= Decimal("1000"):
                raise ValueError("Service prices must be positive USD decimals <= 1000")

    def signing_key(self):
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        path = self.data_dir / "signing.key"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.stat().st_mode & 0o077:
                raise ValueError("signing.key must have owner-only permissions")
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(secrets.token_bytes(32))
        key = path.read_bytes()
        if len(key) != 32:
            raise ValueError("signing.key must be exactly 32 bytes")
        return key
