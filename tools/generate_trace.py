#!/usr/bin/env python3
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eventguard.trace import generate_trace, trace_fingerprint

parser = argparse.ArgumentParser()
parser.add_argument("--seed", type=int, default=11)
parser.add_argument("--samples-per-phase", type=int, default=6)
parser.add_argument("--out", type=Path, default=ROOT / "results" / "trace.csv")
args = parser.parse_args()
rows = generate_trace(args.seed, args.samples_per_phase)
args.out.parent.mkdir(parents=True, exist_ok=True)
with args.out.open("w", newline="", encoding="utf-8") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0].as_dict()))
    writer.writeheader()
    writer.writerows(row.as_dict() for row in rows)
print(json.dumps({"rows": len(rows), "seed": args.seed, "sha256": trace_fingerprint(rows), "path": str(args.out)}, indent=2))
