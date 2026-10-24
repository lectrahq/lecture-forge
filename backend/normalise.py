"""
Text normalisation shared by dataset export (Part 2) and scoring.

Why this exists: WER and CER compare two strings character by character, so the
same words written two different ways count as errors even when the recognition
was right. Malayalam has several of these traps - above all the chillu letters,
which can be written either as one modern character or as the old three-character
sequence. Both look identical on screen.

The SAME normalise_text() must be applied to the reference and to the model's
prediction before any score is computed.

No third-party libraries: the GPU scripts import this file too.
"""

import json
import os
import re
import unicodedata

# --- Malayalam specifics -------------------------------------------------

# Chillu letters are consonants spoken without a vowel at the end of a word.
# They can be written two ways; these look the same but are different characters:
#   old style : consonant + virama (U+0D4D) + zero-width joiner (U+200D)
#   modern    : one atomic character (U+0D7A-U+0D7F)
# We fold everything to the modern atomic form, because that is what the
# IndicConformer vocabulary and the ElevenLabs transcripts both use.
VIRAMA = "്"
ZWJ = "‍"
CHILLU_FOLD = {
    "ണ" + VIRAMA + ZWJ: "ൺ",   # ണ് + ZWJ -> ൺ
    "ന" + VIRAMA + ZWJ: "ൻ",   # ന് + ZWJ -> ൻ
    "ര" + VIRAMA + ZWJ: "ർ",   # ര് + ZWJ -> ർ
    "ല" + VIRAMA + ZWJ: "ൽ",   # ല് + ZWJ -> ൽ
    "ള" + VIRAMA + ZWJ: "ൾ",   # ള് + ZWJ -> ൾ
    "ക" + VIRAMA + ZWJ: "ൿ",   # ക് + ZWJ -> ൿ
}
# Note: only ZWJ makes a chillu. A zero-width NON-joiner (U+200C) after the
# virama means "show the virama", which is a different letter, so it is left alone
# here and simply dropped with the other invisible characters below.

# Invisible characters that change nothing about the words themselves.
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))

_WHITESPACE = re.compile(r"\s+")


def fold_chillu(text: str) -> str:
    """Write every chillu letter the modern way."""
    for old, modern in CHILLU_FOLD.items():
        text = text.replace(old, modern)
    return text


def strip_invisible(text: str) -> str:
    """Drop zero-width and other invisible characters."""
    return text.translate(ZERO_WIDTH)


def strip_punctuation(text: str) -> str:
    """Drop punctuation, keeping letters, digits, marks and spaces.

    Uses the Unicode category so Malayalam punctuation and the danda are caught
    too. Vowel signs and the virama are marks (category M), so they survive.
    """
    return "".join(" " if unicodedata.category(c).startswith("P") else c for c in text)


def sanitize_malayalam_orthography(text: str) -> str:
    """Clean Malayalam orthographic anomalies, broken ligatures, and non-standard conjuncts.

    Automated repair without manual spelling correction:
    1. Normalizes chillu-n + virama + rra ('ൻ്റ'/'ൻ്റെ') to standard conjunct ('ന്റ'/'ന്റെ' = NA + VIRAMA + RRA).
    2. Strips orphaned leading viramas at start of words or string (boundary cut artifact).
    3. Removes invalid viramas immediately following vowel signs (e.g. 'ി്', 'ാ്', 'ു്').
    4. Collapses multiple consecutive viramas (e.g. 'സ്റ്റാർ്ടട്ട്' -> 'സ്റ്റാർട്ട').
    5. Collapses duplicate consecutive vowel signs (e.g. 'ാാ' -> 'ാ').
    6. Removes invalid viramas immediately attached to atomic chillu letters (ൺ, ൻ, ർ, ൽ, ൾ, ൿ).
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    # 1. Normalize chillu-n conjuncts
    text = re.sub(r"\u0D7B[\u200C\u200D]?\u0D4D\u0D31", "\u0D28\u0D4D\u0D31", text)
    # 2. Strip leading orphaned viramas at start of string or after whitespace
    text = re.sub(r"(?:^|\s)[\u0D4D]+", " ", text)
    # 3. Strip viramas immediately following any vowel signs
    text = re.sub(r"([\u0D3E-\u0D4C\u0D57])[\u0D4D]+", r"\1", text)
    # 4. Collapse multiple consecutive viramas
    text = re.sub(r"[\u0D4D]{2,}", "\u0D4D", text)
    # 5. Collapse duplicate consecutive vowel signs
    text = re.sub(r"([\u0D3E-\u0D4C\u0D57])[\u0D3E-\u0D4C\u0D57]+", r"\1", text)
    # 6. Remove virama immediately following atomic chillu characters
    text = re.sub(r"([ൺ-ൿ])[\u0D4D]+", r"\1", text)
    return _WHITESPACE.sub(" ", text).strip()


def normalise_text(text: str) -> str:
    """The one normalisation applied to BOTH references and predictions."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = sanitize_malayalam_orthography(text)
    text = fold_chillu(text)          # before stripping ZWJ, which chillu needs
    text = strip_invisible(text)
    text = strip_punctuation(text)
    text = text.lower()               # only affects Latin; Malayalam has no case
    return _WHITESPACE.sub(" ", text).strip()


# --- English words -------------------------------------------------------

# Decision for this project (see PROJECT_DIARY and README, Part 2):
# the Malayalam IndicConformer vocabulary has 256 tokens and contains no Latin
# letters at all, so the model cannot write "matrix". English words in the
# reference text are therefore transliterated into Malayalam script using a
# fixed, hand-checked table, so that the same English word is always spelled the
# same way. The table is built once by build_english_map.py.

def _read_table(path: str) -> dict:
    """The spelling table, or an empty one.

    A missing table is the normal state before any lecture has been transcribed,
    so it must not be an error: it simply means nothing has a spelling yet.
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_english_map(path: str, only_checked: bool = True) -> dict[str, str]:
    """Read the frozen English -> Malayalam-script table.

    only_checked defaults to True on purpose. Proposed spellings are derived from
    the local model's own output, so using them to score that model would let it
    mark its own homework, and using them as training labels would teach it its own
    mistakes. Only spellings a person has approved count as ground truth.
    """
    data = _read_table(path)
    status = data.get("status", {})
    out = {}
    for word, spelling in data.get("map", {}).items():
        # An empty value is a word still waiting for a spelling. It must never be
        # loaded, or the word would be replaced by nothing and silently lost.
        if not spelling:
            continue
        if only_checked and status.get(word) != "checked":
            continue
        out[word.lower()] = spelling
    return out


def map_coverage(path: str, counts: dict[str, int]) -> dict:
    """How much of the English actually spoken has an approved spelling.

    Counting entries would mislead: in one lecture the fifty commonest words
    accounted for 64% of all English used, so "38 of 319 approved" sounds far worse
    than it is. What matters is the share of spoken words that can be scored.
    """
    data = _read_table(path)
    status = data.get("status", {})
    mapping = data.get("map", {})
    total = sum(counts.values()) or 0
    covered = sum(n for word, n in counts.items()
                  if mapping.get(word) and status.get(word) == "checked")
    approved = sum(1 for w, s in mapping.items() if s and status.get(w) == "checked")
    return {
        "words_total": len(mapping),
        "words_approved": approved,
        "occurrences_total": total,
        "occurrences_covered": covered,
        "coverage_percent": round(100 * covered / total, 1) if total else 0.0,
    }


def has_latin(text: str) -> bool:
    return any(("a" <= c <= "z") or ("A" <= c <= "Z") for c in text)


def transliterate_english(text: str, mapping: dict[str, str]) -> tuple[str, list[str]]:
    """Rewrite English words in Malayalam script.

    Returns the rewritten text and the list of English words that were not in the
    table. Unknown words are left as they are, so nothing is silently lost; the
    export script reports them and they get added to the table.
    """
    unknown: list[str] = []
    out = []
    for word in text.split():
        if has_latin(word):
            # A word may be English+Malayalam glued together ("rowലെ"): transliterate
            # the Latin run and keep the Malayalam suffix as it is.
            def swap(match: re.Match) -> str:
                latin = match.group(0)
                replacement = mapping.get(latin.lower())
                if not replacement:      # missing, or present but still blank
                    unknown.append(latin.lower())
                    return latin
                return replacement
            word = re.sub(r"[A-Za-z]+", swap, word)
        out.append(word)
    return " ".join(out), unknown


def prepare_reference(text: str, mapping: dict[str, str]) -> tuple[str, list[str]]:
    """Reference text ready for training/scoring: English transliterated, then normalised."""
    text, unknown = transliterate_english(text, mapping)
    return normalise_text(text), unknown
