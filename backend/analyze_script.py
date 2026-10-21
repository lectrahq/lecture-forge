"""
Script analysis report (Part 2, step 1).

Looks at every finished job's transcript and reports how much of the speech was
written in Malayalam script, how much in Latin script (English words), and which
English words appear most often. The numbers decide how English words should be
handled when IndicConformer is fine-tuned.

Run it on the VPS:
    docker compose exec backend python analyze_script.py
    docker compose exec backend python analyze_script.py --json   (machine-readable)

This script only reads; it changes nothing.
"""

import argparse
import json
import os
import sys
import unicodedata
from collections import Counter

import db

# Unicode facts we need. The Malayalam block is U+0D00-U+0D7F.
MALAYALAM = range(0x0D00, 0x0D80)
ATOMIC_CHILLU = "ൺൻർൽൾൿ"   # ൺ ൻ ർ ൽ ൾ ൿ
VIRAMA = "്"
ZWJ = "‍"
ZWNJ = "‌"

# Spoken numbers, counted separately: these are usually better written as digits
# than transliterated into Malayalam script.
NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "hundred", "thousand", "first", "second", "third", "fourth", "fifth",
}

# Punctuation and quote marks stripped before a word is classified.
STRIP = " \t\n .,!?;:…\"'“”‘’()[]{}<>/\\|@#$%^&*_+=~`।॥-–—"


def classify(word: str) -> str:
    """Which script is this word written in?"""
    has_mal = any(ord(c) in MALAYALAM for c in word)
    has_latin = any(("a" <= c <= "z") or ("A" <= c <= "Z") for c in word)
    has_digit = any(c.isdigit() for c in word)
    if has_mal and has_latin:
        return "mixed"
    if has_mal:
        return "malayalam"
    if has_latin:
        return "latin"
    if has_digit:
        return "digit"
    return "other"


def words_of(transcript: dict) -> list[str]:
    """The spoken words of a Scribe response, punctuation stripped."""
    out = []
    for item in transcript.get("words") or []:
        if item.get("type") != "word":
            continue
        text = unicodedata.normalize("NFC", (item.get("text") or "")).strip(STRIP)
        if text:
            out.append(text)
    return out


def count_chillu_forms(text: str) -> tuple[int, int]:
    """How many chillu letters are written atomically vs the old way (consonant + virama + ZWJ)."""
    atomic = sum(text.count(c) for c in ATOMIC_CHILLU)
    old_style = text.count(VIRAMA + ZWJ)
    return atomic, old_style


def analyse() -> dict:
    jobs = [j for j in db.list_jobs() if j["status"] == "done"]
    report = {"jobs": [], "totals": {}, "top_latin": [], "generated_for": len(jobs)}

    all_counts = Counter()
    latin_words = Counter()
    mixed_words = Counter()
    total_seconds = 0.0
    total_segments = 0
    segments_with_latin = 0
    mal_char_lengths = []
    atomic_total = old_total = 0

    for job in jobs:
        folder = os.path.join(db.DATA_DIR, job["id"])
        transcript_path = os.path.join(folder, "transcript.json")
        if not os.path.isfile(transcript_path):
            continue
        with open(transcript_path, encoding="utf-8") as f:
            transcript = json.load(f)

        words = words_of(transcript)
        counts = Counter(classify(w) for w in words)
        all_counts.update(counts)
        for w in words:
            kind = classify(w)
            if kind == "latin":
                latin_words[w.lower()] += 1
            elif kind == "mixed":
                mixed_words[w] += 1
            elif kind == "malayalam":
                mal_char_lengths.append(len(w))

        a, o = count_chillu_forms(transcript.get("text") or "")
        atomic_total += a
        old_total += o

        # Segment-level view: how many training clips contain at least one English word.
        segments_path = os.path.join(folder, "segments.json")
        job_segments = job_with_latin = 0
        if os.path.isfile(segments_path):
            with open(segments_path, encoding="utf-8") as f:
                segments = json.load(f)
            job_segments = len(segments)
            for seg in segments:
                seg_words = [w.strip(STRIP) for w in (seg.get("text") or "").split()]
                if any(classify(w) in ("latin", "mixed") for w in seg_words if w):
                    job_with_latin += 1
        total_segments += job_segments
        segments_with_latin += job_with_latin
        total_seconds += job["duration"] or 0

        total_words = len(words)
        report["jobs"].append({
            "job_id": job["id"],
            "title": job["title"],
            "minutes": round((job["duration"] or 0) / 60, 1),
            "segments": job_segments,
            "words": total_words,
            "malayalam_pct": pct(counts["malayalam"], total_words),
            "latin_pct": pct(counts["latin"], total_words),
            "mixed_pct": pct(counts["mixed"], total_words),
            "segments_with_english_pct": pct(job_with_latin, job_segments),
        })

    total_words = sum(all_counts.values())
    report["totals"] = {
        "jobs": len(report["jobs"]),
        "hours": round(total_seconds / 3600, 2),
        "segments": total_segments,
        "words": total_words,
        "malayalam": all_counts["malayalam"],
        "latin": all_counts["latin"],
        "mixed": all_counts["mixed"],
        "digit": all_counts["digit"],
        "other": all_counts["other"],
        "malayalam_pct": pct(all_counts["malayalam"], total_words),
        "latin_pct": pct(all_counts["latin"], total_words),
        "mixed_pct": pct(all_counts["mixed"], total_words),
        "unique_latin_words": len(latin_words),
        "segments_with_english": segments_with_latin,
        "segments_with_english_pct": pct(segments_with_latin, total_segments),
        "avg_malayalam_word_chars": round(sum(mal_char_lengths) / len(mal_char_lengths), 1) if mal_char_lengths else 0,
        "chillu_atomic": atomic_total,
        "chillu_old_style": old_total,
    }
    report["top_latin"] = latin_words.most_common(50)
    report["mixed_examples"] = mixed_words.most_common(15)

    # Extra numbers that decide how English should be handled when training.
    latin_total = sum(latin_words.values())
    top50 = sum(n for _, n in latin_words.most_common(50))
    single_letters = sum(n for w, n in latin_words.items() if len(w) == 1)
    numbers = sum(n for w, n in latin_words.items() if w in NUMBER_WORDS)
    said_once = sum(1 for n in latin_words.values() if n == 1)
    report["english_detail"] = {
        "top50_coverage_pct": pct(top50, latin_total),
        "single_letter_tokens": single_letters,
        "single_letter_pct": pct(single_letters, latin_total),
        "number_word_tokens": numbers,
        "number_word_pct": pct(numbers, latin_total),
        "words_said_once": said_once,
        "words_said_once_pct": pct(said_once, len(latin_words)),
    }
    return report


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def print_report(r: dict) -> None:
    t = r["totals"]
    line = "-" * 78
    print(line)
    print("SCRIPT ANALYSIS REPORT")
    print(line)
    if not t.get("words"):
        print("No finished jobs with transcripts found. Transcribe some lectures first.")
        return

    print(f"Finished jobs : {t['jobs']}")
    print(f"Audio         : {t['hours']} hours ({t['segments']} training clips)")
    print(f"Words         : {t['words']:,}")
    print()
    print("Script of each spoken word")
    print(f"  Malayalam script : {t['malayalam']:>7,}  {t['malayalam_pct']:>5}%")
    print(f"  Latin script     : {t['latin']:>7,}  {t['latin_pct']:>5}%   (English words)")
    print(f"  Mixed in one word: {t['mixed']:>7,}  {t['mixed_pct']:>5}%")
    print(f"  Digits           : {t['digit']:>7,}")
    print(f"  Other            : {t['other']:>7,}")
    print()
    print(f"Unique English words            : {t['unique_latin_words']}")
    print(f"Clips containing >=1 English word: {t['segments_with_english']} of {t['segments']}"
          f"  ({t['segments_with_english_pct']}%)")
    print(f"Average Malayalam word length    : {t['avg_malayalam_word_chars']} characters")
    print()
    print("Chillu letter spelling (matters for text normalisation)")
    print(f"  atomic form (ൻ etc.)          : {t['chillu_atomic']:,}")
    print(f"  old style (virama + ZWJ)       : {t['chillu_old_style']:,}")
    print()
    print("Per job")
    print(f"  {'minutes':>7} {'clips':>6} {'words':>7} {'mal%':>6} {'eng%':>6} {'mixed%':>7}  title")
    for j in r["jobs"]:
        print(f"  {j['minutes']:>7} {j['segments']:>6} {j['words']:>7,} {j['malayalam_pct']:>6}"
              f" {j['latin_pct']:>6} {j['mixed_pct']:>7}  {(j['title'] or '')[:40]}")
    e = r["english_detail"]
    print("Make-up of the English words")
    print(f"  top 50 words cover            : {e['top50_coverage_pct']}% of all English words")
    print(f"  single letters (a, b, i ...)  : {e['single_letter_tokens']:,}  ({e['single_letter_pct']}%)")
    print(f"  spoken numbers (two, three...): {e['number_word_tokens']:,}  ({e['number_word_pct']}%)")
    print(f"  words said only once          : {e['words_said_once']}  ({e['words_said_once_pct']}% of unique)")
    print()
    print("50 most common English (Latin-script) words")
    for i in range(0, len(r["top_latin"]), 5):
        row = r["top_latin"][i:i + 5]
        print("  " + "".join(f"{w} ({n})".ljust(22) for w, n in row))
    if r["mixed_examples"]:
        print()
        print("Examples of words mixing both scripts")
        print("  " + "   ".join(f"{w} ({n})" for w, n in r["mixed_examples"]))
    print(line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Report how much English appears in the collected transcripts.")
    parser.add_argument("--json", action="store_true", help="print the raw numbers as JSON")
    args = parser.parse_args()

    report = analyse()
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        print_report(report)
