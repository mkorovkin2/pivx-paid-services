#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PYTHON_BIN=${PYTHON_BIN:-python3}
NODE_BIN=${NODE_BIN:-node}
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required; set PYTHON_BIN"'
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -W error::ResourceWarning -m unittest discover -t . -s tests -v
"$NODE_BIN" --check paid_services/static/app.js
"$NODE_BIN" --test scripts/test-ui.cjs
"$PYTHON_BIN" - <<'PY'
import subprocess
import sys
import tempfile
with tempfile.TemporaryDirectory(prefix="v511-verify-") as directory:
    subprocess.run([sys.executable, "-m", "paid_services", "demo", "--headless", "--data-dir", directory], check=True)
PY
