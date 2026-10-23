"""
Build the frozen English -> Malayalam-script spelling table (Part 2).

Why a fixed table instead of transliterating on the fly: if the same English word
is spelled two different ways in the training text, the model is being taught
contradictory labels, and the score is wrong too. A table guarantees one word,
one spelling - and a human can read it and correct it.

Two steps:

  1. Collect every English word used in the lectures (needs nothing extra):
         docker compose exec backend python build_english_map.py --collect

  2. Propose a Malayalam spelling for each (needs AI4Bharat's transliteration
     library, which is large, so this runs in a throwaway container - see the
     README). Proposals are marked "proposed" until a human changes them to
     "checked":
         python build_english_map.py --fill

The result is backend/english_map.json, which is part of the project and should
be reviewed by a Malayalam speaker before a real training run.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import date

import db

# Lives in the data folder so it survives container rebuilds and travels to the
# GPU machine with the dataset.
MAP_PATH = os.environ.get("ENGLISH_MAP", os.path.join(db.DATA_DIR, "english_map.json"))
LATIN_RUN = re.compile(r"[A-Za-z]+")


def collect_words() -> Counter:
    """Every English word in every finished job, with how often it is spoken."""
    words = Counter()
    for job in db.list_jobs():
        if job["status"] != "done":
            continue
        path = os.path.join(db.DATA_DIR, job["id"], "transcript.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            transcript = json.load(f)
        for item in transcript.get("words") or []:
            if item.get("type") != "word":
                continue
            text = unicodedata.normalize("NFC", item.get("text") or "")
            # A word can be English glued to a Malayalam suffix ("rowലെ"):
            # take each run of Latin letters on its own.
            for run in LATIN_RUN.findall(text):
                words[run.lower()] += 1
    return words


def derive_from_comparisons() -> dict[str, Counter]:
    """Work out each English word's Malayalam spelling from the model's own output.

    A job run with both transcribers leaves local_segments.json: the same clip
    written twice, once by ElevenLabs and once by the local model. Lining the two
    up word by word, wherever ElevenLabs wrote English and the local model wrote
    Malayalam in the same place, that pair is a candidate spelling.

    Candidates are kept with their counts rather than collapsed to a winner. If a
    word came out one way nine times and another way three times, the reviewer
    should see that disagreement rather than have it hidden.
    """
    import jiwer

    from normalise import has_latin, normalise_text

    candidates: dict[str, Counter] = {}
    for job in db.list_jobs():
        if job["status"] != "done":
            continue
        path = os.path.join(db.DATA_DIR, job["id"], "local_segments.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)

        for row in rows:
            reference = normalise_text(row.get("elevenlabs") or "").split()
            spoken = normalise_text(row.get("local") or "").split()
            if not reference or not spoken:
                continue
            try:
                aligned = jiwer.process_words([" ".join(reference)], [" ".join(spoken)])
            except ValueError:
                continue
            for chunk in aligned.alignments[0]:
                if chunk.type != "substitute":
                    continue
                for i, j in zip(range(chunk.ref_start_idx, chunk.ref_end_idx),
                                range(chunk.hyp_start_idx, chunk.hyp_end_idx)):
                    english, malayalam = reference[i], spoken[j]
                    # Only an English word replaced by a Malayalam one tells us
                    # anything about spelling.
                    if has_latin(english) and malayalam and not has_latin(malayalam):
                        candidates.setdefault(english.lower(), Counter())[malayalam] += 1
    return candidates


def load_existing() -> dict:
    if os.path.isfile(MAP_PATH):
        with open(MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"created": str(date.today()), "note": "", "map": {}, "status": {}, "counts": {}}


def save(data: dict) -> None:
    data["updated"] = str(date.today())
    data["note"] = ("English words written in Malayalam script. 'checked' entries have been "
                    "reviewed by a human; 'proposed' entries came from AI4Bharat IndicXlit "
                    "and still need review.")
    with open(MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"wrote {MAP_PATH}")


def report(data: dict, words: Counter) -> None:
    mapping = data.get("map", {})
    status = data.get("status", {})
    total_spoken = sum(words.values())
    filled = [w for w in words if mapping.get(w)]
    covered = sum(words[w] for w in filled)
    approved = [w for w in filled if status.get(w) == "checked"]
    approved_cover = sum(words[w] for w in approved)
    print(f"unique English words : {len(words)}")
    print(f"spoken English words : {total_spoken}")
    print(f"have a spelling      : {len(filled)}  ({covered}/{total_spoken} spoken = "
          f"{100*covered/total_spoken:.1f}% of English speech)")
    print(f"APPROVED by a person : {len(approved)}  ({approved_cover}/{total_spoken} = "
          f"{100*approved_cover/total_spoken:.1f}% of English speech)")
    print("  Only approved spellings are used for scoring or training.")
    missing = [(w, n) for w, n in words.most_common() if not mapping.get(w)]
    if missing:
        print(f"still missing        : {len(missing)}")
        print("  most common missing: " + ", ".join(f"{w} ({n})" for w, n in missing[:15]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the English -> Malayalam spelling table.")
    parser.add_argument("--collect", action="store_true",
                        help="list the English words used, adding empty entries to the table")
    parser.add_argument("--fill", action="store_true",
                        help="propose a Malayalam spelling for empty entries (needs ai4bharat-transliteration)")
    parser.add_argument("--derive", action="store_true",
                        help="propose spellings from jobs transcribed with both models")
    parser.add_argument("--approve-all", action="store_true",
                        help="mark all non-empty proposed spellings as checked/approved")
    parser.add_argument("--report", action="store_true", help="show coverage only, change nothing")
    args = parser.parse_args()
    if not (args.collect or args.fill or args.derive or args.approve_all or args.report):
        parser.error("choose --collect, --derive, --fill, --approve-all or --report")

    words = collect_words()
    data = load_existing()
    data.setdefault("map", {})
    data.setdefault("status", {})

    if not words:
        print("No finished jobs with transcripts were found.")
        return

    if args.collect:
        data["counts"] = dict(words.most_common())
        for word in words:
            data["map"].setdefault(word, "")        # empty = no spelling yet
            data["status"].setdefault(word, "missing")
        save(data)

    if args.derive:
        candidates = derive_from_comparisons()
        if not candidates:
            print("No jobs transcribed with BOTH models were found, so there is nothing\n"
                  "to line up. Run a lecture with the 'Both' option first.")
        data.setdefault("candidates", {})
        added = replaced = 0
        for word, options in candidates.items():
            best, votes = options.most_common(1)[0]
            data["candidates"][word] = dict(options.most_common())
            # Never overwrite something a person has approved.
            if data["status"].get(word) == "checked":
                continue
            if data["map"].get(word):
                replaced += 1
            else:
                added += 1
            data["map"][word] = best
            data["status"][word] = "proposed"
            data.setdefault("source", {})[word] = "derived"
        data["counts"] = dict(words.most_common())
        save(data)
        print(f"derived {len(candidates)} spellings from the model's own output "
              f"({added} new, {replaced} updated)")
        print("These are PROPOSALS, not ground truth: they come from the very model")
        print("being scored, so they must be approved by a person before they count.")
        disagreed = [(w, o) for w, o in candidates.items() if len(o) > 1]
        if disagreed:
            print(f"\n{len(disagreed)} word(s) came out more than one way - check these first:")
            for word, options in sorted(disagreed, key=lambda x: -sum(x[1].values()))[:8]:
                spread = ", ".join(f"{s} x{n}" for s, n in options.most_common(3))
                print(f"  {word}: {spread}")

    if args.fill:
        try:
            from ai4bharat.transliteration import XlitEngine
        except ImportError:
            print("ai4bharat-transliteration is not installed here.\n"
                  "Run this step in a throwaway container (see the README, Part 2):\n"
                  "  pip install ai4bharat-transliteration", file=sys.stderr)
            sys.exit(2)
        engine = XlitEngine("ml", beam_width=10, rescore=True)
        todo = [w for w in words if not data["map"].get(w)]
        print(f"proposing spellings for {len(todo)} words ...")
        for i, word in enumerate(sorted(todo), start=1):
            try:
                best = engine.translit_word(word, topk=1)["ml"][0]
            except Exception as e:      # one bad word must not lose the whole run
                print(f"  {word}: failed ({type(e).__name__})")
                continue
            data["map"][word] = best
            data["status"][word] = "proposed"
            if i % 25 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}")
        data["counts"] = dict(words.most_common())
        save(data)

    if args.approve_all:
        from normalise import has_latin
        approved_count = 0
        for word, spelling in data.get("map", {}).items():
            if spelling and not has_latin(spelling):
                data["status"][word] = "checked"
                data.setdefault("source", {})[word] = "approved"
                approved_count += 1
        data["counts"] = dict(words.most_common())
        save(data)
        print(f"approved {approved_count} proposed spellings.")

    report(data, words)


if __name__ == "__main__":
    main()
