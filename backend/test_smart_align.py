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

if __name__ == "__main__":
    unittest.main()
