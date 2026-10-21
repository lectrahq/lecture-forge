"""
Unit tests for normalise.py.

Run on the VPS:
    docker compose exec backend python -m unittest test_normalise -v
"""

import unittest

import json
import os
import tempfile

from normalise import (fold_chillu, has_latin, load_english_map, normalise_text,
                       prepare_reference, strip_punctuation, transliterate_english)

ZWJ = "‍"
VIRAMA = "്"


class TestChillu(unittest.TestCase):
    """Chillu letters must end up written one single way, or scoring counts
    identical-looking text as wrong."""

    def test_all_six_old_style_fold_to_atomic(self):
        pairs = [
            ("ണ", "ൺ"),  # ണ -> ൺ
            ("ന", "ൻ"),  # ന -> ൻ
            ("ര", "ർ"),  # ര -> ർ
            ("ല", "ൽ"),  # ല -> ൽ
            ("ള", "ൾ"),  # ള -> ൾ
            ("ക", "ൿ"),  # ക -> ൿ
        ]
        for consonant, atomic in pairs:
            old_style = consonant + VIRAMA + ZWJ
            self.assertEqual(fold_chillu(old_style), atomic,
                             f"{consonant!r} + virama + ZWJ should fold to {atomic!r}")

    def test_real_example_from_our_transcripts(self):
        # "ന്‍റെ" as it appeared in one ElevenLabs transcript (old-style chillu n).
        old = "ന" + VIRAMA + ZWJ + "റെ"
        self.assertEqual(normalise_text(old), "ൻറെ")

    def test_atomic_chillu_is_left_alone(self):
        text = "അവൻ"  # ends with atomic ൻ
        self.assertEqual(normalise_text(text), text)

    def test_both_spellings_become_identical(self):
        old = "അ" + "ന" + VIRAMA + ZWJ
        new = "അ" + "ൻ"
        self.assertEqual(normalise_text(old), normalise_text(new))

    def test_plain_virama_is_not_a_chillu(self):
        # ക് without a joiner is a dead consonant, not the chillu ൿ.
        text = "ക" + VIRAMA
        self.assertEqual(fold_chillu(text), text)
        self.assertIn(VIRAMA, normalise_text(text), "the virama must survive")

    def test_zwnj_after_virama_does_not_create_a_chillu(self):
        text = "ന" + VIRAMA + "‌"
        self.assertEqual(fold_chillu(text), text)
        self.assertEqual(normalise_text(text), "ന" + VIRAMA)


class TestNormalisation(unittest.TestCase):

    def test_nfc_composes_vowel_signs(self):
        # ka + vowel sign e + vowel sign aa  ==  ka + vowel sign o
        decomposed = "കൊ"
        composed = "കൊ"
        self.assertEqual(normalise_text(decomposed), normalise_text(composed))
        self.assertEqual(normalise_text(decomposed), composed)

    def test_zero_width_characters_removed(self):
        self.assertEqual(normalise_text("അ​വ﻿"), "അവ")

    def test_punctuation_removed(self):
        self.assertEqual(normalise_text("hello, world!"), "hello world")
        self.assertEqual(normalise_text("അവ."), "അവ")

    def test_punctuation_becomes_a_space_not_a_join(self):
        # "a,b" must not turn into the single word "ab".
        self.assertEqual(normalise_text("a,b"), "a b")

    def test_whitespace_collapsed_and_trimmed(self):
        self.assertEqual(normalise_text("  a \t\n b  "), "a b")

    def test_latin_lowercased(self):
        self.assertEqual(normalise_text("Matrix TRANSPOSE"), "matrix transpose")

    def test_digits_survive(self):
        self.assertEqual(normalise_text("row 3"), "row 3")

    def test_empty_and_blank(self):
        self.assertEqual(normalise_text(""), "")
        self.assertEqual(normalise_text("   "), "")
        self.assertEqual(normalise_text(None or ""), "")

    def test_idempotent(self):
        samples = [
            "Hello, World!",
            "ന" + VIRAMA + ZWJ + "റെ",
            "  അ​വ , matrix  ",
            "",
        ]
        for s in samples:
            once = normalise_text(s)
            self.assertEqual(normalise_text(once), once, f"not idempotent for {s!r}")

    def test_virama_is_not_treated_as_punctuation(self):
        self.assertIn(VIRAMA, strip_punctuation("ക" + VIRAMA))


class TestEnglishTransliteration(unittest.TestCase):

    MAP = {"matrix": "മാട്രിക്സ",
           "row": "റോ"}

    def test_known_word_replaced(self):
        out, unknown = transliterate_english("matrix", self.MAP)
        self.assertEqual(out, self.MAP["matrix"])
        self.assertEqual(unknown, [])

    def test_case_insensitive_lookup(self):
        out, _ = transliterate_english("Matrix", self.MAP)
        self.assertEqual(out, self.MAP["matrix"])

    def test_unknown_word_kept_and_reported(self):
        out, unknown = transliterate_english("eigenvalue", self.MAP)
        self.assertEqual(out, "eigenvalue", "unknown words must not be silently dropped")
        self.assertEqual(unknown, ["eigenvalue"])

    def test_blank_spelling_is_treated_as_missing(self):
        # build_english_map.py --collect writes empty values for words that still
        # need a spelling. A blank must never replace the word, or the English
        # would be silently deleted from the training text.
        out, unknown = transliterate_english("matrix", {"matrix": ""})
        self.assertEqual(out, "matrix")
        self.assertEqual(unknown, ["matrix"])

    def test_no_word_is_ever_lost(self):
        for mapping in ({}, {"matrix": ""}, {"matrix": "മ"}):
            out, _ = transliterate_english("alpha matrix beta", mapping)
            self.assertEqual(len(out.split()), 3, f"a word disappeared with {mapping!r}")

    def test_malayalam_text_untouched(self):
        text = "അവൻ"
        out, unknown = transliterate_english(text, self.MAP)
        self.assertEqual(out, text)
        self.assertEqual(unknown, [])

    def test_mixed_word_keeps_malayalam_suffix(self):
        # "rowലെ" -> Malayalam-script "row" + the original ലെ suffix
        out, unknown = transliterate_english("rowലെ", self.MAP)
        self.assertEqual(out, self.MAP["row"] + "ലെ")
        self.assertEqual(unknown, [])

    def test_prepare_reference_has_no_latin_left(self):
        text, unknown = prepare_reference("The Matrix row.", {**self.MAP, "the": "ദ"})
        self.assertEqual(unknown, [])
        self.assertFalse(has_latin(text), f"Latin script left in reference: {text!r}")

    def test_prepare_reference_normalises_too(self):
        text, _ = prepare_reference("  Matrix,  matrix ", self.MAP)
        self.assertEqual(text, self.MAP["matrix"] + " " + self.MAP["matrix"])


class TestEnglishMapFile(unittest.TestCase):

    def test_blank_entries_are_not_loaded(self):
        # All three are approved, so only the blankness decides. A blank spelling
        # must never load, or the word would be replaced by nothing and lost.
        data = {"map": {"matrix": "മാ", "row": "", "column": None},
                "status": {"matrix": "checked", "row": "checked", "column": "checked"}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            mapping = load_english_map(path)
            self.assertEqual(mapping, {"matrix": "മാ"})
            self.assertNotIn("row", mapping)
            self.assertNotIn("column", mapping)
        finally:
            os.unlink(path)

    def test_unapproved_entries_are_not_loaded(self):
        # The safety rule: spellings derived from the local model are proposals
        # until a person approves them, and must not be used before that.
        data = {"map": {"matrix": "മാ"}, "status": {"matrix": "proposed"}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            self.assertEqual(load_english_map(path), {})
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
