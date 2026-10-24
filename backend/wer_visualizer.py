"""
WER & CER Visual Alignment, Token Diffing, and OOV Error Analytics.

Computes exact sequence alignments (Multi-Token Wagner-Fischer DP) between reference
(ElevenLabs transliterated/phonetic) and hypothesis (Local IndicConformer), classifying each word into:
  - match (C)
  - substitution (S)
  - deletion (D)
  - insertion (I)

Specially engineered for code-switched Malayalam-English (Manglish) lecture speech:
1. Acronym Expansion: Letter-by-letter phonetic expansion (RVS -> ആർവിഎസ്, S3 -> എസ്റ്റീ/എസ്ത്രീ, MAT -> എംഎടി/മാറ്റ്).
2. Compound Agglutination: Multi-token DP binds English loanwords + Malayalam case suffixes into a single unit
   (e.g. "Academy യുടെ" ↔ "അക്കാഡമിയുടെ", "module ന്റെ" ↔ "മോഡ്യൂളിന്റെ", "S3 കാരുടെ" ↔ "എസ്റ്റീക്കാരുടെ").
3. Common Lecture Loanword Lexicon: Built-in phonetic dictionary for standard college lecture terminology
   (maths -> മാത്സ്, complex -> കോംപ്ലക്സ്, variable -> വേരിയബിൾ, integration -> ഇന്റഗ്രേഷൻ, textbook -> ടെക്സ്റ്റ് ബുക്ക്).
4. Accurate Out-of-Vocabulary (OOV) Error Attribution: True acoustic errors (e.g. Maths -> മാസ്) are tracked
   precisely without cascading false deletions onto surrounding words.
"""

import json
import logging
import os
import re
import unicodedata
from normalise import normalise_text

log = logging.getLogger("wer_visualizer")

# --- Malayalam Case Suffixes & Postpositions ---
MALAYALAM_CASE_SUFFIXES = {
    "യുടെ", "ന്റെ", "ൻ്റെ", "ഇന്റെ",
    "ൽ", "ിൽ", "യിൽ",
    "ക്ക്", "യ്ക്ക്", "ിക്ക്", "ന്", "ിന്",
    "ഉം", "വും",
    "ഓട്", "യോട്", "ഇനോട്",
    "ആൽ", "ഇനാൽ",
    "ലേക്ക്", "ഇലേക്ക്", "ത്തിലേക്ക്",
    "നകത്ത്", "ൽനകത്ത്", "ിൽനകത്ത്",
    "നിന്നു", "ൽനിന്ന്", "ിൽനിന്ന്", "നിന്നും", "ൽനിന്നും", "ിൽനിന്നും",
    "ആണ്", "ആയിരുന്നു", "ആയിരുന്നെങ്കിൽ", "എന്നാണ്",
    "കാരുടെ", "കാര്", "കാരം",
    "കൾ", "കളെ", "കളിൽ", "കൾക്ക്", "കളിലേക്ക്",
    "തന്നെ", "തന്നേ"
}

# --- Acronym English Letters & Digits to Malayalam Syllables ---
ACRONYM_MAP = {
    "A": "എ", "B": "ബി", "C": "സി", "D": "ഡി", "E": "ഇ", "F": "എഫ്",
    "G": "ജി", "H": "എച്ച്", "I": "ഐ", "J": "ജെ", "K": "കെ", "L": "എൽ",
    "M": "എം", "N": "എൻ", "O": "ഒ", "P": "പി", "Q": "ക്യു", "R": "ആർ",
    "S": "എസ്", "T": "ടി", "U": "യു", "V": "വി", "W": "ഡബ്ല്യു", "X": "എക്സ്",
    "Y": "വൈ", "Z": "ഇസഡ്",
    "0": "സീറോ", "1": "വൺ", "2": "ടു", "3": "ത്രീ", "4": "ഫോർ",
    "5": "ഫൈവ്", "6": "സിക്സ്", "7": "സെവൻ", "8": "എയിറ്റ്", "9": "നയൻ"
}

# --- Built-in Phonetic Dictionary for High-Frequency Academic Loanwords ---
COMMON_LOANWORDS = {
    "maths": ["മാത്സ്", "മാത്‍സ്", "മാത്തമാറ്റിക്സ്"],
    "mathematics": ["മാത്തമാറ്റിക്സ്", "മാത്തമറ്റിക്സ്", "മാത്സ്"],
    "academy": ["അക്കാദമി", "അക്കാഡമി"],
    "module": ["മോഡ്യൂൾ", "മൊഡ്യൂൾ", "മോഡൽ"],
    "start": ["സ്റ്റാർട്ട്"],
    "fourth": ["ഫോർത്ത്"],
    "third": ["തേർഡ്", "തിർഡ്"],
    "first": ["ഫസ്റ്റ്"],
    "second": ["സെക്കൻഡ്", "സെക്കന്റ്"],
    "complex": ["കോംപ്ലക്സ്"],
    "variable": ["വേരിയബിൾ"],
    "integration": ["ഇന്റഗ്രേഷൻ"],
    "differentiation": ["ഡിഫറൻഷ്യേഷൻ", "ഡിഫ്ഫേരേന്സേഷന്"],
    "syllabus": ["സിലബസ്"],
    "topic": ["ടോപ്പിക്", "ടോപിക്"],
    "textbook": ["ടെക്സ്റ്റ്ബുക്ക്", "ടെക്സ്റ്റ് ബുക്ക്"],
    "edition": ["എഡിഷൻ", "അഡിഷൻ"],
    "publication": ["പബ്ലിക്കേഷൻ"],
    "matrix": ["മാട്രിക്സ്"],
    "integral": ["ഇന്റഗ്രൽ"],
    "formula": ["ഫോർമുല"],
    "follow": ["ഫോളോ"],
    "advanced": ["അഡ്വാൻസ്ഡ്", "അഡ്വാൻസ്"],
    "engineering": ["എൻജിനീയറിങ്", "എഞ്ചിനീയറിംഗ്", "എൻജിനീയറിംഗ്"],
    "four": ["ഫോർ", "ഫോറാണ്"],
    "three": ["ത്രീ"],
    "two": ["ടു"],
    "one": ["വൺ", "ഒന്ന്"],
    "zero": ["സീറോ", "പൂജ്യം"],
    "kreyszig": ["ക്രോസിക്ക്", "ക്രോസ്വിഗ്", "ക്രോയിസിഗ്", "ക്രേസിക്ക്", "ക്രേസിക്കിന്റെ"],
    "erwin": ["എർവിൻ"],
    "john": ["ജോൺ"],
    "wiley": ["വേലിയേൻ", "വൈലി"],
    "sons": ["സൺസ്", "സൺസിന്റെ"],
    "cauchy": ["കോഷി", "കോഷ്യസ്"],
    "okay": ["ഓക്കെ", "ഓകെ"],
    "ok": ["ഓക്കെ", "ഓകെ"],
    "yes": ["യെസ്"],
    "no": ["നോ"],
    "next": ["നെക്സ്റ്റ്"],
    "previous": ["പ്രീവിയസ്"],
    "check": ["ചെക്ക്"],
    "note": ["നോട്ട്"],
    "write": ["റൈറ്റ്"],
    "read": ["റീഡ്"],
    "table": ["ടേബിൾ"],
    "method": ["മെത്തേഡ്", "മെത്തഡ്"],
    "order": ["ഓർഡർ"],
    "degree": ["ഡിഗ്രി"],
    "system": ["സിസ്റ്റം"],
    "rank": ["റാങ്ക്"],
    "row": ["റോ"],
    "column": ["കോളം"],
    "element": ["എലമെന്റ്"],
    "diagonal": ["ഡയഗണൽ"],
    "determinant": ["ഡിറ്റർമിനന്റ്"],
    "inverse": ["ഇൻവേഴ്സ്"],
    "eigen": ["ഐഗൻ"],
    "function": ["ഫംഗ്ഷൻ", "ഫങ്ഷൻ"],
    "limit": ["ലിമിറ്റ്"],
    "series": ["സീരീസ്"],
    "value": ["വാല്യൂ"],
    "derivative": ["ഡെറിവേറ്റീവ്"],
    "power": ["പവർ"],
    "square": ["സ്ക്വയർ"],
    "cube": ["ക്യൂബ്"],
    "constant": ["കോൺസ്റ്റന്റ്"],
    "linear": ["ലീനിയർ"],
    "vector": ["വെക്ടർ"],
    "equation": ["ഇക്വേഷൻ", "ഇക്വേഷൻസ്"],
    "clear": ["ക്ലിയർ"],
    "simple": ["സിമ്പിൾ"],
    "same": ["സെയിം"],
    "level": ["ലെവൽ"],
    "line": ["ലൈൻ"],
    "point": ["പോയിന്റ്"],
    "graph": ["ഗ്രാഫ്"],
    "root": ["റൂട്ട്"],
    "circle": ["സർക്കിൾ"],
    "question": ["ക്വസ്റ്റ്യൻ"],
    "problem": ["പ്രോബ്ലം", "പ്രോബ്ലംസ്"],
    "solve": ["സോൾവ്"],
    "answer": ["ആൻസർ"],
    "class": ["ക്ലാസ്"],
    "step": ["സ്റ്റെപ്പ്"],
    "example": ["എക്സാമ്പിൾ"],
    "taylor": ["ടെയ്‌ലർ"],
    "laurent": ["ലോറന്റ്"],
    "singular": ["സിംഗുലർ"],
    "analytic": ["അനലിറ്റിക്"],
    "harmonic": ["ഹാർമോണിക്"],
    "transformation": ["ട്രാൻസ്ഫോർമേഷൻ"]
}


def levenshtein_distance(s1: str, s2: str) -> int:
    """Fast character-level Levenshtein distance."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    r1, r2 = list(range(len(s2) + 1)), [0] * (len(s2) + 1)
    for i, c1 in enumerate(s1):
        r2[0] = i + 1
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            r2[j + 1] = min(r1[j + 1] + 1, r2[j] + 1, r1[j] + cost)
        r1, r2 = r2, [0] * (len(s2) + 1)
    return r1[len(s2)]


def is_latin_word(word: str) -> bool:
    """Check if word contains Latin letters (English loanword)."""
    return any("LATIN" in unicodedata.name(c, "") for c in word)


def expand_acronym(word: str) -> list[str]:
    """Expands acronyms (RVS -> ആർവിഎസ്, S3 -> എസ്റ്റീ/എസ്ത്രീ, MAT -> എംഎടി/മാറ്റ്, KTU -> കെടിയു)."""
    w = word.strip().upper()
    if not w:
        return []
    if len(w) <= 6 and (w.isupper() or any(c.isdigit() for c in w)):
        chars = []
        for c in w:
            if c in ACRONYM_MAP:
                chars.append(ACRONYM_MAP[c])
            else:
                return []
        primary = "".join(chars)
        res = [primary]
        # Common colloquial acronym variants
        if w == "S3":
            res.extend(["എസ്റ്റീ", "എസ്ത്രീ", "എസ് ത്രീ"])
        elif w == "MAT":
            res.extend(["മാറ്റ്", "എം എ ടി", "എം എ"])
        elif w == "KTU":
            res.extend(["കെടിയു", "കെ ടി യു"])
        return res
    return []


def get_word_candidates(word: str, mapping: dict[str, str]) -> list[str]:
    """Return all valid Malayalam representations for a reference word."""
    w_clean = re.sub(r"[^A-Za-z0-9]", "", word).lower()
    cands = []

    # 1. User approved / dictionary mapping
    if w_clean in mapping and mapping[w_clean]:
        cands.append(normalise_text(mapping[w_clean]))

    # 2. Acronym expansion
    acros = expand_acronym(word)
    for a in acros:
        norm_a = normalise_text(a)
        if norm_a not in cands:
            cands.append(norm_a)

    # 3. Common lecture loanword dictionary
    if w_clean in COMMON_LOANWORDS:
        for cw in COMMON_LOANWORDS[w_clean]:
            norm_cw = normalise_text(cw)
            if norm_cw not in cands:
                cands.append(norm_cw)

    if not cands and not is_latin_word(word):
        cands.append(normalise_text(word))

    return cands


def get_morphological_variants(stem: str, suffix: str) -> list[str]:
    """Generate valid Malayalam compound forms when a stem attaches a case marker."""
    variants = [stem + suffix, stem + "്" + suffix]
    # Gemination for kaarude / kal (e.g. എസ്റ്റീ + കാരുടെ -> എസ്റ്റീക്കാരുടെ)
    if suffix.startswith("ക"):
        variants.append(stem + "്ക" + suffix[1:])
        variants.append(stem + "ക്ക" + suffix[1:])
        variants.append(stem + "ുക" + suffix[1:])
    # Virama / Vowel junction for inte / il / inu
    if suffix in ("ന്റെ", "ൻ്റെ", "ഇന്റെ"):
        variants.append(stem + "ിന്റെ")
        variants.append(stem + "ന്റെ")
        if stem.endswith("ൽ") or stem.endswith("ൾ") or stem.endswith("ർ") or stem.endswith("ൺ") or stem.endswith("ൻ"):
            variants.append(stem[:-1] + "ന്റെ")
    if suffix in ("ൽ", "ിൽ", "യിൽ"):
        variants.append(stem + "ിൽ")
        variants.append(stem + "യിൽ")
        variants.append(stem + "ൽ")
    if suffix in ("ന്", "ിന്"):
        variants.append(stem + "ിന്")
        variants.append(stem + "ത്തിന്")
    if suffix in ("ക്ക്", "യ്ക്ക്", "ിക്ക്"):
        variants.append(stem + "ിക്ക്")
        variants.append(stem + "യ്ക്ക്")
        variants.append(stem + "ക്ക്")
    if suffix in ("ഉം", "വും"):
        variants.append(stem + "ഉം")
        variants.append(stem + "വും")
        variants.append(stem + "ിലും")
    if suffix in ("ലേക്ക്", "ഇലേക്ക്"):
        variants.append(stem + "ലേക്ക്")
        variants.append(stem + "ിലേക്ക്")
        variants.append(stem + "ത്തിലേക്ക്")
    if suffix in ("ആണ്", "ആയിരുന്നു", "ആയിരുന്നെങ്കിൽ"):
        variants.append(stem + suffix)
        variants.append(stem + "റ" + suffix)
    return list(set(variants))


def match_score_1_to_1(ref_w: str, hyp_w: str, mapping: dict[str, str]) -> tuple[bool, int]:
    """Check if ref_w matches or phonetically approximates hyp_w."""
    if ref_w == hyp_w:
        return True, 0
    cands = get_word_candidates(ref_w, mapping)
    for c in cands:
        if c == hyp_w:
            return True, 0
        d = levenshtein_distance(c, hyp_w)
        if d <= 1 and min(len(c), len(hyp_w)) >= 4:
            # Very close phonetic variant (e.g. അക്കാദമി vs അക്കാഡമി)
            return True, 0

    min_dist = levenshtein_distance(ref_w, hyp_w)
    for c in cands:
        d = levenshtein_distance(c, hyp_w)
        if d < min_dist:
            min_dist = d
    return False, min_dist


def match_score_2_to_1(ref_w1: str, ref_w2: str, hyp_w: str, mapping: dict[str, str]) -> tuple[bool, int]:
    """Check if (ref_w1 + ref_w2) combines into hyp_w (e.g. Academy + യുടെ -> അക്കാഡമിയുടെ)."""
    cands1 = get_word_candidates(ref_w1, mapping)
    if not cands1:
        cands1 = [ref_w1]

    for c1 in cands1:
        variants = get_morphological_variants(c1, ref_w2)
        for v in variants:
            if v == hyp_w:
                return True, 0
            if levenshtein_distance(v, hyp_w) <= 1:
                return True, 0

        # Substring / boundary check: hyp_w must contain both stem and suffix
        if hyp_w.endswith(ref_w2):
            stem = hyp_w[:-len(ref_w2)]
            if stem == c1 or levenshtein_distance(stem, c1) <= 1:
                return True, 0

        if ref_w2 in MALAYALAM_CASE_SUFFIXES:
            if hyp_w.startswith(c1) or levenshtein_distance(c1, hyp_w[:len(c1)]) <= 1:
                if ref_w2 in hyp_w or levenshtein_distance(ref_w2, hyp_w[-len(ref_w2):]) <= 1:
                    return True, 0

    return False, 999


def match_score_1_to_2(ref_w: str, hyp_w1: str, hyp_w2: str, mapping: dict[str, str]) -> tuple[bool, int]:
    """Check if ref_w splits into hyp_w1 + hyp_w2 (e.g. textbook -> ടെക്സ്റ്റ് ബുക്ക്)."""
    comb_h = hyp_w1 + hyp_w2
    return match_score_1_to_1(ref_w, comb_h, mapping)


def align_sequence_smart(
    ref_tokens: list[str],
    hyp_tokens: list[str],
    mapping: dict[str, str] | None = None,
    loanword_set: set[str] | None = None
) -> dict:
    """
    Multi-token Wagner-Fischer Dynamic Programming sequence alignment supporting:
    - 1-to-1 match/substitution/deletion/insertion
    - 2-to-1 compound match (Ref i-2 & i-1 -> Hyp j-1)
    - 1-to-2 split match (Ref i-1 -> Hyp j-2 & j-1)
    """
    if mapping is None:
        mapping = {}
    if loanword_set is None:
        loanword_set = set()

    n = len(ref_tokens)
    m = len(hyp_tokens)

    # Precompute candidate sets and flags for reference tokens
    ref_clean = [normalise_text(w) for w in ref_tokens]
    ref_cands = [set(get_word_candidates(w, mapping)) | {ref_clean[idx]} for idx, w in enumerate(ref_tokens)]
    ref_is_latin = [is_latin_word(w) for w in ref_tokens]

    # Precompute 2-to-1 compound variants for adjacent ref tokens
    comp_21_variants: dict[int, set[str]] = {}
    for i in range(2, n + 1):
        r_w1 = ref_tokens[i - 2]
        r_w2 = ref_tokens[i - 1]
        cands1 = ref_cands[i - 2]
        variants = set()
        for c1 in cands1:
            for v in get_morphological_variants(c1, r_w2):
                variants.add(normalise_text(v))
            variants.add(normalise_text(c1 + r_w2))
        comp_21_variants[i] = variants

    # dp[i][j] = (min_cost, op_type, di, dj)
    dp = [[(999999, 'none', 0, 0) for _ in range(m + 1)] for _ in range(n + 1)]
    dp[0][0] = (0, 'start', 0, 0)

    for i in range(1, n + 1):
        dp[i][0] = (i, 'del', 1, 0)
    for j in range(1, m + 1):
        dp[0][j] = (j, 'ins', 0, 1)

    for i in range(1, n + 1):
        r_w = ref_tokens[i - 1]
        cand_set = ref_cands[i - 1]
        comp_set = comp_21_variants.get(i)

        for j in range(1, m + 1):
            h_w = hyp_tokens[j - 1]

            # Option 1: 1-to-1 match or sub (O(1) set lookup)
            is_match = (h_w in cand_set) or (r_w == h_w)
            cost_11 = 0 if is_match else 1
            op_11 = 'match' if is_match else 'sub'
            cand_11 = (dp[i - 1][j - 1][0] + cost_11, op_11, 1, 1)

            # Option 2: 1-to-0 del
            cand_del = (dp[i - 1][j][0] + 1, 'del', 1, 0)

            # Option 3: 0-to-1 ins
            cand_ins = (dp[i][j - 1][0] + 1, 'ins', 0, 1)

            best = cand_11
            if cand_del[0] < best[0]:
                best = cand_del
            if cand_ins[0] < best[0]:
                best = cand_ins

            # Option 4: 2-to-1 compound match (Ref i-2 & i-1 -> Hyp j-1)
            if i >= 2 and comp_set is not None:
                if h_w in comp_set:
                    cand_comp = (dp[i - 2][j - 1][0] + 0, 'compound_match_2_1', 2, 1)
                    if cand_comp[0] <= best[0]:
                        best = cand_comp
                else:
                    r_w2 = ref_tokens[i - 1]
                    if (r_w2 in MALAYALAM_CASE_SUFFIXES) and (ref_is_latin[i - 2] or len(r_w) >= 3):
                        cand_comp_sub = (dp[i - 2][j - 1][0] + 1, 'compound_sub_2_1', 2, 1)
                        if cand_comp_sub[0] < best[0]:
                            best = cand_comp_sub

            # Option 5: 1-to-2 compound match (Ref i-1 -> Hyp j-2 & j-1)
            if j >= 2:
                comb_h = hyp_tokens[j - 2] + h_w
                if (comb_h in cand_set) or (r_w == comb_h):
                    cand_12 = (dp[i - 1][j - 2][0] + 0, 'compound_match_1_2', 1, 2)
                    if cand_12[0] <= best[0]:
                        best = cand_12

            dp[i][j] = best

    # Backtrack
    i, j = n, m
    aligned = []
    matches = subs = dels = inss = 0

    while i > 0 or j > 0:
        cost, op, di, dj = dp[i][j]
        if di == 1 and dj == 1:
            r_w = ref_tokens[i - 1]
            h_w = hyp_tokens[j - 1]
            is_loan = (r_w.lower() in loanword_set) or is_latin_word(r_w)
            c_dist = levenshtein_distance(r_w, h_w)
            if op == 'match':
                aligned.append({
                    "type": "match",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": False,
                    "char_dist": 0,
                })
                matches += 1
            else:
                aligned.append({
                    "type": "substitution",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": False,
                    "char_dist": c_dist,
                })
                subs += 1
        elif di == 2 and dj == 1:
            r_w = f"{ref_tokens[i - 2]} {ref_tokens[i - 1]}"
            h_w = hyp_tokens[j - 1]
            is_loan = any((w.lower() in loanword_set) or is_latin_word(w) for w in [ref_tokens[i - 2], ref_tokens[i - 1]])
            if 'match' in op:
                aligned.append({
                    "type": "match",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": True,
                    "char_dist": 0,
                })
                matches += 1
            else:
                aligned.append({
                    "type": "substitution",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": True,
                    "char_dist": levenshtein_distance(r_w, h_w),
                })
                subs += 1
        elif di == 1 and dj == 2:
            r_w = ref_tokens[i - 1]
            h_w = f"{hyp_tokens[j - 2]} {hyp_tokens[j - 1]}"
            is_loan = (r_w.lower() in loanword_set) or is_latin_word(r_w)
            if 'match' in op:
                aligned.append({
                    "type": "match",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": True,
                    "char_dist": 0,
                })
                matches += 1
            else:
                aligned.append({
                    "type": "substitution",
                    "ref": r_w,
                    "hyp": h_w,
                    "is_loanword": is_loan,
                    "is_compound": True,
                    "char_dist": levenshtein_distance(r_w, h_w),
                })
                subs += 1
        elif di == 1 and dj == 0:
            r_w = ref_tokens[i - 1]
            is_loan = (r_w.lower() in loanword_set) or is_latin_word(r_w)
            aligned.append({
                "type": "deletion",
                "ref": r_w,
                "hyp": None,
                "is_loanword": is_loan,
                "is_compound": False,
                "char_dist": len(r_w),
            })
            dels += 1
        elif di == 0 and dj == 1:
            h_w = hyp_tokens[j - 1]
            aligned.append({
                "type": "insertion",
                "ref": None,
                "hyp": h_w,
                "is_loanword": is_latin_word(h_w),
                "is_compound": False,
                "char_dist": len(h_w),
            })
            inss += 1
        else:
            break
        i -= di
        j -= dj

    aligned.reverse()
    total_ref = n
    errors = subs + dels + inss
    wer = round(errors / max(1, total_ref), 4)

    return {
        "aligned": aligned,
        "counts": {
            "matches": matches,
            "substitutions": subs,
            "deletions": dels,
            "insertions": inss,
            "total_ref": total_ref,
            "errors": errors,
            "wer": wer,
        }
    }


def align_tokens_dp(ref_text: str, hyp_text: str, loanword_set: set[str] | None = None) -> dict:
    """Backwards compatibility wrapper for align_tokens_dp."""
    return align_sequence_smart(ref_text.strip().split(), hyp_text.strip().split(), loanword_set=loanword_set)


def compute_comparison_analytics(segments: list[dict], mapping: dict | None = None) -> dict:
    """
    Run full WER/CER alignment across all segments and build:
    1. Heatmap Ribbon (timeline bars with WER severity)
    2. Category Error Breakdown (Malayalam vs Loanword WER)
    3. Out-of-Vocabulary & Technical Misrecognition Heatmap
    """
    if mapping is None:
        mapping = {}

    loanword_set = (
        set(k.lower() for k in mapping.keys())
        | set(v.lower() for v in mapping.values())
        | set(COMMON_LOANWORDS.keys())
    )

    enriched_segments = []
    heatmap_ribbon = []
    oov_tracker = {}  # word -> {occurrences, errors, confusions: {hyp: count}}

    total_words = 0
    total_matches = 0
    total_subs = 0
    total_dels = 0
    total_ins = 0

    malayalam_words = 0
    malayalam_errors = 0

    loan_words = 0
    loan_errors = 0

    all_ref_chars = 0
    total_char_dist = 0

    for idx, s in enumerate(segments):
        raw_ref = s.get("elevenlabs", "")
        raw_hyp = s.get("local", "")

        # Clean raw text while preserving English loanwords for phonetic matching
        ref_tokens = [w for w in raw_ref.strip().split() if w]
        hyp_tokens = [normalise_text(w) for w in raw_hyp.strip().split() if normalise_text(w)]

        seg_res = align_sequence_smart(ref_tokens, hyp_tokens, mapping=mapping, loanword_set=loanword_set)
        aligned = seg_res["aligned"]
        counts = seg_res["counts"]

        # Track segment character error
        seg_ref_chars = sum(len(a["ref"]) for a in aligned if a["ref"])
        seg_char_dist = sum(a["char_dist"] for a in aligned)
        seg_cer = round(seg_char_dist / max(1, seg_ref_chars), 4) if seg_ref_chars else 0.0

        all_ref_chars += seg_ref_chars
        total_char_dist += seg_char_dist

        total_words += counts["total_ref"]
        total_matches += counts["matches"]
        total_subs += counts["substitutions"]
        total_dels += counts["deletions"]
        total_ins += counts["insertions"]

        # Track token level loanword vs native
        for a in aligned:
            ref_w = a.get("ref")
            hyp_w = a.get("hyp")
            is_loan = a.get("is_loanword", False)
            is_err = a["type"] in ("substitution", "deletion")

            if ref_w:
                if is_loan:
                    loan_words += 1
                    if is_err:
                        loan_errors += 1
                else:
                    malayalam_words += 1
                    if is_err:
                        malayalam_errors += 1

                # Track OOV & recurring technical errors
                # If compound, take the root word (e.g. "Academy യുടെ" -> "Academy")
                root_word = ref_w.split()[0] if " " in ref_w else ref_w
                root_is_loan = is_loan or is_latin_word(root_word) or (root_word.lower() in loanword_set)

                if root_is_loan or len(root_word) > 4:
                    ref_key = root_word.lower()
                    if ref_key not in oov_tracker:
                        oov_tracker[ref_key] = {
                            "word": root_word,
                            "is_loanword": root_is_loan,
                            "occurrences": 0,
                            "errors": 0,
                            "confusions": {},
                        }
                    oov_tracker[ref_key]["occurrences"] += 1
                    if is_err:
                        oov_tracker[ref_key]["errors"] += 1
                        if hyp_w:
                            c_dict = oov_tracker[ref_key]["confusions"]
                            c_dict[hyp_w] = c_dict.get(hyp_w, 0) + 1

        # Classify heatmap severity
        wer = counts["wer"]
        if wer == 0:
            severity = "match"  # Green
        elif wer <= 0.25:
            severity = "low"    # Light Green/Yellow
        elif wer <= 0.50:
            severity = "medium" # Amber/Orange
        else:
            severity = "high"   # Red

        start = s.get("start", 0.0)
        end = s.get("end", 0.0)

        heatmap_ribbon.append({
            "idx": idx,
            "start": start,
            "end": end,
            "wer": wer,
            "cer": seg_cer,
            "words": counts["total_ref"],
            "errors": counts["substitutions"] + counts["deletions"] + counts["insertions"],
            "severity": severity,
        })

        enriched_segments.append({
            **s,
            "scores": {
                "wer": wer,
                "cer": seg_cer,
            },
            "alignment": aligned,
            "alignment_counts": counts,
        })

    overall_wer = round((total_subs + total_dels + total_ins) / max(1, total_words), 4)
    overall_cer = round(total_char_dist / max(1, all_ref_chars), 4) if all_ref_chars else 0.0

    malayalam_wer = round(malayalam_errors / max(1, malayalam_words), 4) if malayalam_words else 0.0
    loanword_wer = round(loan_errors / max(1, loan_words), 4) if loan_words else 0.0

    # Build ranked OOV Heatmap entries
    oov_heatmap = []
    for k, v in oov_tracker.items():
        if v["occurrences"] >= 1:
            err_rate = round(v["errors"] / v["occurrences"], 3)
            # Sort top acoustic confusion hypotheses
            sorted_confusions = sorted(v["confusions"].items(), key=lambda x: x[1], reverse=True)
            oov_heatmap.append({
                "word": v["word"],
                "is_loanword": v["is_loanword"],
                "occurrences": v["occurrences"],
                "errors": v["errors"],
                "error_rate": err_rate,
                "top_confusions": [f"{w} ({c})" for w, c in sorted_confusions[:3]],
            })

    # Sort high error rate and frequency first
    oov_heatmap.sort(key=lambda x: (x["errors"], x["error_rate"], x["occurrences"]), reverse=True)

    return {
        "enriched_segments": enriched_segments,
        "heatmap_ribbon": heatmap_ribbon,
        "metrics": {
            "overall_wer": overall_wer,
            "overall_cer": overall_cer,
            "overall_agreement": round(max(0.0, 1.0 - overall_wer), 4),
            "malayalam_wer": malayalam_wer,
            "loanword_wer": loanword_wer,
            "total_words": total_words,
            "matches": total_matches,
            "substitutions": total_subs,
            "deletions": total_dels,
            "insertions": total_ins,
        },
        "oov_heatmap": oov_heatmap[:40],  # Top 40 high priority errors
    }


def get_or_create_wer_analytics(folder: str, segments: list[dict], mapping: dict | None = None, recompute: bool = False) -> dict:
    """Return cached analytics or compute, cache and return."""
    if mapping is None:
        mapping = {}
    cache_path = os.path.join(folder, "wer_analytics.json")
    if not recompute and os.path.isfile(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
                # Verify schema version
                if data.get("schema_version") == 2:
                    return data
        except Exception as e:
            log.warning("Could not load cached wer_analytics from %s: %s", cache_path, e)

    analytics = compute_comparison_analytics(segments, mapping)
    analytics["schema_version"] = 2
    analytics["mapping_count"] = len(mapping)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(analytics, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.warning("Could not save wer_analytics to %s: %s", cache_path, e)

    return analytics
