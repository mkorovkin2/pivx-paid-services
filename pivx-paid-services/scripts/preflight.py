"""Read-only environment probe; never creates a wallet or sends funds."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import socket
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args()
    checks = {"python": sys.version.split()[0], "python_311_or_newer": sys.version_info >= (3, 11),
              "pivxd": shutil.which("pivxd"), "pivx_cli": shutil.which("pivx-cli")}
    for host in ("github.com", "computingcache.com"):
        try:
            checks["dns:" + host] = {"available": bool(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))}
        except OSError as error:
            checks["dns:" + host] = {"available": False, "error": type(error).__name__}
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 0))
            checks["loopback_bind"] = {"available": True}
        except OSError as error:
            checks["loopback_bind"] = {"available": False, "error": type(error).__name__}
    output = {"checked_at": datetime.now(timezone.utc).isoformat(), "checks": checks,
              "transactions_sent": 0, "wallets_created": 0, "spent_usd": 0,
              "notice": "DNS/binary/bind probes only; successful probes do not establish chain or faucet availability."}
    encoded = json.dumps(output, indent=2) + "\n"
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
