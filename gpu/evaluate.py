#!/usr/bin/env python
"""
Score the FINE-TUNED model on the test split.

Produces exactly the same files as baseline.py, so the two can be compared
directly and the website can put them side by side.

    python evaluate.py --dataset /workspace/data/<id> \
                       --checkpoint /workspace/results/<run>/best.nemo \
                       --out /workspace/results/<run>/finetuned
"""

import argparse
import os
import sys

import common


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the fine-tuned model.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--checkpoint", required=True, help="the .nemo file written by finetune.py")
    parser.add_argument("--out", required=True)
    parser.add_argument("--splits", nargs="+", default=["test"])
    parser.add_argument("--decoders", nargs="+", default=["ctc", "rnnt"], choices=["ctc", "rnnt"])
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not os.path.isfile(args.checkpoint):
        sys.exit(f"checkpoint not found: {args.checkpoint}")

    common.log(f"machine: {common.describe_machine()}")
    common.evaluate_model(
        dataset_dir=args.dataset,
        splits=args.splits,
        out_dir=args.out,
        label="finetuned",
        checkpoint=args.checkpoint,
        decoders=tuple(args.decoders),
        batch_size=args.batch_size,
        limit=args.limit,
    )
    common.log("evaluation done")


if __name__ == "__main__":
    main()
