"""
WER & CER Visual Alignment, Token Diffing, and OOV Error Analytics.

Computes exact sequence alignments (Wagner-Fischer DP) between reference (ElevenLabs transliterated)
and hypothesis (Local IndicConformer), classifying each word into:
  - match (C)
  - substitution (S)
  - deletion (D)
  - insertion (I)
Also calculates character edit distance (Levenshtein) and aggregates Out-of-Vocabulary (OOV)
and technical loanword error metrics.
"""

import json
import logging
import os
import unicodedata
from normalise import prepare_reference, normalise_text

log = logging.getLogger("wer_visualizer")


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


def align_tokens_dp(ref_text: str, hyp_text: str, loanword_set: set[str] | None = None) -> dict:
    """
    Wagner-Fischer Dynamic Programming sequence alignment at token level.
    Returns aligned token pairs with match/substitution/deletion/insertion tags.
    """
    ref_words = ref_text.strip().split()
    hyp_words = hyp_text.strip().split()
    n = len(ref_words)
    m = len(hyp_words)

    if loanword_set is None:
        loanword_set = set()

    # dp[i][j] = min edit cost
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    ops = [[None] * (m + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        dp[i][0] = i
        ops[i][0] = 'del'
    for j in range(1, m + 1):
        dp[0][j] = j
        ops[0][j] = 'ins'

    for i in range(1, n + 1):
        r_w = ref_words[i - 1]
        for j in range(1, m + 1):
            h_w = hyp_words[j - 1]
            if r_w == h_w:
                dp[i][j] = dp[i - 1][j - 1]
                ops[i][j] = 'match'
            else:
                cost_sub = dp[i - 1][j - 1] + 1
                cost_del = dp[i - 1][j] + 1
                cost_ins = dp[i][j - 1] + 1

                min_cost = min(cost_sub, cost_del, cost_ins)
                dp[i][j] = min_cost
                if min_cost == cost_sub:
                    ops[i][j] = 'sub'
                elif min_cost == cost_del:
                    ops[i][j] = 'del'
                else:
                    ops[i][j] = 'ins'

    # Backtrack
    i, j = n, m
    aligned = []
    matches = substitutions = deletions = insertions = 0

    while i > 0 or j > 0:
        op = ops[i][j]
        if op == 'match':
            r_w = ref_words[i - 1]
            h_w = hyp_words[j - 1]
            is_loan = (r_w.lower() in loanword_set) or is_latin_word(r_w)
            aligned.append({
                "type": "match",
                "ref": r_w,
                "hyp": h_w,
                "is_loanword": is_loan,
                "char_dist": 0,
            })
            matches += 1
            i -= 1
            j -= 1
        elif op == 'sub':
            r_w = ref_words[i - 1]
            h_w = hyp_words[j - 1]
            c_dist = levenshtein_distance(r_w, h_w)
            is_loan = (r_w.lower() in loanword_set) or is_latin_word(r_w)
            aligned.append({
                "type": "substitution",
                "ref": r_w,
                "hyp": h_w,
                "is_loanword": is_loan,
                "char_dist": c_dist,
            })
            substitutions += 1
            i -= 1
            j -= 1
        elif op == 'del':
            r_w = ref_words[i - 1]
            is_loan = (r_w.lower() in loanword_set) or is_latin_word(r_w)
            aligned.append({
                "type": "deletion",
                "ref": r_w,
                "hyp": None,
                "is_loanword": is_loan,
                "char_dist": len(r_w),
            })
            deletions += 1
            i -= 1
        elif op == 'ins':
            h_w = hyp_words[j - 1]
            aligned.append({
                "type": "insertion",
                "ref": None,
                "hyp": h_w,
                "is_loanword": is_latin_word(h_w),
                "char_dist": len(h_w),
            })
            insertions += 1
            j -= 1
        else:
            break

    aligned.reverse()
    wer = round((substitutions + deletions + insertions) / max(1, n), 4)

    return {
        "aligned": aligned,
        "counts": {
            "matches": matches,
            "substitutions": substitutions,
            "deletions": deletions,
            "insertions": insertions,
            "total_ref": n,
            "wer": wer,
        }
    }


def compute_comparison_analytics(segments: list[dict], mapping: dict | None = None) -> dict:
    """
    Run full WER/CER alignment across all segments and build:
    1. Heatmap Ribbon (timeline bars with WER severity)
    2. Category Error Breakdown (Malayalam vs Loanword WER)
    3. Out-of-Vocabulary & Technical Misrecognition Heatmap
    """
    if mapping is None:
        mapping = {}

    loanword_set = set(k.lower() for k in mapping.keys()) | set(v.lower() for v in mapping.values())

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

        ref_clean, _ = prepare_reference(raw_ref, mapping)
        hyp_clean = normalise_text(raw_hyp)

        seg_res = align_tokens_dp(ref_clean, hyp_clean, loanword_set=loanword_set)
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
                if is_loan or len(ref_w) > 4:
                    ref_key = ref_w.lower()
                    if ref_key not in oov_tracker:
                        oov_tracker[ref_key] = {
                            "word": ref_w,
                            "is_loanword": is_loan,
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

    # Build sorted OOV Error Heatmap list
    oov_heatmap = []
    for info in oov_tracker.values():
        if info["errors"] > 0:
            err_rate = round(info["errors"] / max(1, info["occurrences"]), 3)
            # Sort confusions by frequency
            top_conf = sorted(info["confusions"].items(), key=lambda x: x[1], reverse=True)[:3]
            oov_heatmap.append({
                "word": info["word"],
                "is_loanword": info["is_loanword"],
                "occurrences": info["occurrences"],
                "errors": info["errors"],
                "error_rate": err_rate,
                "top_confusions": [k for k, _ in top_conf],
            })

    # Sort by error count descending
    oov_heatmap.sort(key=lambda x: (x["errors"], x["error_rate"]), reverse=True)

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
                if data.get("mapping_count") == len(mapping):
                    return data
        except Exception as e:
            log.warning("Could not load cached wer_analytics from %s: %s", cache_path, e)

    analytics = compute_comparison_analytics(segments, mapping)
    analytics["mapping_count"] = len(mapping)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(analytics, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.warning("Could not save wer_analytics to %s: %s", cache_path, e)

    return analytics

