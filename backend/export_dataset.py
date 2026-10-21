"""
Export the collected lectures as a training dataset for IndicConformer (Part 2).

Produces a folder that can be copied to a GPU machine as it is:

    data/datasets/<name>/
        train.jsonl  val.jsonl  test.jsonl   NeMo manifests
        audio/<clip>.wav                     the clips themselves
        dataset_info.json                    what is in each split, and why

Two rules keep the result honest:

  * Splitting is BY LECTURE, never by clip. If clips from one lecture appeared in
    both training and testing, the model would be tested on a voice and a topic it
    had already studied, and the score would look far better than the truth.
  * Reference text goes through the same clean-up as the model's output later
    (see normalise.py), so scoring compares like with like.

Run on the VPS:
    docker compose exec backend python export_dataset.py
    docker compose exec backend python export_dataset.py --pin-test <job_id> --dry-run
"""

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone

import db
from normalise import has_latin, load_english_map, prepare_reference

# Clips outside this range are dropped: very short ones carry almost no speech,
# very long ones blow up GPU memory during training.
MIN_SECONDS = 1.0
MAX_SECONDS = 25.0

MAP_PATH = os.environ.get("ENGLISH_MAP", os.path.join(db.DATA_DIR, "english_map.json"))


def human(seconds: float) -> str:
    return f"{seconds / 3600:.2f} h"


def load_segments(folder: str) -> tuple[list[dict], bool]:
    """A job's clips. Prefers a human-corrected transcript when one exists."""
    corrected = os.path.join(folder, "transcript_corrected.json")
    if os.path.isfile(corrected):
        with open(corrected, encoding="utf-8") as f:
            return json.load(f), True
    path = os.path.join(folder, "segments.json")
    if not os.path.isfile(path):
        return [], False
    with open(path, encoding="utf-8") as f:
        return json.load(f), False


def assign_splits(jobs: list[dict], ratios: dict[str, float],
                  pinned_test: set[str]) -> dict[str, list[dict]]:
    """Put whole lectures into train/val/test, aiming at the requested share of audio."""
    splits: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
    rest = []
    for job in jobs:
        (splits["test"] if job["id"] in pinned_test else rest).append(job)

    total = sum(j["duration"] or 0 for j in jobs) or 1.0
    # Longest lecture first, each time into the split that is furthest below its target.
    for job in sorted(rest, key=lambda j: -(j["duration"] or 0)):
        have = {name: sum(x["duration"] or 0 for x in group) for name, group in splits.items()}
        target_gap = {name: ratios[name] * total - have[name] for name in splits}
        splits[max(target_gap, key=target_gap.get)].append(job)
    return splits


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the lectures as a NeMo training dataset.")
    parser.add_argument("--train", type=float, default=0.8)
    parser.add_argument("--val", type=float, default=0.1)
    parser.add_argument("--test", type=float, default=0.1)
    parser.add_argument("--pin-test", nargs="*", default=[], metavar="JOB_ID",
                        help="lectures that must go in the test split")
    parser.add_argument("--exclude", nargs="*", default=[], metavar="JOB_ID",
                        help="lectures to leave out entirely")
    parser.add_argument("--name", default=None, help="dataset folder name (default: date and time)")
    parser.add_argument("--copy", action="store_true",
                        help="copy the clips instead of hard-linking them (uses more disk)")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args()

    ratios = {"train": args.train, "val": args.val, "test": args.test}
    if abs(sum(ratios.values()) - 1.0) > 0.001:
        parser.error(f"--train/--val/--test must add up to 1.0 (got {sum(ratios.values())})")

    finished = [j for j in db.list_jobs()
                if j["status"] == "done" and j["id"] not in set(args.exclude)]
    # Only lectures explicitly marked as training data are ever included. A lecture
    # transcribed just to read it must not end up silently in the training set.
    jobs = [j for j in finished if j["purpose"] == "training"]
    skipped = [j for j in finished if j["purpose"] != "training"]
    if skipped:
        print(f"skipping {len(skipped)} transcribe-only job(s): "
              + ", ".join(j["id"] for j in skipped[:5])
              + (" ..." if len(skipped) > 5 else ""))
        print("  (use the 'Use for training' button on the website to include one)")
    if not jobs:
        print("No finished lectures marked as training data to export.", file=sys.stderr)
        sys.exit(1)

    mapping = {}
    if os.path.isfile(MAP_PATH):
        mapping = load_english_map(MAP_PATH)
    print(f"English spelling table: {len(mapping)} words"
          f"{'' if mapping else '  (none yet - run build_english_map.py)'}")

    splits = assign_splits(jobs, ratios, set(args.pin_test))
    name = args.name or datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    out_dir = os.path.join(db.DATA_DIR, "datasets", name)
    audio_dir = os.path.join(out_dir, "audio")
    if not args.dry_run:
        os.makedirs(audio_dir, exist_ok=True)

    dropped = Counter()
    unknown_english = Counter()
    any_corrected = False
    info_splits: dict[str, dict] = {}

    for split, split_jobs in splits.items():
        lines, seconds, clips = [], 0.0, 0
        job_entries = []
        for job in split_jobs:
            folder = os.path.join(db.DATA_DIR, job["id"])
            segments, corrected = load_segments(folder)
            any_corrected = any_corrected or corrected
            kept_here = 0
            for segment in segments:
                duration = float(segment.get("duration") or
                                 (segment.get("end", 0) - segment.get("start", 0)))
                text, unknown = prepare_reference(segment.get("text") or "", mapping)
                unknown_english.update(unknown)

                if not text:
                    dropped["empty text"] += 1
                    continue
                if duration < MIN_SECONDS:
                    dropped[f"shorter than {MIN_SECONDS:g} s"] += 1
                    continue
                if duration > MAX_SECONDS:
                    dropped[f"longer than {MAX_SECONDS:g} s"] += 1
                    continue

                source = os.path.join(folder, segment["file"])
                if not os.path.isfile(source):
                    dropped["clip file missing"] += 1
                    continue
                clip_name = os.path.basename(segment["file"])
                if not args.dry_run:
                    target = os.path.join(audio_dir, clip_name)
                    if not os.path.exists(target):
                        if args.copy:
                            shutil.copy2(source, target)
                        else:
                            try:
                                os.link(source, target)      # same disk: costs no space
                            except OSError:
                                shutil.copy2(source, target)

                lines.append(json.dumps(
                    {"audio_filepath": f"audio/{clip_name}",
                     "duration": round(duration, 3),
                     "text": text},
                    ensure_ascii=False))
                seconds += duration
                clips += 1
                kept_here += 1

            job_entries.append({"job_id": job["id"], "title": job["title"],
                                "clips": kept_here, "seconds": round(
                                    sum(float(s.get("duration") or 0) for s in segments), 1),
                                "human_corrected": corrected})

        if not args.dry_run:
            with open(os.path.join(out_dir, f"{split}.jsonl"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + ("\n" if lines else ""))

        info_splits[split] = {"lectures": len(split_jobs), "clips": clips,
                              "hours": round(seconds / 3600, 3), "jobs": job_entries}

    usable = all(info_splits[s]["clips"] > 0 for s in ("train", "val", "test"))
    info = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": name,
        "splits": info_splits,
        "total_hours": round(sum(i["hours"] for i in info_splits.values()), 3),
        "total_clips": sum(i["clips"] for i in info_splits.values()),
        "requested_ratios": ratios,
        "pinned_test": args.pin_test,
        "excluded": args.exclude,
        "skipped_transcribe_only": [j["id"] for j in skipped],
        "dropped_segments": dict(dropped),
        "clip_limits_seconds": [MIN_SECONDS, MAX_SECONDS],
        "english_map_words": len(mapping),
        "unknown_english_words": dict(unknown_english.most_common()),
        "human_verified_test_references": bool(
            info_splits["test"]["jobs"] and all(j["human_corrected"] for j in info_splits["test"]["jobs"])),
        "usable_for_training": usable,
        "normalisation": "normalise.py: NFC, chillu folded to atomic, invisible characters "
                         "and punctuation removed, whitespace collapsed, Latin lowercased, "
                         "English transliterated from the frozen table",
    }
    if not args.dry_run:
        with open(os.path.join(out_dir, "dataset_info.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        # The spelling table travels with the dataset, so the exact text used for
        # training can always be reproduced later.
        if os.path.isfile(MAP_PATH):
            shutil.copy2(MAP_PATH, os.path.join(out_dir, "english_map.json"))

    # ---- report
    print()
    print(f"dataset {name}{'  (DRY RUN - nothing written)' if args.dry_run else ''}")
    for split in ("train", "val", "test"):
        i = info_splits[split]
        print(f"  {split:<6} {i['lectures']:>2} lectures  {i['clips']:>4} clips  {human(i['hours']*3600)}")
        for j in i["jobs"]:
            flag = "  [human-corrected]" if j["human_corrected"] else ""
            print(f"           {j['job_id']}  {(j['title'] or '')[:44]}{flag}")
    print(f"  total  {info['total_clips']} clips, {human(info['total_hours']*3600)}")
    if dropped:
        print("  dropped: " + ", ".join(f"{n} {reason}" for reason, n in dropped.most_common()))
    if unknown_english:
        top = ", ".join(f"{w} ({n})" for w, n in unknown_english.most_common(12))
        print(f"  English words with no Malayalam spelling yet: {len(unknown_english)} -> {top}")
        print("  (these were left in English; run build_english_map.py, then export again)")
    print(f"  test references human-verified: {info['human_verified_test_references']}")

    if not usable:
        print()
        print("  *** NOT USABLE FOR TRAINING YET ***")
        empty = [s for s in ("train", "val", "test") if info_splits[s]["clips"] == 0]
        print(f"  These splits are empty: {', '.join(empty)}.")
        print(f"  Splitting is by whole lecture, so at least 3 lectures are needed "
              f"(you have {len(jobs)}). Collect more lectures, then export again.")
    if not args.dry_run:
        print(f"\n  written to {out_dir}")


if __name__ == "__main__":
    main()
