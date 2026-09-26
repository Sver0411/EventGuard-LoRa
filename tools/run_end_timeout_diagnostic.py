#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eventguard.hardware_diagnostic import run_end_timeout_diagnostic


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one engineering-only reproduction of the Stage 1 END timeout condition.")
    parser.add_argument("--output-dir", type=Path,
                        help="Use a new empty diagnostic output directory; existing attempts are never overwritten.")
    args = parser.parse_args()
    result = run_end_timeout_diagnostic(args.output_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
