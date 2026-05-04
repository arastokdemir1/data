#!/usr/bin/env python3
"""
Run every available CarLog data updater in the correct order.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="Data repository root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    scripts_dir = Path(__file__).resolve().parent

    run([sys.executable, str(scripts_dir / "update_fuel_prices_opet.py"), "--output-dir", str(root)])
    run([sys.executable, str(scripts_dir / "update_pricing_manifest.py"), "--root", str(root)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
