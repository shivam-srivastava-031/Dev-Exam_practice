"""Download the pre-built question bank and pre-trained models from the GitHub Release.

The deployed server does this itself at cold start (app/artifacts.py); this script
runs the same download for a local rehearsal of the deployment:

    python deploy/fetch_artifacts.py                 # into $TMPDIR/ssc-data, as on Vercel
    python deploy/fetch_artifacts.py --to some/dir
    USE_BUNDLE=1 python -m uvicorn app.main:app      # then run exactly as deployed
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import artifacts, config  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--to", type=Path, default=config.BUNDLE_DIR)
    args = parser.parse_args()
    artifacts.fetch(args.to)
