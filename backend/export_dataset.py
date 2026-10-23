"""
Export the collected lectures as a training dataset for IndicConformer (Part 2).

Produces a folder that can be copied to a GPU machine as it is:

    data/datasets/<name>/
        train.jsonl  val.jsonl  test.jsonl   NeMo manifests
        audio/<clip>.wav                     the clips themselves
        dataset_info.json                    what is in each split, and why
        english_map.json                     frozen transliteration table

Two rules keep the result honest:

  * Splitting is BY LECTURE, never by clip. If clips from one lecture appeared in
    both training and testing, the model would be tested on a voice and a topic it
    had already studied, and the score would look far better than the truth.
  * Reference text goes through the same clean-up as the model's output later
    (see normalise.py), so scoring compares like with like.

Can be run from the CLI or invoked via FastAPI routes:
    docker compose exec backend python export_dataset.py
    docker compose exec backend python export_dataset.py --pin-test <job_id> --dry-run
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tarfile
from collections import Counter
from datetime import datetime, timezone

import db
from normalise import has_latin, load_english_map, prepare_reference

log = logging.getLogger("dataset_export")

# Clips outside this range are dropped: very short ones carry almost no speech,
# very long ones blow up GPU memory during training.
MIN_SECONDS = 1.0
MAX_SECONDS = 25.0

MAP_PATH = os.environ.get("ENGLISH_MAP", os.path.join(db.DATA_DIR, "english_map.json"))
DATASETS_DIR = os.path.join(db.DATA_DIR, "datasets")
TARGET_TRAINING_HOURS = 10.0


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


def sanitize_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "_", (name or "").strip())
    return cleaned[:60].strip("._") or datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")


def get_dataset_summary() -> dict:
    """Statistics on collected training lectures and previous exports for the UI dashboard."""
    finished = [j for j in db.list_jobs(purpose="training") if j["status"] == "done"]
    total_seconds = sum(j["duration"] or 0 for j in finished)
    total_clips = sum(j["segment_count"] or 0 for j in finished)
    total_hours = round(total_seconds / 3600, 2)
    percent = min(100.0, round((total_hours / TARGET_TRAINING_HOURS) * 100, 1))

    mapping = load_english_map(MAP_PATH) if os.path.isfile(MAP_PATH) else {}
    datasets = list_datasets()

    return {
        "training_jobs_count": len(finished),
        "total_seconds": round(total_seconds, 1),
        "total_hours": total_hours,
        "total_clips": total_clips,
        "target_hours": TARGET_TRAINING_HOURS,
        "target_percent": percent,
        "ready_to_export": len(finished) >= 3,
        "english_map_words": len(mapping),
        "training_jobs": [
            {
                "id": j["id"],
                "title": j["title"] or j["id"],
                "duration": j["duration"],
                "clips": j["segment_count"],
                "created_at": j["created_at"],
            }
            for j in finished
        ],
        "datasets": datasets,
    }


def list_datasets() -> list[dict]:
    """Scan data/datasets/ for created datasets."""
    if not os.path.isdir(DATASETS_DIR):
        return []
    items = []
    for entry in sorted(os.listdir(DATASETS_DIR), reverse=True):
        entry_path = os.path.join(DATASETS_DIR, entry)
        if not os.path.isdir(entry_path):
            continue
        info_path = os.path.join(entry_path, "dataset_info.json")
        if not os.path.isfile(info_path):
            continue
        try:
            with open(info_path, encoding="utf-8") as f:
                info = json.load(f)
            archive_path = os.path.join(DATASETS_DIR, f"{entry}.tar.gz")
            items.append({
                "name": entry,
                "created": info.get("created"),
                "total_hours": info.get("total_hours", 0),
                "total_clips": info.get("total_clips", 0),
                "usable": info.get("usable_for_training", False),
                "splits": {
                    s: {
                        "lectures": info.get("splits", {}).get(s, {}).get("lectures", 0),
                        "clips": info.get("splits", {}).get(s, {}).get("clips", 0),
                        "hours": info.get("splits", {}).get(s, {}).get("hours", 0),
                    }
                    for s in ("train", "val", "test")
                },
                "has_archive": os.path.isfile(archive_path),
                "archive_size_bytes": os.path.getsize(archive_path) if os.path.isfile(archive_path) else 0,
            })
        except Exception as e:
            log.warning("could not read dataset %s: %s", entry, e)
    return items


def create_dataset_archive(name: str) -> str:
    """Create a .tar.gz archive of an exported dataset for direct download and GPU pod copying."""
    dataset_folder = os.path.join(DATASETS_DIR, name)
    if not os.path.isdir(dataset_folder):
        raise FileNotFoundError(f"Dataset folder '{name}' not found.")
    archive_path = os.path.join(DATASETS_DIR, f"{name}.tar.gz")
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(dataset_folder, arcname=name)
    return archive_path


def export_dataset(ratios: dict[str, float] | None = None,
                   name: str | None = None,
                   pin_test: list[str] | set[str] | None = None,
                   exclude: list[str] | set[str] | None = None,
                   copy: bool = False,
                   dry_run: bool = False,
                   make_archive: bool = True) -> dict:
    """Core export function. Produces manifests, audio folder, info and tarball."""
    ratios = ratios or {"train": 0.8, "val": 0.1, "test": 0.1}
    pin_test = set(pin_test or [])
    exclude = set(exclude or [])

    if abs(sum(ratios.values()) - 1.0) > 0.001:
        raise ValueError(f"Split ratios must sum to 1.0 (got {sum(ratios.values())})")

    finished = [j for j in db.list_jobs()
                if j["status"] == "done" and j["id"] not in exclude]
    jobs = [j for j in finished if j["purpose"] == "training"]
    skipped = [j for j in finished if j["purpose"] != "training"]

    if not jobs:
        raise ValueError("No finished lectures marked as training data are available to export.")

    mapping = load_english_map(MAP_PATH) if os.path.isfile(MAP_PATH) else {}
    splits = assign_splits(jobs, ratios, pin_test)
    name = sanitize_name(name)
    out_dir = os.path.join(DATASETS_DIR, name)
    audio_dir = os.path.join(out_dir, "audio")

    if not dry_run:
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

                source = os.path.join(folder, segment.get("file", ""))
                if not os.path.isfile(source):
                    dropped["clip file missing"] += 1
                    continue
                clip_name = os.path.basename(segment["file"])
                if not dry_run:
                    target = os.path.join(audio_dir, clip_name)
                    if not os.path.exists(target):
                        if copy:
                            shutil.copy2(source, target)
                        else:
                            try:
                                os.link(source, target)
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

            job_entries.append({
                "job_id": job["id"],
                "title": job["title"],
                "clips": kept_here,
                "seconds": round(sum(float(s.get("duration") or 0) for s in segments), 1),
                "human_corrected": corrected,
            })

        if not dry_run:
            with open(os.path.join(out_dir, f"{split}.jsonl"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + ("\n" if lines else ""))

        info_splits[split] = {
            "lectures": len(split_jobs),
            "clips": clips,
            "hours": round(seconds / 3600, 3),
            "jobs": job_entries,
        }

    usable = all(info_splits[s]["clips"] > 0 for s in ("train", "val", "test"))
    info = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": name,
        "splits": info_splits,
        "total_hours": round(sum(i["hours"] for i in info_splits.values()), 3),
        "total_clips": sum(i["clips"] for i in info_splits.values()),
        "requested_ratios": ratios,
        "pinned_test": list(pin_test),
        "excluded": list(exclude),
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

    if not dry_run:
        with open(os.path.join(out_dir, "dataset_info.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        if os.path.isfile(MAP_PATH):
            shutil.copy2(MAP_PATH, os.path.join(out_dir, "english_map.json"))

        if make_archive:
            try:
                archive_path = create_dataset_archive(name)
                info["archive_path"] = archive_path
                info["archive_size_bytes"] = os.path.getsize(archive_path)
            except Exception as e:
                log.warning("failed creating dataset tarball for %s: %s", name, e)

    return info


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
    skipped = [j for j in finished if j["purpose"] != "training"]
    if skipped:
        print(f"skipping {len(skipped)} transcribe-only job(s): "
              + ", ".join(j["id"] for j in skipped[:5])
              + (" ..." if len(skipped) > 5 else ""))
        print("  (use the 'Use for training' button on the website to include one)")

    training_jobs = [j for j in finished if j["purpose"] == "training"]
    if not training_jobs:
        print("No finished lectures marked as training data to export.", file=sys.stderr)
        sys.exit(1)

    mapping = load_english_map(MAP_PATH) if os.path.isfile(MAP_PATH) else {}
    print(f"English spelling table: {len(mapping)} words"
          f"{'' if mapping else '  (none yet - run build_english_map.py)'}")

    try:
        info = export_dataset(
            ratios=ratios,
            name=args.name,
            pin_test=args.pin_test,
            exclude=args.exclude,
            copy=args.copy,
            dry_run=args.dry_run,
            make_archive=True,
        )
    except Exception as e:
        print(f"Export error: {e}", file=sys.stderr)
        sys.exit(1)

    print()
    print(f"dataset {info['name']}{'  (DRY RUN - nothing written)' if args.dry_run else ''}")
    for split in ("train", "val", "test"):
        i = info["splits"][split]
        print(f"  {split:<6} {i['lectures']:>2} lectures  {i['clips']:>4} clips  {human(i['hours']*3600)}")
        for j in i["jobs"]:
            flag = "  [human-corrected]" if j["human_corrected"] else ""
            print(f"           {j['job_id']}  {(j['title'] or '')[:44]}{flag}")
    print(f"  total  {info['total_clips']} clips, {human(info['total_hours']*3600)}")

    if info.get("dropped_segments"):
        print("  dropped: " + ", ".join(f"{n} {reason}" for reason, n in info["dropped_segments"].items()))
    if info.get("unknown_english_words"):
        top = ", ".join(f"{w} ({n})" for w, n in list(info["unknown_english_words"].items())[:12])
        print(f"  English words with no Malayalam spelling yet: {len(info['unknown_english_words'])} -> {top}")
        print("  (these were left in English; run build_english_map.py, then export again)")
    print(f"  test references human-verified: {info['human_verified_test_references']}")

    if not info["usable_for_training"]:
        print()
        print("  *** NOT USABLE FOR TRAINING YET ***")
        empty = [s for s in ("train", "val", "test") if info["splits"][s]["clips"] == 0]
        print(f"  These splits are empty: {', '.join(empty)}.")
        print(f"  Splitting is by whole lecture, so at least 3 lectures are needed "
              f"(you have {len(training_jobs)}). Collect more lectures, then export again.")
    elif not args.dry_run:
        print(f"\n  written to {os.path.join(DATASETS_DIR, info['name'])}")


if __name__ == "__main__":
    main()
