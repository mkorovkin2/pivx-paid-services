"""Checkout launcher: share the tested implementation with the service backend."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pivx-paid-services"))
from paid_services.mcp import main

if __name__ == "__main__":
    main()
