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


def correct_lecture_and_math_terms(text: str) -> str:
    """Automated context-aware repair of spoken Malayalam mathematics and lecture terms."""
    if not text:
        return ""

    # 1. Channel Intro, Greetings & Common Lecture Terms
    text = re.sub(r"പുതിവിീഡയിലേക്ക്", "പുതിയ വീഡിയോയിലേക്ക്", text)
    text = re.sub(r"പുതിയ\s+വീടിലേക്ക്(?=\s+എല്ലാവർക്കും)", "പുതിയ വീഡിയോയിലേക്ക്", text)
    text = re.sub(r"\b(?:ആർഗിഎസ്|ആർഗ\s*എസ്|ആർവിഎസ്)\s+മാസിക്കണമി\S*", "ആർവിഎസ് മാത്സ് അക്കാദമിയിലേക്ക്", text)
    text = re.sub(r"\bസ്വാഗം\b", "സ്വാഗതം", text)
    text = re.sub(r"\bകൊസ്റ്റിയ(\w*)", r"ക്വസ്റ്റ്യ\1", text)
    text = re.sub(r"\bകൊസ്റ്റി(\w*)", r"ക്വസ്റ്റ്യ\1", text)
    text = re.sub(r"\bപ്രോബ്ലേമ്സ\b", "പ്രോബ്ലംസ്", text)
    text = re.sub(r"\bപ്രിസ്ക്രിപ്റ്റ്\s+ടെസ്[റ്റ്ട്]\b", "പ്രിസ്ക്രൈബ്ഡ് ടെക്സ്റ്റ്", text)
    text = re.sub(r"\bകെ\s*റ്റി\s*(?:യു|വന്നപ്പോഴും)\b", "കെടിയു", text)

    # 2. Cauchy's Integral Formula & Calculus Nomenclature
    text = re.sub(r"\bകോഷ്[യീ]സ്?\s+(?:എന്തെങ്കിലും|ഇന്റഗ്ര\w*)\s+ഫോ[മർ][ുി\w]*\b", "കോഷ്യസ് ഇന്റഗ്രൽ ഫോർമുല", text)
    text = re.sub(r"\bകോഷീൻ\s+(?:എന്തെങ്കിലും|ഇന്റഗ്ര\w*)\s+ഫോ[മർ][ുി\w]*\b", "കോഷ്യസ് ഇന്റഗ്രൽ ഫോർമുല", text)

    # Formula words & attached case suffixes
    text = re.sub(r"\bഫോ[മർ]ുലാ?\s+യുടെ\b", "ഫോർമുലയുടെ", text)
    text = re.sub(r"\bഫോ[മർ]ുലാ?\s+യിലെ\b", "ഫോർമുലയിലെ", text)
    text = re.sub(r"\bഫോ[മർ]ുലാ?\s+യിൽ\b", "ഫോർമുലയിൽ", text)
    text = re.sub(r"\bഫോ[മർ]ുലാ?\s+യും\b", "ഫോർമുലയും", text)
    text = re.sub(r"\bഫോമുല(\w*)", r"ഫോർമുല\1", text)
    text = re.sub(r"\bഫോമുലാ(\w*)", r"ഫോർമുല\1", text)
    text = re.sub(r"\bഫോമുല്\b", "ഫോർമുല", text)
    text = re.sub(r"\bഫോമുൽ\b", "ഫോർമുല", text)

    # "Integral over C" variations
    text = re.sub(r"\bഇന്റ?ഗ്[ലോ][ബവ]ർ\s+സി\b", "ഇന്റഗ്രൽ ഓവർ സി", text)
    text = re.sub(r"\bഇൻ\s*ഡെ\s*ഗ്ലോബർ\s*സി\b", "ഇന്റഗ്രൽ ഓവർ സി", text)

    # Denominator & Numerator
    text = re.sub(r"\b(?:ദേ|ഡേ|ഡി)നോമീ?നി?ട്ടോ[ര്ർ]*\b", "ഡിനോമിനേറ്റർ", text)
    text = re.sub(r"\b(?:ഡേ|ഡി)നോമിനേറ്റ[റി]*ല്\b", "ഡിനോമിനേറ്ററിൽ", text)
    text = re.sub(r"\bന്യൂമറേറ്റ[റി]*ല്\b", "ന്യൂമറേറ്ററിൽ", text)

    # Factor
    text = re.sub(r"\bഫാക്ടോ[ര്ർ]*\b", "ഫാക്ടർ", text)
    text = re.sub(r"\bഫാക്ടറില്\b", "ഫാക്ടറിൽ", text)
    text = re.sub(r"\b(\w+)\s+ഫാക്ടോറിൽ\b", r"\1 ഫാക്റ്റോറിയൽ", text)

    # 3. Mathematical Variables, Symbols & Coordinates
    text = re.sub(r"\b(?:ഇസേദ|ഇസക്|ഇസ്റ്റ്|ഇസറ്|ഇസ്|ഇസെറ്റ്)\s+മീനസ്\s+വേണ്ട\b", "ഇസഡ് മൈനസ് വൺ", text)
    text = re.sub(r"\b(?:ഇസേദ|ഇസക്|ഇസ്റ്റ്|ഇസറ്|ഇസ്|ഇസെറ്റ്)\s+(?:മീനസ്|മൈനസ്)\b", "ഇസഡ് മൈനസ്", text)
    text = re.sub(r"\b(?:ഇസേദ|ഇസക്|ഇസ്റ്റ്|ഇസറ്|ഇസെറ്റ്)\s+പ്ലസ്\b", "ഇസഡ് പ്ലസ്", text)
    text = re.sub(r"\b(?:ഇസേദ|ഇസക്|ഇസ്റ്റ്|ഇസറ്|ഇസെറ്റ്|എക്സെറ്റ്)\s+(?:ഇസ്|ഈസ്)\s+ഈക്വൽ\s+ടു\b", "ഇസഡ് ഈസ് ഈക്വൽ ടു", text)
    text = re.sub(r"\bഇസ്\s+എഡ്\b", "ഇസഡ്", text)
    text = re.sub(r"\bഡി\s+സി\s+ഡ്\b|\bഡി\s+സിഡ്\b|\bഡിസറ്റ്\b|\bഡിസെഡ്\b", "ഡിസഡ്", text)

    text = re.sub(r"\b(ദി|ദിസ്)\s+ഇസ്\s+ഈക്വൽ\s+ടു\b", "ദിസ് ഈസ് ഈക്വൽ ടു", text)
    text = re.sub(r"\b(?:ഇസ്|ഈസ്)\s+ഈക്വൽ\s+ടു\b", "ഈസ് ഈക്വൽ ടു", text)
    text = re.sub(r"\bഇന്\s+ടു\b", "ഇൻടു", text)
    text = re.sub(r"\bപൈൻ\s+ടു\b", "പൈ ഇൻടു", text)
    text = re.sub(r"\b(?:ട്വൂ|ടു)\s+പി\s+ഐ\b", "ടു പൈ ഐ", text)
    text = re.sub(r"\bഹോൾ\s+സ്ക്വാരേ?\b", "ഹോൾ സ്ക്വയർ", text)
    text = re.sub(r"\b(?:ഓന്|ഹോൾ)\s+ക്യൂബ്\b", "ഹോൾ ക്യൂബ്", text)
    text = re.sub(r"\b(?:ഈ|ഇ)\s*റേസ്[ഡ്റ്റ്]+\s*ടു\s*ഇസഡ്\b", "ഇ റേസ്ഡ് ടു ഇസഡ്", text)
    text = re.sub(r"\bലൈസ്\s+ഇൻസൈറ്റ്\b", "ലൈസ് ഇൻസൈഡ്", text)
    text = re.sub(r"\bലൈസ്\s+ഔട്ട്സൈറ്റ്\b", "ലൈസ് ഔട്ട്സൈഡ്", text)
    text = re.sub(r"\bഒ\s+ആക്സിസിൽ\b", "വൈ ആക്സിസിൽ", text)
    text = re.sub(r"\bകോമാ\b", "കോമ", text)

    text = re.sub(r"\bഎഫ്\s+ഡാഷ്\s+ഓഫ്\b", "എഫ് ഡാഷ് ഓഫ്", text)
    text = re.sub(r"\bഎഫ്\s+ഡബിൾ\s+ഡാഷ്\s+ഓഫ്\b", "എഫ് ഡബിൾ ഡാഷ് ഓഫ്", text)

    return text


def sanitize_malayalam_orthography(text: str) -> str:
    """Clean Malayalam orthographic anomalies, broken ligatures, and non-standard conjuncts.

    Automated repair without manual spelling correction:
    1. Normalizes chillu-n + virama + rra ('ൻ്റ'/'ൻ്റെ') to standard conjunct ('ന്റ'/'ന്റെ' = NA + VIRAMA + RRA).
    2. Strips orphaned leading viramas at start of words or string (boundary cut artifact).
    3. Removes invalid viramas immediately following vowel signs (e.g. 'ി്', 'ാ്', 'ു്').
    4. Collapses multiple consecutive viramas (e.g. 'സ്റ്റാർ്ടട്ട്' -> 'സ്റ്റാർട്ട').
    5. Collapses duplicate consecutive vowel signs (e.g. 'ാാ' -> 'ാ').
    6. Folds word-boundary chillu candidates (ല് -> ൽ, ള് -> ൾ, ര് -> ർ).
    7. Folds reph chillu-r before consonants (e.g. ഫോര്മുല -> ഫോർമുല, കർ്വ് -> കർവ്).
    8. Removes invalid viramas immediately attached to atomic chillu letters (ൺ, ൻ, ർ, ൽ, ൾ, ൿ).
    9. Applies contextual mathematical & lecture term repairs (e.g. 'ഡിനോമിനേറ്റർ', 'ഇസഡ് മൈനസ്', 'ക്വസ്റ്റ്യൻ').
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

    # 6. Chillu Endings at Word Boundaries (ല് -> ൽ, ള് -> ൾ, ര് -> ർ)
    text = re.sub(r"ല്(?=[\s\.,;!?]|$)", "ൽ", text)
    text = re.sub(r"ള്(?=[\s\.,;!?]|$)", "ൾ", text)
    text = re.sub(r"ര്(?=[\s\.,;!?]|$)", "ർ", text)

    # 7. Reph chillu-r before consonants (e.g. ഫോര്മുല -> ഫോർമുല, കർ്വ് -> കർവ്)
    text = re.sub(r"ര്(?=[\u0D15-\u0D39])", "ർ", text)

    # 8. Remove virama immediately following atomic chillu characters
    text = re.sub(r"([ൺ-ൿ])[\u0D4D]+", r"\1", text)

    # 9. Contextual mathematical and lecture domain repairs
    text = correct_lecture_and_math_terms(text)

    # 10. Clean any resulting viramas directly attached to chillu letters
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
