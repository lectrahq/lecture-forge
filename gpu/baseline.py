#!/usr/bin/env python
"""
Baseline: how well does the PRETRAINED IndicConformer already do?

It transcribes the same clips the fine-tuned model will be judged on, using both
decoder heads (CTC and RNNT), and writes the scores. Nothing is trained here.

    python baseline.py --dataset /workspace/data/<id> --out /workspace/results/<run>/baseline

For the dry run on a machine without a GPU, use --limit to do only a few clips:
    python baseline.py --dataset ... --out ... --limit 5 --decoders ctc
"""

import argparse

import common


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the pretrained model.")
    parser.add_argument("--dataset", required=True, help="dataset folder (holds train/val/test.jsonl)")
    parser.add_argument("--out", required=True, help="where to write predictions.json and metrics.json")
    parser.add_argument("--splits", nargs="+", default=["val", "test"])
    parser.add_argument("--decoders", nargs="+", default=["ctc", "rnnt"], choices=["ctc", "rnnt"])
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None,
                        help="only the first N clips of each split (for a quick dry run)")
    args = parser.parse_args()

    common.log(f"machine: {common.describe_machine()}")
    common.evaluate_model(
        dataset_dir=args.dataset,
        splits=args.splits,
        out_dir=args.out,
        label="baseline",
        checkpoint=None,              # the pretrained checkpoint
        decoders=tuple(args.decoders),
        batch_size=args.batch_size,
        limit=args.limit,
    )
    common.log("baseline done")


if __name__ == "__main__":
    main()
