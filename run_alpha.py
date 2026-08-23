from __future__ import annotations

import alpha_pipeline
import db as dbm
from utils import load_config
import argparse


def reference_batch_size(value):
    parsed = int(value)
    if not 1 <= parsed <= 30:
        raise argparse.ArgumentTypeError("--reference-batch-size must be between 1 and 30")
    return parsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--reference-batch-size", type=reference_batch_size)
    args = parser.parse_args()
    config = load_config()
    if args.reference_batch_size is not None:
        config.setdefault("alpha", {})["reference_refresh_batch_size"] = args.reference_batch_size
    conn = dbm.connect(config.get("db_path", "crypto_dashboard.db"))
    try:
        result=alpha_pipeline.refresh_alpha(conn, config, force=args.force, entrypoint="run_alpha.py")
        print(f"alpha status={result['status']} run_id={result.get('run_id')} reason={result.get('reason')} counts={result.get('counts')}")
        if result["status"] == "failed": raise RuntimeError(result.get("reason", "alpha run failed"))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
