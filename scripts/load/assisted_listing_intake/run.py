#!/usr/bin/env python3
"""Run the assisted intake capacity measurement and emit JSON evidence."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.load.assisted_listing_intake.runtime import run_capacity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--volume", type=int, default=240)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.volume <= 1000:
        parser.error("--volume must be between 1 and the approved batch maximum of 1000")
    if not 1 <= args.concurrency <= 100:
        parser.error("--concurrency must be between 1 and the retrieval maximum of 100")

    output = args.output or Path("docs/evidence/completion/ODP-INTAKE-LOAD-001/load-report.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="intake-load-") as directory:
        report = run_capacity(Path(directory) / "runtime.sqlite3", volume=args.volume, concurrency=args.concurrency)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
