#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eventguard.host import run_hardware, run_simulation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the EventGuard-LoRa reproducible experiment matrix.")
    parser.add_argument("--simulate", action="store_true", help="Run host reference simulation only; no ESP32/E220 is used.")
    parser.add_argument("--dry-run", action="store_true", help="Discover and identify boards without building or flashing.")
    parser.add_argument("--skip-build", action="store_true", help="Use the firmware already flashed on both boards.")
    parser.add_argument("--seeds", type=int, nargs="+", help="Override the configured seed list.")
    parser.add_argument("--samples-per-phase", type=int, help="Override trace density (default from config).")
    parser.add_argument("--strategies", nargs="+", choices=["NO_PROTECTION", "FIXED_REDUNDANCY", "EVENTGUARD"])
    parser.add_argument("--loss-rates", type=float, nargs="+", help="Override loss percentages as fractions, e.g. 0 .1 .3.")
    parser.add_argument("--loss-models", nargs="+", choices=["RANDOM", "BURST"])
    parser.add_argument("--burst-length", type=int, choices=[2, 3, 5], help="Deterministic burst-loss run length.")
    args = parser.parse_args()
    try:
        runner = run_simulation if args.simulate else run_hardware
        kwargs = {"strategies": args.strategies, "loss_rates": args.loss_rates, "models": args.loss_models,
                  "seeds": args.seeds, "samples_per_phase": args.samples_per_phase}
        kwargs["burst_length"] = args.burst_length
        if not args.simulate:
            kwargs.update(skip_build=args.skip_build, dry_run=args.dry_run)
        runs = runner(**kwargs)
        if args.dry_run:
            print("Discovery completed. No firmware was built or flashed.")
        else:
            print(f"Completed {len(runs)} runs. Results: {ROOT / 'results'}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
