import unittest
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))
from wer_visualizer import align_sequence_smart, compute_comparison_analytics

class TestSmartWerAlignment(unittest.TestCase):
    def test_user_reported_intro_sentence(self):
        ref = "നമസ്കാരം RVS Maths Academy യുടെ പുതിയ വീഡിയോയിലേക്ക് എല്ലാവർക്കും സ്വാഗതം"
        hyp = "നമസ്കാരം ആർവിഎസ് മാസ് അക്കാഡമിയുടെ പുതിയ വീഡിയോയിലേക്ക് എല്ലാവർക്കും സ്വാഗതം"
        
        res = align_sequence_smart(ref.split(), hyp.split(), {})
        aligned = res["aligned"]
        counts = res["counts"]
        
        # 1. First word match
        self.assertEqual(aligned[0]["ref"], "നമസ്കാരം")
        self.assertEqual(aligned[0]["type"], "match")
        
        # 2. RVS acronym match to ആർവിഎസ്
        self.assertEqual(aligned[1]["ref"], "RVS")
        self.assertEqual(aligned[1]["hyp"], "ആർവിഎസ്")
        self.assertEqual(aligned[1]["type"], "match")
        
        # 3. Maths substituted to മാസ് (the single true error)
        self.assertEqual(aligned[2]["ref"], "Maths")
        self.assertEqual(aligned[2]["hyp"], "മാസ്")
        self.assertEqual(aligned[2]["type"], "substitution")
        
        # 4. Academy യുടെ compound match to അക്കാഡമിയുടെ
        self.assertEqual(aligned[3]["ref"], "Academy യുടെ")
        self.assertEqual(aligned[3]["hyp"], "അക്കാഡമിയുടെ")
        self.assertEqual(aligned[3]["type"], "match")
        
        # 5. Remaining words match
        for a in aligned[4:]:
            self.assertEqual(a["type"], "match")
            
        # Total errors must be EXACTLY 1 (Maths -> മാസ്)
        self.assertEqual(counts["substitutions"], 1)
        self.assertEqual(counts["deletions"], 0)
        self.assertEqual(counts["insertions"], 0)
        self.assertEqual(counts["errors"], 1)
        
    def test_oov_heatmap_records_maths_not_rvs(self):
        segs = [{
            "start": 5.0,
            "end": 12.0,
            "elevenlabs": "നമസ്കാരം RVS Maths Academy യുടെ പുതിയ വീഡിയോയിലേക്ക് എല്ലാവർക്കും സ്വാഗതം",
            "local": "നമസ്കാരം ആർവിഎസ് മാസ് അക്കാഡമിയുടെ പുതിയ വീഡിയോയിലേക്ക് എല്ലാവർക്കും സ്വാഗതം"
        }]
        analytics = compute_comparison_analytics(segs, {})
        oov = analytics["oov_heatmap"]
        
        # Maths should be in OOV with 1 occurrence, 1 error, and confusion മാസ്
        maths_entry = next((item for item in oov if item["word"].lower() == "maths"), None)
        self.assertIsNotNone(maths_entry)
        self.assertEqual(maths_entry["occurrences"], 1)
        self.assertEqual(maths_entry["errors"], 1)
        self.assertTrue(any("മാസ്" in c for c in maths_entry["top_confusions"]))
        
        # RVS should have 0 errors (should not appear as top error or has 0 errors)
        rvs_entry = next((item for item in oov if item["word"].lower() == "rvs"), None)
        if rvs_entry:
            self.assertEqual(rvs_entry["errors"], 0)

    def test_segment6_sandhi_compounds(self):
        # Tests seniors ന് -> സീനിയേഴ്സിന്, year ൽ -> ഇയറിൽ, paper ഉം -> പേപ്പറും, textbook ഉം -> ടെക്സ്റ്റ് ബുക്കും
        ref = "നിങ്ങളുടെ seniors ന് previous year ൽ ചോദിച്ചിട്ടുള്ള question paper ഉം textbook ഉം കൂടെ base ചെയ്ത് prepare ചെയ്തിരിക്കുന്നു"
        hyp = "നിങ്ങളുടെ സീനിയേഴ്സിന് പ്രീവിയസ് ഇയറിൽ ചോദിച്ചിട്ടുള്ള ക്വസ്റ്റ്യൻ പേപ്പറും ടെക്സ്റ്റ് ബുക്കും കൂടെ ബേസ് ചെയ്ത് പ്രിപ്പയർ ചെയ്തിരിക്കുന്നു"

        res = align_sequence_smart(ref.split(), hyp.split(), {})
        counts = res["counts"]

        # All words should match cleanly with 0 errors
        self.assertEqual(counts["errors"], 0)
        self.assertEqual(counts["substitutions"], 0)
        self.assertEqual(counts["deletions"], 0)
        self.assertEqual(counts["insertions"], 0)

    def test_hyphenated_loanwords(self):
        # Tests form-ൽ -> ഫോമിൽ, module-ൽ -> മോഡ്യൂളിൽ
        ref = "ഈ form-ൽ എഴുതുക"
        hyp = "ഈ ഫോമിൽ എഴുതുക"
        res = align_sequence_smart(ref.split(), hyp.split(), {})
        self.assertEqual(res["counts"]["errors"], 0)

    def test_colloquial_variants(self):
        # Spoken colloquial equivalents (പറഞ്ഞാ -> പറഞ്ഞാൽ, വരുമ്പം -> വരുമ്പോൾ)
        ref = "അങ്ങനെ പറഞ്ഞാൽ വരുമ്പോൾ"
        hyp = "അങ്ങനെ പറഞ്ഞാ വരുമ്പം"
        res = align_sequence_smart(ref.split(), hyp.split(), {})
        self.assertEqual(res["counts"]["errors"], 0)

    def test_math_symbols(self):
        # Tests Cauchy's -> കോഷീസ്, theta -> തീറ്റ, dz -> ഡിസെഡ്
        ref = "Cauchy's theorem theta dz"
        hyp = "കോഷീസ് തിയറം തീറ്റ ഡിസെഡ്"
        res = align_sequence_smart(ref.split(), hyp.split(), {})
        self.assertEqual(res["counts"]["errors"], 0)

    def test_number_compound(self):
        # Tests 201 -> ടു സീറോ ഒന്ന്
        ref = "code 201 ആണ്"
        hyp = "കോഡ് ടു സീറോ ഒന്ന് ആണ്"
        res = align_sequence_smart(ref.split(), hyp.split(), {})
        self.assertEqual(res["counts"]["errors"], 0)

    def test_bilingual_unlisted_loanwords(self):
        # Tests arbitrary unlisted technical terms that are NOT in COMMON_LOANWORDS
        ref = "thermodynamics resistor capacitor eigenvalue distribution chapter simplify choice reverse"
        hyp = "തെർമോഡൈനാമിക്സ് റെസിസ്റ്റർ കപ്പാസിറ്റർ ഐഗൻവാല്യൂ ഡിസ്ട്രിബ്യൂഷൻ ചാപ്റ്റർ സിംപ്ലിഫൈ ചോയ്സ് റിവേഴ്സ്"
        res = align_sequence_smart(ref.split(), hyp.split(), {})
        self.assertEqual(res["counts"]["errors"], 0)
        self.assertEqual(res["counts"]["matches"], len(ref.split()))

    def test_nakathu_and_past_tense_sandhi(self):
        # Tests Integration + നകത്ത് -> ഇന്റഗ്രേഷനകത്ത്, Differentiation + ആയിരുന്നെങ്കിൽ -> ഡിഫറൻഷ്യേഷനായിരുന്നെങ്കിൽ
        ref = "Integration നകത്ത് Differentiation ആയിരുന്നെങ്കിൽ resistor ൽ"
        hyp = "ഇന്റഗ്രേഷനകത്ത് ഡിഫറൻഷ്യേഷനായിരുന്നെങ്കിൽ റെസിസ്റ്ററിൽ"
        res = align_sequence_smart(ref.split(), hyp.split(), {})
        self.assertEqual(res["counts"]["errors"], 0)
        self.assertEqual(res["counts"]["matches"], 3)

    def test_negative_confusions_remain_errors(self):
        # Tests that acoustic confusions (Maths -> മാസ്, integral -> എന്തെങ്കിലും, pi -> ഫൈവ്)
        # are correctly flagged as errors and NOT falsely matched
        ref = "Maths integral pi"
        hyp = "മാസ് എന്തെങ്കിലും ഫൈവ്"
        res = align_sequence_smart(ref.split(), hyp.split(), {})
        self.assertEqual(res["counts"]["matches"], 0)
        self.assertEqual(res["counts"]["substitutions"], 3)

if __name__ == "__main__":
    unittest.main()

