"""
Unit tests for the English spelling table.

The rule these exist to protect: a spelling derived from the local model is a
proposal, not ground truth. Scoring the model against its own output would let it
mark its own homework, and training on it would teach it its own mistakes. Only a
spelling a person has approved may be used.
"""

import json
import os
import tempfile
import unittest

from normalise import load_english_map, map_coverage

MALAYALAM = "മാട്രിക്സ"   # മാട്രിക്സ്
OTHER = "പോർഷൻ"                                # പോർഷൻ


def write_table(**kwargs) -> str:
    data = {"map": {}, "status": {}, "counts": {}, **kwargs}
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, handle)
    handle.close()
    return handle.name


class TestOnlyApprovedCounts(unittest.TestCase):

    def test_proposed_spellings_are_not_used(self):
        path = write_table(map={"matrix": MALAYALAM}, status={"matrix": "proposed"})
        try:
            self.assertEqual(load_english_map(path), {},
                             "a proposal must never be used for scoring or training")
        finally:
            os.unlink(path)

    def test_approved_spellings_are_used(self):
        path = write_table(map={"matrix": MALAYALAM}, status={"matrix": "checked"})
        try:
            self.assertEqual(load_english_map(path), {"matrix": MALAYALAM})
        finally:
            os.unlink(path)

    def test_proposals_can_be_read_when_explicitly_asked_for(self):
        # The review screen needs to see them; nothing else does.
        path = write_table(map={"matrix": MALAYALAM}, status={"matrix": "proposed"})
        try:
            self.assertEqual(load_english_map(path, only_checked=False),
                             {"matrix": MALAYALAM})
        finally:
            os.unlink(path)

    def test_missing_status_is_not_approved(self):
        # An entry with no status recorded must be treated as unapproved.
        path = write_table(map={"matrix": MALAYALAM}, status={})
        try:
            self.assertEqual(load_english_map(path), {})
        finally:
            os.unlink(path)

    def test_blank_spelling_never_loads(self):
        # Replacing a word with nothing would silently delete it from the text.
        path = write_table(map={"matrix": ""}, status={"matrix": "checked"})
        try:
            self.assertEqual(load_english_map(path), {})
        finally:
            os.unlink(path)


class TestCoverage(unittest.TestCase):
    """Coverage counts words actually spoken, not table entries: a few common words
    matter far more than many rare ones."""

    def test_counts_spoken_words_not_entries(self):
        path = write_table(
            map={"matrix": MALAYALAM, "obscure": OTHER},
            status={"matrix": "checked", "obscure": "proposed"})
        try:
            # 'matrix' is said 200 times, 'obscure' twice.
            coverage = map_coverage(path, {"matrix": 200, "obscure": 2})
            self.assertEqual(coverage["occurrences_total"], 202)
            self.assertEqual(coverage["occurrences_covered"], 200)
            self.assertGreater(coverage["coverage_percent"], 98,
                               "approving one common word should cover most speech")
            self.assertEqual(coverage["words_approved"], 1)
        finally:
            os.unlink(path)

    def test_nothing_approved_is_zero_percent(self):
        path = write_table(map={"matrix": MALAYALAM}, status={"matrix": "proposed"})
        try:
            self.assertEqual(map_coverage(path, {"matrix": 200})["coverage_percent"], 0.0)
        finally:
            os.unlink(path)

    def test_empty_table_does_not_divide_by_zero(self):
        path = write_table()
        try:
            self.assertEqual(map_coverage(path, {})["coverage_percent"], 0.0)
        finally:
            os.unlink(path)

    def test_missing_table_is_not_an_error(self):
        # No table yet is the normal state before the first lecture is transcribed.
        # This returned HTTP 500 from the spelling-table endpoint until it was fixed.
        missing = "/tmp/definitely-not-here-english-map.json"
        self.assertFalse(os.path.exists(missing))
        self.assertEqual(load_english_map(missing), {})
        self.assertEqual(map_coverage(missing, {})["coverage_percent"], 0.0)
        self.assertEqual(map_coverage(missing, {"matrix": 5})["occurrences_covered"], 0)

    def test_corrupt_table_is_not_an_error(self):
        # Half-written file: the next poll should recover, not crash the page.
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8")
        handle.write('{"map": {"matrix":')
        handle.close()
        try:
            self.assertEqual(load_english_map(handle.name), {})
        finally:
            os.unlink(handle.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
