#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eventguard.hardware_validation import run


def main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen-v1 E220 validation. Stage 1 requires recovered frozen firmware and never flashes it.")
    parser.add_argument("stage", choices=("smoke", "stage1", "full"))
    parser.add_argument("--skip-flash", action="store_true", help="Do not build or flash. Required for Stage 1; it reads artifacts/final_stage1_firmware/.")
    parser.add_argument("--output-dir", type=Path, help="Write a separate versioned hardware result tree.")
    args = parser.parse_args()
    print(json.dumps(run(args.stage, args.skip_flash, args.output_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
