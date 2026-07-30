#!/usr/bin/env python3
"""
feedback_packet.py — Print a copy-paste-ready debug packet for one run_id.

When a run misbehaves and we can't resolve it, run this and paste the output
back. It contains the full resolved plan + seed (Layer 1), so the exact run is
reproducible on the other side.

    python3 scripts/feedback_packet.py <run_id>
    python3 scripts/feedback_packet.py <run_id> --log mylogs

If the problem happened ON THE PHONE (not in the decision), also attach the
Geelark execution log + failing-step screenshot for the same run_id.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from run_logger import make_feedback_packet


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--log", default="logs", help="log directory (default: logs)")
    args = ap.parse_args()
    print(make_feedback_packet(args.run_id, log_dir=args.log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
