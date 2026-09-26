#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eventguard.hardware_analysis import analyze

parser = argparse.ArgumentParser()
parser.add_argument("--stage", choices=("smoke", "stage1", "full"), default="stage1")
args = parser.parse_args()
print(json.dumps(analyze(args.stage), indent=2))
