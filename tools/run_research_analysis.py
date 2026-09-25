#!/usr/bin/env python3
"""Generate frozen-v1 research analysis from the locked evaluation dataset."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eventguard.research_analysis import run_all_analyses

if __name__ == "__main__":
    print(json.dumps(run_all_analyses(), indent=2))
