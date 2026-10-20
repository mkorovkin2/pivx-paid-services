"""Launch the dedicated Core testnet wallet MCP/CLI from any working directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pivx-paid-services"))
from paid_services.testnet_wallet import main

if __name__ == "__main__":
    main()
